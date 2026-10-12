"""Joining two shapes into one with a soldered / silicone-like fillet.

A *join* is a Geometry Nodes modifier on the target mesh that references the other shape (an
ordinary EZ shape, see ``cuts.py``). It stays live: move the shapes or drag their handles and the
bead follows. The node group works in three steps:

1. an exact Boolean *union* of the two meshes (clean surfaces, clean sharp edges);
2. a *morphological closing* of that union on a signed distance grid: grow the surface by the fillet
   radius and shrink it back. Convex edges return to where they were, but concave places (the
   junction of the shapes) stay filled: a smooth bead of that radius;
3. a second union of the clean mesh with the closed one, which has been pulled in by a fraction of a
   voxel. Away from the junction the clean mesh wins, so flat faces and edges stay exact and the
   voxel stair-steps of the grid never show; only the bead is taken from the grid.

Smooth shading is set at the end by edge angle, so the bead is smooth and the sharp edges stay sharp.
The Bevel modifier was tried first, but it works on the Boolean's triangles and breaks on the long
sliver triangles an intersection produces. The SDF nodes exist in Blender 4.2 and later.
"""

import math

import bpy

from . import shapes as cn

JOIN_KEY = "ezhs_join"
JOIN_VERSION = 1
JOIN_GROUP_NAME = "EZ Join"

OTHER = "Other"
FILLET = "Fillet"
DETAIL = "Detail"
SMOOTH_ANGLE = math.radians(35.0)  # edges flatter than this are shaded smooth

DEFAULT_DETAIL = 5     # voxels per fillet radius
MIN_DETAIL, MAX_DETAIL = 2, 16
MAX_GRID = 256.0       # the voxel size never goes below (bounding box diagonal) / MAX_GRID: keeps evaluation bounded
SHRINK = 0.5           # of a voxel: how far the closed mesh is pulled inside the clean one


def _multi_input(node):
    for socket in node.inputs:
        if socket.is_multi_input:
            return socket
    raise KeyError("multi-input socket")


def _math(nodes, operation, a=None, b=None):
    node = nodes.new('ShaderNodeMath')
    node.operation = operation
    if a is not None:
        node.inputs[0].default_value = a
    if b is not None:
        node.inputs[1].default_value = b
    return node


def _union(nodes, links, *geometries):
    node = nodes.new('GeometryNodeMeshBoolean')
    node.operation = 'UNION'
    if hasattr(node, "solver"):
        node.solver = 'EXACT'
    for geometry in geometries:
        links.new(geometry, _multi_input(node))
    return node


def smooth_by_angle(nodes, links, geometry, angle=None):
    """Shade faces smooth but keep edges sharper than ``angle`` (radians: a number or a socket; default SMOOTH_ANGLE) sharp.

    Returns the output socket.
    """
    edge_angle = nodes.new('GeometryNodeInputMeshEdgeAngle')
    flat = nodes.new('FunctionNodeCompare')
    flat.data_type = 'FLOAT'
    flat.operation = 'LESS_THAN'
    if angle is None or isinstance(angle, (int, float)):
        flat.inputs[1].default_value = SMOOTH_ANGLE if angle is None else angle
    else:
        links.new(angle, flat.inputs[1])
    links.new(edge_angle.outputs['Unsigned Angle'], flat.inputs[0])
    edges = nodes.new('GeometryNodeSetShadeSmooth')
    edges.domain = 'EDGE'
    links.new(geometry, edges.inputs[0])
    links.new(flat.outputs['Result'], cn._socket(edges.inputs, "Shade Smooth"))
    faces = nodes.new('GeometryNodeSetShadeSmooth')
    faces.domain = 'FACE'
    links.new(edges.outputs[0], faces.inputs[0])
    return faces.outputs[0]


def _build(ng):
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=OTHER, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape that is joined to this mesh")
    cn._new_input(ng, FILLET, 'NodeSocketFloat', 0.2, 0.0, 1000.0,
                  "Radius of the bead where the shapes meet (0 is a plain union)")
    cn._new_input(ng, DETAIL, 'NodeSocketInt', DEFAULT_DETAIL, MIN_DETAIL, MAX_DETAIL,
                  "Grid voxels per fillet radius: higher is smoother but slower")

    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[OTHER], info.inputs['Object'])

    union = _union(nodes, links, gin.outputs['Geometry'], info.outputs['Geometry'])
    mesh = union.outputs['Mesh']

    # voxel size: fillet / detail, but never so fine that the grid explodes
    bbox = nodes.new('GeometryNodeBoundBox')
    links.new(mesh, bbox.inputs[0])
    span = nodes.new('ShaderNodeVectorMath')
    span.operation = 'DISTANCE'
    links.new(bbox.outputs['Min'], span.inputs[0])
    links.new(bbox.outputs['Max'], span.inputs[1])
    coarse = _math(nodes, 'DIVIDE', None, MAX_GRID)
    links.new(span.outputs['Value'], coarse.inputs[0])
    fine = _math(nodes, 'DIVIDE')
    links.new(gin.outputs[FILLET], fine.inputs[0])
    links.new(gin.outputs[DETAIL], fine.inputs[1])
    voxel = _math(nodes, 'MAXIMUM')
    links.new(fine.outputs[0], voxel.inputs[0])
    links.new(coarse.outputs[0], voxel.inputs[1])
    voxel_min = _math(nodes, 'MAXIMUM', None, 1e-5)  # a flat or empty union has no span
    links.new(voxel.outputs[0], voxel_min.inputs[0])

    # the narrow band must hold the offset: ceil(fillet / voxel) + a few voxels
    reach = _math(nodes, 'DIVIDE')
    links.new(gin.outputs[FILLET], reach.inputs[0])
    links.new(voxel_min.outputs[0], reach.inputs[1])
    band_f = _math(nodes, 'ADD', None, 3.0)
    links.new(reach.outputs[0], band_f.inputs[0])
    band = nodes.new('FunctionNodeFloatToInt')
    band.rounding_mode = 'CEILING'
    links.new(band_f.outputs[0], band.inputs[0])

    def to_sdf(geometry):
        node = nodes.new('GeometryNodeMeshToSDFGrid')
        links.new(geometry, node.inputs['Mesh'])
        links.new(voxel_min.outputs[0], node.inputs['Voxel Size'])
        links.new(band.outputs[0], node.inputs['Band Width'])
        return node.outputs[0]

    def to_mesh(grid, threshold):
        node = nodes.new('GeometryNodeGridToMesh')
        links.new(grid, node.inputs['Grid'])
        if isinstance(threshold, (int, float)):
            node.inputs['Threshold'].default_value = threshold
        else:
            links.new(threshold, node.inputs['Threshold'])
        return node.outputs['Mesh']

    grown = to_mesh(to_sdf(mesh), gin.outputs[FILLET])  # surface moved outwards by the fillet
    pull = _math(nodes, 'MULTIPLY', None, SHRINK)
    links.new(voxel_min.outputs[0], pull.inputs[0])
    back = _math(nodes, 'ADD')
    links.new(gin.outputs[FILLET], back.inputs[0])
    links.new(pull.outputs[0], back.inputs[1])
    inward = _math(nodes, 'MULTIPLY', None, -1.0)
    links.new(back.outputs[0], inward.inputs[0])
    closed = to_mesh(to_sdf(grown), inward.outputs[0])  # ... and back inwards (a little further)

    final = _union(nodes, links, mesh, closed)

    use_fillet = nodes.new('FunctionNodeCompare')
    use_fillet.data_type = 'FLOAT'
    use_fillet.operation = 'GREATER_THAN'
    use_fillet.inputs[1].default_value = 1e-6
    links.new(gin.outputs[FILLET], use_fillet.inputs[0])
    switch = nodes.new('GeometryNodeSwitch')
    switch.input_type = 'GEOMETRY'
    links.new(use_fillet.outputs['Result'], switch.inputs['Switch'])
    links.new(mesh, switch.inputs['False'])
    links.new(final.outputs['Mesh'], switch.inputs['True'])

    links.new(smooth_by_angle(nodes, links, switch.outputs[0]), gout.inputs[0])


def get_join_group():
    for ng in bpy.data.node_groups:
        if (ng.bl_idname == 'GeometryNodeTree' and ng.get(JOIN_KEY) == JOIN_VERSION):
            return ng
    ng = bpy.data.node_groups.new(JOIN_GROUP_NAME, 'GeometryNodeTree')
    ng[JOIN_KEY] = JOIN_VERSION
    ng.is_modifier = True
    try:
        _build(ng)
    except Exception:
        bpy.data.node_groups.remove(ng)  # no half-built group is left behind (e.g. Blender without SDF nodes)
        raise
    return ng


def is_join_modifier(mod):
    return mod.type == 'NODES' and mod.node_group is not None and JOIN_KEY in mod.node_group


def available():
    """True if this Blender has the SDF grid nodes a join needs (4.2 and later)."""
    return hasattr(bpy.types, "GeometryNodeMeshToSDFGrid") and hasattr(bpy.types, "GeometryNodeGridToMesh")


def other_of(mod):
    return cn.get_input(mod, OTHER)


def default_fillet(target, other):
    """A quarter of the smallest dimension of the two shapes: a visible bead that never swallows either."""
    def smallest(obj):
        sizes = [d for d in obj.dimensions if d > 1e-6]
        return min(sizes) if sizes else 1.0
    return 0.25 * min(smallest(target), smallest(other))


def add_join_modifier(target, other, name, fillet=None):
    """Append the live join of ``other`` to ``target``. Returns the modifier."""
    mod = target.modifiers.new(name, 'NODES')
    mod.node_group = get_join_group()
    cn.set_input(mod, OTHER, other)
    cn.set_input(mod, FILLET, default_fillet(target, other) if fillet is None else float(fillet))
    return mod
