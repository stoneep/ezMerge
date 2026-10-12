"""Parametric shapes: Geometry Nodes groups plus helpers to read/write their inputs.

Every shape sits on its object origin and grows along the local axes, so a
dimension can be dragged like a SketchUp push/pull and may go negative:

* ``CYLINDER`` (also used for polygons: few segments, flat shading):
  base center at the origin, Radius, Height along +Z, Segments, Smooth. With ``CenteredZ`` the origin is halfway
  up (the center of the whole shape).
* ``CONE``: like the cylinder (and with the same ``CenteredZ``) but with a separate TopRadius (0 = a pointed cone).
* ``BOX``: base *corner* at the origin, Width (+X), Depth (+Y), Height (+Z). With ``Centered`` the origin is the
  center of the base instead (the box spans -Width/2..Width/2 and -Depth/2..Depth/2 around it); with
  ``CenteredZ`` too it is the center of the whole box (Height/2 below and above it).
* ``SPHERE``: a UV sphere centered on the origin (Radius, Segments; the rings follow the segments).
* ``QUADSPHERE``: a sphere made of six curved grids (a cube pushed onto a sphere): all quads, no poles.
  Same parameters as the sphere; Segments counts the quads around a great circle (4 per grid edge).
"""

import math

import bpy
from mathutils import Matrix, Vector

KIND_CYLINDER = 'CYLINDER'
KIND_BOX = 'BOX'
KIND_CONE = 'CONE'
KIND_SPHERE = 'SPHERE'
KIND_QUADSPHERE = 'QUADSPHERE'
KINDS = (KIND_CYLINDER, KIND_BOX, KIND_CONE, KIND_SPHERE, KIND_QUADSPHERE)
SPHERE_KINDS = (KIND_SPHERE, KIND_QUADSPHERE)  # round in every direction: one radius, centered on the origin

KIND_KEY = "ezhs_kind"
VERSION_KEY = "ezhs_version"
GROUP_VERSION = {KIND_CYLINDER: 4, KIND_BOX: 4, KIND_CONE: 3, KIND_SPHERE: 1, KIND_QUADSPHERE: 1}
GROUP_NAME = {KIND_CYLINDER: "EZ Cylinder", KIND_BOX: "EZ Box", KIND_CONE: "EZ Cone", KIND_SPHERE: "EZ Sphere",
              KIND_QUADSPHERE: "EZ Quad Sphere"}
MODIFIER_NAME = GROUP_NAME

MIN_SEGMENTS = 3
MAX_SEGMENTS = 512
MAX_RINGS = 512

# name, socket type, default, min, max, description
_INPUTS = {
    KIND_CYLINDER: (
        ("Radius", 'NodeSocketFloat', 1.0, 0.0, 1e6, "Radius (circumradius for polygons)"),
        ("Height", 'NodeSocketFloat', 2.0, -1e6, 1e6, "Height along local +Z (negative grows downward)"),
        ("Segments", 'NodeSocketInt', 32, MIN_SEGMENTS, MAX_SEGMENTS, "Number of sides"),
        ("Smooth", 'NodeSocketBool', True, False, True, "Smooth shading of the sides"),
        ("CenteredZ", 'NodeSocketBool', False, False, True,
         "The origin is halfway up the shape (otherwise the center of its base)"),
        ("Rings", 'NodeSocketInt', 1, 1, MAX_RINGS,
         "Rows along the height: more rows let a Merge flare bend where it meets another mesh"),
    ),
    KIND_CONE: (
        ("Radius", 'NodeSocketFloat', 1.0, 0.0, 1e6, "Radius of the base"),
        ("TopRadius", 'NodeSocketFloat', 0.0, 0.0, 1e6, "Radius of the top (0 makes a pointed cone)"),
        ("Height", 'NodeSocketFloat', 2.0, -1e6, 1e6, "Height along local +Z (negative grows downward)"),
        ("Segments", 'NodeSocketInt', 32, MIN_SEGMENTS, MAX_SEGMENTS, "Number of sides"),
        ("Smooth", 'NodeSocketBool', True, False, True, "Smooth shading of the sides"),
        ("CenteredZ", 'NodeSocketBool', False, False, True,
         "The origin is halfway up the shape (otherwise the center of its base)"),
        ("Rings", 'NodeSocketInt', 1, 1, MAX_RINGS,
         "Rows along the height: more rows let a Merge flare bend where it meets another mesh"),
    ),
    KIND_SPHERE: (
        ("Radius", 'NodeSocketFloat', 1.0, 0.0, 1e6, "Radius of the sphere"),
        ("Segments", 'NodeSocketInt', 32, MIN_SEGMENTS, MAX_SEGMENTS,
         "Number of segments around the sphere (the rings are half as many)"),
        ("Smooth", 'NodeSocketBool', True, False, True, "Smooth shading"),
    ),
    KIND_BOX: (
        ("Width", 'NodeSocketFloat', 1.0, -1e6, 1e6, "Size along local +X (negative grows backwards)"),
        ("Depth", 'NodeSocketFloat', 1.0, -1e6, 1e6, "Size along local +Y (negative grows backwards)"),
        ("Height", 'NodeSocketFloat', 1.0, -1e6, 1e6, "Height along local +Z (negative grows downward)"),
        ("Centered", 'NodeSocketBool', False, False, True,
         "The origin is the center of the base (otherwise a corner of it)"),
        ("CenteredZ", 'NodeSocketBool', False, False, True,
         "The origin is also halfway up the box: the center of the whole box"),
        ("Rings", 'NodeSocketInt', 1, 1, MAX_RINGS,
         "Rows along the height: more rows let a Merge flare bend where it meets another mesh"),
    ),
}

_INPUTS[KIND_QUADSPHERE] = tuple(
    spec[:5] + ("Number of quads around a great circle (4 times the quads along an edge of a grid)",)
    if spec[0] == "Segments" else spec for spec in _INPUTS[KIND_SPHERE])

# Which parameter measures the extent along each local axis (the faces that can be pushed/pulled).
DIMENSIONS = {
    KIND_CYLINDER: {2: "Height"},
    KIND_CONE: {2: "Height"},
    KIND_BOX: {0: "Width", 1: "Depth", 2: "Height"},
    KIND_SPHERE: {},
    KIND_QUADSPHERE: {},
}


def param_names(kind):
    return tuple(spec[0] for spec in _INPUTS[kind])


def _socket(sockets, identifier):
    # Look sockets up by identifier: display names changed between versions
    # (e.g. Set Shade Smooth's "Geometry" input is labelled "Mesh" in 4.3+).
    for s in sockets:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


def _enabled(sockets, name):
    for s in sockets:
        if s.name == name and s.enabled:
            return s
    raise KeyError(name)


def _new_input(ng, name, socket_type, default, min_value, max_value, description):
    item = ng.interface.new_socket(
        name=name, in_out='INPUT', socket_type=socket_type, description=description)
    item.default_value = default
    if socket_type != 'NodeSocketBool':
        item.min_value = min_value
        item.max_value = max_value
    if socket_type == 'NodeSocketFloat':
        try:
            item.subtype = 'DISTANCE'
        except TypeError:
            pass
    return item


def _group_outputs(ng):
    """Declare the interface of ``ng`` and return {input name: group-input socket identifier}."""
    kind = ng[KIND_KEY]
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    for spec in _INPUTS[kind]:
        _new_input(ng, *spec)
    return {name: ng.interface.items_tree[name].identifier for name in param_names(kind)}


def _store_uv(nodes):
    uv = nodes.new('GeometryNodeStoreNamedAttribute')
    uv.data_type = 'FLOAT2'
    uv.domain = 'CORNER'
    _socket(uv.inputs, "Name").default_value = "UVMap"
    return uv


def _build_cylinder(ng):
    ids = _group_outputs(ng)
    nodes, links = ng.nodes, ng.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')

    cyl = nodes.new('GeometryNodeMeshCylinder')
    cyl.fill_type = 'NGON'
    _socket(cyl.inputs, "Side Segments").default_value = 1
    _socket(cyl.inputs, "Fill Segments").default_value = 1

    depth = nodes.new('ShaderNodeMath')
    depth.operation = 'ABSOLUTE'
    half = nodes.new('ShaderNodeMath')
    half.operation = 'MULTIPLY'
    half.inputs[1].default_value = 0.5
    offset = nodes.new('ShaderNodeCombineXYZ')

    smooth = nodes.new('GeometryNodeSetShadeSmooth')
    smooth.domain = 'FACE'
    uv = _store_uv(nodes)
    xform = nodes.new('GeometryNodeTransform')

    links.new(_socket(gin.outputs, ids["Segments"]), _socket(cyl.inputs, "Vertices"))
    links.new(_socket(gin.outputs, ids["Rings"]), _socket(cyl.inputs, "Side Segments"))
    links.new(_socket(gin.outputs, ids["Radius"]), _socket(cyl.inputs, "Radius"))
    links.new(_socket(gin.outputs, ids["Height"]), depth.inputs[0])
    links.new(depth.outputs[0], _socket(cyl.inputs, "Depth"))
    links.new(_socket(gin.outputs, ids["Height"]), half.inputs[0])
    # The node builds the cylinder around zero: half the height up puts the base on the origin, unless CenteredZ.
    rest = nodes.new('ShaderNodeMath')
    rest.operation = 'SUBTRACT'
    rest.inputs[0].default_value = 1.0
    links.new(_socket(gin.outputs, ids["CenteredZ"]), rest.inputs[1])
    lift = nodes.new('ShaderNodeMath')
    lift.operation = 'MULTIPLY'
    links.new(half.outputs[0], lift.inputs[0])
    links.new(rest.outputs[0], lift.inputs[1])
    links.new(lift.outputs[0], _socket(offset.inputs, "Z"))
    rest.location = (440.0, -420.0)
    lift.location = (660.0, -420.0)

    links.new(_socket(cyl.outputs, "Mesh"), _socket(smooth.inputs, "Geometry"))
    links.new(_socket(cyl.outputs, "Side"), _socket(smooth.inputs, "Selection"))
    links.new(_socket(gin.outputs, ids["Smooth"]), _socket(smooth.inputs, "Shade Smooth"))
    links.new(smooth.outputs[0], _socket(uv.inputs, "Geometry"))
    links.new(_socket(cyl.outputs, "UV Map"), _enabled(uv.inputs, "Value"))
    links.new(uv.outputs[0], _socket(xform.inputs, "Geometry"))
    links.new(offset.outputs[0], _socket(xform.inputs, "Translation"))
    links.new(xform.outputs[0], gout.inputs[0])

    for i, node in enumerate((gin, cyl, smooth, uv, xform, gout)):
        node.location = (i * 220.0, 0.0)
    depth.location = (220.0, -260.0)
    half.location = (440.0, -300.0)
    offset.location = (660.0, -300.0)


def _build_cone(ng):
    ids = _group_outputs(ng)
    nodes, links = ng.nodes, ng.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')

    cone = nodes.new('GeometryNodeMeshCone')
    cone.fill_type = 'NGON'
    _socket(cone.inputs, "Side Segments").default_value = 1
    _socket(cone.inputs, "Fill Segments").default_value = 1

    def math(operation, x=None, y=None, z=None):
        node = nodes.new('ShaderNodeMath')
        node.operation = operation
        for i, value in enumerate((x, y, z)):
            if value is None:
                continue
            if isinstance(value, (int, float)):
                node.inputs[i].default_value = value
            else:
                links.new(value, node.inputs[i])
        return node

    base = _socket(gin.outputs, ids["Radius"])
    top = _socket(gin.outputs, ids["TopRadius"])
    height = _socket(gin.outputs, ids["Height"])
    depth = math('ABSOLUTE', height)
    # Unlike the cylinder, the cone node builds its mesh from z = 0 up to z = depth (not centered).
    # CenteredZ moves the whole cone down by half its (signed) height: the origin is then halfway up.
    centered = _socket(gin.outputs, ids["CenteredZ"])
    shift = math('SUBTRACT', math('MINIMUM', height, 0.0).outputs[0],
                 math('MULTIPLY', centered, math('MULTIPLY', height, 0.5).outputs[0]).outputs[0])
    # For a negative height the mesh is moved down, so its top (+Z) sits at the origin: the radii swap sides.
    upward = math('GREATER_THAN', height, -1e-9)
    mesh_top = math('MULTIPLY_ADD', math('SUBTRACT', top, base).outputs[0], upward.outputs[0], base)
    mesh_bottom = math('MULTIPLY_ADD', math('SUBTRACT', base, top).outputs[0], upward.outputs[0], top)
    offset = nodes.new('ShaderNodeCombineXYZ')

    smooth = nodes.new('GeometryNodeSetShadeSmooth')
    smooth.domain = 'FACE'
    uv = _store_uv(nodes)
    xform = nodes.new('GeometryNodeTransform')

    links.new(_socket(gin.outputs, ids["Segments"]), _socket(cone.inputs, "Vertices"))
    links.new(_socket(gin.outputs, ids["Rings"]), _socket(cone.inputs, "Side Segments"))
    links.new(mesh_top.outputs[0], _socket(cone.inputs, "Radius Top"))
    links.new(mesh_bottom.outputs[0], _socket(cone.inputs, "Radius Bottom"))
    links.new(depth.outputs[0], _socket(cone.inputs, "Depth"))
    links.new(shift.outputs[0], _socket(offset.inputs, "Z"))
    links.new(_socket(cone.outputs, "Mesh"), _socket(smooth.inputs, "Geometry"))
    links.new(_socket(cone.outputs, "Side"), _socket(smooth.inputs, "Selection"))
    links.new(_socket(gin.outputs, ids["Smooth"]), _socket(smooth.inputs, "Shade Smooth"))
    links.new(smooth.outputs[0], _socket(uv.inputs, "Geometry"))
    links.new(_socket(cone.outputs, "UV Map"), _enabled(uv.inputs, "Value"))
    links.new(uv.outputs[0], _socket(xform.inputs, "Geometry"))
    links.new(offset.outputs[0], _socket(xform.inputs, "Translation"))
    links.new(xform.outputs[0], gout.inputs[0])

    for i, node in enumerate((gin, cone, smooth, uv, xform, gout)):
        node.location = (i * 220.0, 0.0)
    offset.location = (660.0, -300.0)


def _build_sphere(ng):
    ids = _group_outputs(ng)
    nodes, links = ng.nodes, ng.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')

    sphere = nodes.new('GeometryNodeMeshUVSphere')
    rings = nodes.new('ShaderNodeMath')
    rings.operation = 'MULTIPLY'
    rings.inputs[1].default_value = 0.5
    at_least_two = nodes.new('ShaderNodeMath')
    at_least_two.operation = 'MAXIMUM'
    at_least_two.inputs[1].default_value = 2.0

    smooth = nodes.new('GeometryNodeSetShadeSmooth')
    smooth.domain = 'FACE'
    uv = _store_uv(nodes)

    links.new(_socket(gin.outputs, ids["Segments"]), _socket(sphere.inputs, "Segments"))
    links.new(_socket(gin.outputs, ids["Segments"]), rings.inputs[0])
    links.new(rings.outputs[0], at_least_two.inputs[0])
    links.new(at_least_two.outputs[0], _socket(sphere.inputs, "Rings"))
    links.new(_socket(gin.outputs, ids["Radius"]), _socket(sphere.inputs, "Radius"))
    links.new(_socket(sphere.outputs, "Mesh"), _socket(smooth.inputs, "Geometry"))
    links.new(_socket(gin.outputs, ids["Smooth"]), _socket(smooth.inputs, "Shade Smooth"))
    links.new(smooth.outputs[0], _socket(uv.inputs, "Geometry"))
    links.new(_socket(sphere.outputs, "UV Map"), _enabled(uv.inputs, "Value"))
    links.new(uv.outputs[0], gout.inputs[0])

    for i, node in enumerate((gin, sphere, smooth, uv, gout)):
        node.location = (i * 220.0, 0.0)
    rings.location = (0.0, -260.0)
    at_least_two.location = (220.0, -260.0)


def _build_quadsphere(ng):
    ids = _group_outputs(ng)
    nodes, links = ng.nodes, ng.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')

    def math(operation, x=None, y=None):
        node = nodes.new('ShaderNodeMath')
        node.operation = operation
        for i, value in enumerate((x, y)):
            if value is None:
                continue
            if isinstance(value, (int, float)):
                node.inputs[i].default_value = value
            else:
                links.new(value, node.inputs[i])
        return node

    # Quads along a grid edge: a quarter of the segments (at least one); a cube node counts vertices.
    quarter = math('MULTIPLY', _socket(gin.outputs, ids["Segments"]), 0.25)
    edge = math('MAXIMUM', math('ROUND', quarter.outputs[0]).outputs[0], 1.0)
    count = math('ADD', edge.outputs[0], 1.0)

    cube = nodes.new('GeometryNodeMeshCube')
    for axis in ("X", "Y", "Z"):
        links.new(count.outputs[0], _socket(cube.inputs, f"Vertices {axis}"))
    _socket(cube.inputs, "Size").default_value = (2.0, 2.0, 2.0)

    # Every vertex goes to the sphere: its direction from the center, at the radius.
    position = nodes.new('GeometryNodeInputPosition')
    direction = nodes.new('ShaderNodeVectorMath')
    direction.operation = 'NORMALIZE'
    scaled = nodes.new('ShaderNodeVectorMath')
    scaled.operation = 'SCALE'
    links.new(_socket(position.outputs, "Position"), direction.inputs[0])
    links.new(direction.outputs[0], scaled.inputs[0])
    links.new(_socket(gin.outputs, ids["Radius"]), _enabled(scaled.inputs, "Scale"))
    place = nodes.new('GeometryNodeSetPosition')
    links.new(_socket(cube.outputs, "Mesh"), _socket(place.inputs, "Geometry"))
    links.new(scaled.outputs[0], _socket(place.inputs, "Position"))

    smooth = nodes.new('GeometryNodeSetShadeSmooth')
    smooth.domain = 'FACE'
    uv = _store_uv(nodes)
    links.new(place.outputs[0], _socket(smooth.inputs, "Geometry"))
    links.new(_socket(gin.outputs, ids["Smooth"]), _socket(smooth.inputs, "Shade Smooth"))
    links.new(smooth.outputs[0], _socket(uv.inputs, "Geometry"))
    links.new(_socket(cube.outputs, "UV Map"), _enabled(uv.inputs, "Value"))
    links.new(uv.outputs[0], gout.inputs[0])

    for i, node in enumerate((gin, cube, place, smooth, uv, gout)):
        node.location = (i * 220.0, 0.0)
    for i, node in enumerate((quarter, edge, count)):
        node.location = (i * 220.0, -300.0)
    position.location = (220.0, -520.0)
    direction.location = (440.0, -520.0)
    scaled.location = (660.0, -520.0)


def _build_box(ng):
    ids = _group_outputs(ng)
    nodes, links = ng.nodes, ng.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')

    cube = nodes.new('GeometryNodeMeshCube')
    size = nodes.new('ShaderNodeCombineXYZ')
    offset = nodes.new('ShaderNodeCombineXYZ')
    uv = _store_uv(nodes)
    xform = nodes.new('GeometryNodeTransform')

    # Centered: the base is centered on the origin, so X and Y need no shift (the cube is built around zero).
    corner = nodes.new('ShaderNodeMath')
    corner.operation = 'SUBTRACT'
    corner.inputs[0].default_value = 1.0
    links.new(_socket(gin.outputs, ids["Centered"]), corner.inputs[1])
    corner.location = (220.0, -700.0)
    base = nodes.new('ShaderNodeMath')  # Z: the box rests on the origin unless CenteredZ puts the origin in its middle
    base.operation = 'SUBTRACT'
    base.inputs[0].default_value = 1.0
    links.new(_socket(gin.outputs, ids["CenteredZ"]), base.inputs[1])
    base.location = (220.0, -800.0)
    for i, (axis, name) in enumerate((("X", "Width"), ("Y", "Depth"), ("Z", "Height"))):
        absolute = nodes.new('ShaderNodeMath')
        absolute.operation = 'ABSOLUTE'
        half = nodes.new('ShaderNodeMath')
        half.operation = 'MULTIPLY'
        half.inputs[1].default_value = 0.5
        links.new(_socket(gin.outputs, ids[name]), absolute.inputs[0])
        links.new(absolute.outputs[0], _socket(size.inputs, axis))
        links.new(_socket(gin.outputs, ids[name]), half.inputs[0])
        shift = nodes.new('ShaderNodeMath')
        shift.operation = 'MULTIPLY'
        links.new(half.outputs[0], shift.inputs[0])
        links.new((base if axis == "Z" else corner).outputs[0], shift.inputs[1])
        links.new(shift.outputs[0], _socket(offset.inputs, axis))
        shift.location = (660.0, -300.0 - i * 120.0)
        absolute.location = (220.0, -260.0 - i * 120.0)
        half.location = (440.0, -300.0 - i * 120.0)

    links.new(size.outputs[0], _socket(cube.inputs, "Size"))
    rows = nodes.new('ShaderNodeMath')  # the cube node counts vertices, not rows
    rows.operation = 'ADD'
    rows.inputs[1].default_value = 1.0
    links.new(_socket(gin.outputs, ids["Rings"]), rows.inputs[0])
    links.new(rows.outputs[0], _socket(cube.inputs, "Vertices Z"))
    rows.location = (660.0, -600.0)
    links.new(_socket(cube.outputs, "Mesh"), _socket(uv.inputs, "Geometry"))
    links.new(_socket(cube.outputs, "UV Map"), _enabled(uv.inputs, "Value"))
    links.new(uv.outputs[0], _socket(xform.inputs, "Geometry"))
    links.new(offset.outputs[0], _socket(xform.inputs, "Translation"))
    links.new(xform.outputs[0], gout.inputs[0])

    for i, node in enumerate((gin, cube, uv, xform, gout)):
        node.location = (i * 220.0, 0.0)
    size.location = (660.0, -300.0)
    offset.location = (660.0, -480.0)


_BUILDERS = {KIND_CYLINDER: _build_cylinder, KIND_BOX: _build_box, KIND_CONE: _build_cone,
             KIND_SPHERE: _build_sphere, KIND_QUADSPHERE: _build_quadsphere}


def get_node_group(kind):
    for ng in bpy.data.node_groups:
        if (ng.bl_idname == 'GeometryNodeTree' and ng.get(KIND_KEY) == kind
                and ng.get(VERSION_KEY) == GROUP_VERSION[kind]):
            return ng
    ng = bpy.data.node_groups.new(GROUP_NAME[kind], 'GeometryNodeTree')
    ng[KIND_KEY] = kind
    ng[VERSION_KEY] = GROUP_VERSION[kind]
    ng.is_modifier = True
    _BUILDERS[kind](ng)
    return ng


def find_modifier(obj):
    """Return the EZ shape modifier of ``obj`` or None."""
    if obj is None or obj.type != 'MESH':
        return None
    for mod in obj.modifiers:
        if mod.type == 'NODES' and mod.node_group and mod.node_group.get(KIND_KEY) in KINDS:
            return mod
    return None


def kind_of(mod):
    return mod.node_group[KIND_KEY]


def input_identifier(mod, name):
    return mod.node_group.interface.items_tree[name].identifier


# Blender 5.0 replaced ID-property access (mod["Socket_1"]) with
# mod.properties.inputs.Socket_1.value. These helpers support both.

def _input_struct(mod, name):
    props = getattr(mod, "properties", None)
    if props is None or not hasattr(props, "inputs"):
        return None
    return getattr(props.inputs, input_identifier(mod, name))


def get_input(mod, name):
    struct = _input_struct(mod, name)
    return struct.value if struct is not None else mod[input_identifier(mod, name)]


def _set_input(mod, name, value):
    struct = _input_struct(mod, name)
    if struct is not None:
        struct.value = value
    else:
        mod[input_identifier(mod, name)] = value


def set_input(mod, name, value):
    """Set the input ``name`` of any Geometry Nodes modifier (a shape's, a join's ...)."""
    _set_input(mod, name, value)


def draw_input(layout, mod, name):
    struct = _input_struct(mod, name)
    if struct is not None:
        layout.prop(struct, "value", text=name)
    else:
        layout.prop(mod, f'["{input_identifier(mod, name)}"]', text=name)


def get_params(mod):
    """{parameter name: value} of the shape (names depend on its kind).

    A parameter that the shape's node group does not have yet (the group of an older file) has its default.
    """
    kind = kind_of(mod)
    params = {}
    for spec in _INPUTS[kind]:
        try:
            params[spec[0]] = get_input(mod, spec[0])
        except KeyError:
            params[spec[0]] = spec[2]
    for name in ("Smooth", "Centered", "CenteredZ"):
        if name in params:
            params[name] = bool(params[name])
    return params


def clamp_segments(value):
    return max(MIN_SEGMENTS, min(MAX_SEGMENTS, int(value)))


def set_params(obj, mod, **values):
    """Set parameters by lower-case keyword (``radius=``, ``width=``, ``smooth=`` ...)."""
    kind = kind_of(mod)
    valid = {name.lower(): name for name in param_names(kind)}
    for key, value in values.items():
        if value is None:
            continue
        if key not in valid:
            raise KeyError(f"{kind} has no parameter {key!r}")
        name = valid[key]
        if name in ("Radius", "TopRadius"):
            value = max(float(value), 0.0)
        elif name == "Segments":
            value = clamp_segments(value)
        elif name == "Rings":
            value = max(1, min(MAX_RINGS, int(value)))
        elif name in ("Smooth", "Centered", "CenteredZ"):
            value = bool(value)
        else:
            value = float(value)
        _set_input(mod, name, value)
    # Setting modifier inputs from Python does not tag the depsgraph by itself.
    obj.update_tag()


def origin_offset(kind, params):
    """Local vector from the origin of the shape's *corner frame* to the object origin, negated.

    Positions in the corner frame (where a box spans 0..size on each axis) become object-local positions by
    subtracting this: (Width/2, Depth/2, 0) for a box with its origin at the center of the base, with Height/2
    on Z too when the origin is the center of the whole box, else 0.
    """
    if kind in (KIND_CYLINDER, KIND_CONE):
        return Vector((0.0, 0.0, params["Height"] * 0.5 if params.get("CenteredZ") else 0.0))
    if kind != KIND_BOX:
        return Vector((0.0, 0.0, 0.0))
    return Vector((params["Width"] * 0.5 if params.get("Centered") else 0.0,
                   params["Depth"] * 0.5 if params.get("Centered") else 0.0,
                   params["Height"] * 0.5 if params.get("CenteredZ") else 0.0))


def contains_local(kind, params, point, eps=0.0):
    """True if a point in the shape's local space lies inside or on the closed solid."""
    if kind == KIND_CYLINDER:
        radius, height = params["Radius"], params["Height"]
        z = point.z + origin_offset(kind, params).z  # height measured from the base (the origin may be halfway up)
        return (min(0.0, height) - eps <= z <= max(0.0, height) + eps
                and math.hypot(point.x, point.y) <= radius + eps)
    if kind == KIND_CONE:
        height = params["Height"]
        z = point.z + origin_offset(kind, params).z
        if not min(0.0, height) - eps <= z <= max(0.0, height) + eps:
            return False
        t = min(max(z / height, 0.0), 1.0) if abs(height) > 1e-12 else 0.0
        radius = params["Radius"] + (params["TopRadius"] - params["Radius"]) * t
        return math.hypot(point.x, point.y) <= radius + eps
    if kind in SPHERE_KINDS:
        return point.length <= params["Radius"] + eps
    corner_point = point + origin_offset(kind, params)
    return all(min(0.0, size) - eps <= value <= max(0.0, size) + eps
               for value, size in zip(corner_point, (params["Width"], params["Depth"], params["Height"])))


def center_local(kind, params):
    """The middle of the shape's bounding box, in its local space."""
    if kind in SPHERE_KINDS:
        return Vector((0.0, 0.0, 0.0))
    if kind == KIND_BOX:
        return Vector((params["Width"], params["Depth"], params["Height"])) * 0.5 - origin_offset(kind, params)
    return Vector((0.0, 0.0, params["Height"] * 0.5)) - origin_offset(kind, params)


def characteristic_size(kind, params):
    """A length that scales tolerances (the biggest dimension of the shape)."""
    if kind == KIND_CYLINDER:
        return max(params["Radius"], abs(params["Height"]), 1e-3)
    if kind == KIND_CONE:
        return max(params["Radius"], params["TopRadius"], abs(params["Height"]), 1e-3)
    if kind in SPHERE_KINDS:
        return max(params["Radius"], 1e-3)
    return max(abs(params["Width"]), abs(params["Depth"]), abs(params["Height"]), 1e-3)


def create_shape(context, kind, matrix, name=None, **params):
    """Add a new parametric object of ``kind``, select it and make it active."""
    name = name or MODIFIER_NAME[kind]
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    obj.matrix_world = Matrix(matrix)

    mod = obj.modifiers.new(MODIFIER_NAME[kind], 'NODES')
    mod.node_group = get_node_group(kind)
    set_params(obj, mod, **params)

    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def create_cylinder(context, matrix, radius, height, segments, name="EZ Cylinder", smooth=True, centered_z=False):
    return create_shape(context, KIND_CYLINDER, matrix, name, radius=radius, height=height,
                        segments=segments, smooth=smooth, centeredz=centered_z)


def create_cone(context, matrix, radius, top_radius, height, segments, name="EZ Cone", smooth=True,
                centered_z=False):
    return create_shape(context, KIND_CONE, matrix, name, radius=radius, topradius=top_radius,
                        height=height, segments=segments, smooth=smooth, centeredz=centered_z)


def create_sphere(context, matrix, radius, segments, name="EZ Sphere", smooth=True):
    return create_shape(context, KIND_SPHERE, matrix, name, radius=radius, segments=segments, smooth=smooth)


def create_quadsphere(context, matrix, radius, segments, name="EZ Quad Sphere", smooth=True):
    return create_shape(context, KIND_QUADSPHERE, matrix, name, radius=radius, segments=segments, smooth=smooth)


def create_box(context, matrix, width, depth, height, name="EZ Box", centered=False, centered_z=False):
    return create_shape(context, KIND_BOX, matrix, name, width=width, depth=depth, height=height,
                        centered=centered, centeredz=centered_z)


def remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
