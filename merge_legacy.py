"""Legacy DEFORM, ROWS and BEVEL builders retained for saved-file compatibility."""

import math

from . import joins
from . import shapes as cn
from .merge_settings import *
from .merge_nodes import (
    _math, _vector, _switch, _smooth_normals, _surface_normal_sampler,
)


def _build(ng):
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape this one spreads over")
    cn._new_input(ng, GLOBAL, 'NodeSocketFloat', 1.0, 0.0, 100.0,
                  "Scales Radius and Strength together (0 turns the merge off)")
    cn._new_input(ng, RADIUS, 'NodeSocketFloat', 0.15, 0.0, 1000.0,
                  "How far from the other surface the shape is bent (the height of the flare)")
    cn._new_input(ng, STRENGTH, 'NodeSocketFloat', 0.3, 0.0, 1000.0,
                  "How far the flare spreads over the other surface (its width)")
    cn._new_input(ng, FALLOFF, 'NodeSocketInt', SMOOTH, SMOOTH, SPHERE,
                  "Profile of the flare: 0 Smooth (quarter ellipse), 1 Soft (parabola), 2 Linear (chamfer), 3 Custom curve, "
                  "4 Sharp (tight pointed root), 5 Sphere (bulging, convex)")
    cn._new_input(ng, SUBDIVISION, 'NodeSocketInt', DEFAULT_SUBDIVISION, MIN_SUBDIVISION, MAX_SUBDIVISION,
                  "Subdivision levels applied to the shape so that the flare can bend (more is smoother and slower)")

    cn._new_input(ng, CONTACT, 'NodeSocketBool', True, False, True,
                  "Cut the shape along the line where it meets the other mesh, so the flare starts on a clean "
                  "loop (the part inside the other mesh is dropped; it was hidden anyway)")

    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])

    # Global Merge scales the two lengths together
    zone = _math(nodes, links, 'MULTIPLY', gin.outputs[RADIUS], gin.outputs[GLOBAL])
    width = _math(nodes, links, 'MULTIPLY', gin.outputs[STRENGTH], gin.outputs[GLOBAL])

    # Contact rows: an exact Boolean difference cuts the shape along its intersection with the other mesh,
    # which adds an edge loop *on* the contact line whatever the angle. The skirt then ends on a clean loop
    # (every vertex of it at distance 0) instead of on the shape's own rows, which cross the contact
    # obliquely and made the edge jagged. The faces left lying flat on the other mesh (its cut-out) go.
    difference = nodes.new('GeometryNodeMeshBoolean')
    difference.operation = 'DIFFERENCE'
    if hasattr(difference, "solver"):
        difference.solver = 'EXACT'
    links.new(gin.outputs['Geometry'], difference.inputs[0])
    links.new(info.outputs['Geometry'], joins._multi_input(difference))
    on_surface = nodes.new('GeometryNodeProximity')
    on_surface.target_element = 'FACES'
    links.new(info.outputs['Geometry'], on_surface.inputs['Target'])
    flat = _math(nodes, links, 'LESS_THAN', on_surface.outputs['Distance'],
                 _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'MULTIPLY', zone, 1e-3), 1e-6))
    dropped = nodes.new('GeometryNodeDeleteGeometry')
    dropped.domain = 'FACE'
    # The exact Boolean leaves the vertices of the cut loop doubled (one per side): their normals differ, so the
    # flare would pull the two copies apart and tear the surface along the loop. Weld them first.
    welded = nodes.new('GeometryNodeMergeByDistance')
    welded.inputs['Distance'].default_value = 1e-5
    links.new(difference.outputs['Mesh'], welded.inputs['Geometry'])
    links.new(welded.outputs['Geometry'], dropped.inputs['Geometry'])
    links.new(flat, dropped.inputs['Selection'])
    prepared = _switch(nodes, links, 'GEOMETRY', gin.outputs[CONTACT], gin.outputs['Geometry'], dropped.outputs[0])

    # The shape's own (vertex) normals are stored before subdividing: the subdivision interpolates them, so the
    # spreading direction changes smoothly across a flat facet. Read after it, the normal of a vertex in the middle of
    # a facet is the facet's and the one on a seam an average, which made the flare ripple once per facet.
    keep_normal = nodes.new('GeometryNodeStoreNamedAttribute')
    keep_normal.data_type = 'FLOAT_VECTOR'
    keep_normal.domain = 'POINT'
    keep_normal.inputs['Name'].default_value = OWN_NORMAL
    links.new(prepared, keep_normal.inputs['Geometry'])
    links.new(nodes.new('GeometryNodeInputNormal').outputs[0], keep_normal.inputs['Value'])

    subdivide = nodes.new('GeometryNodeSubdivideMesh')
    links.new(keep_normal.outputs[0], subdivide.inputs[0])
    links.new(gin.outputs[SUBDIVISION], subdivide.inputs['Level'])

    # where is the other surface: closest point, its normal, the distance, and which side we are on
    proximity = nodes.new('GeometryNodeProximity')
    proximity.target_element = 'FACES'
    links.new(info.outputs['Geometry'], proximity.inputs['Target'])
    point, distance = proximity.outputs['Position'], proximity.outputs['Distance']
    # The other mesh with its faces shaded smooth by angle (sharp edges stay): its corner normals are interpolated
    # across the facets of a curved surface, where the flat-shaded mesh would give the facet's normal everywhere.
    shaded = _smooth_normals(nodes, links, info.outputs['Geometry'])
    base_normal = _surface_normal_sampler(nodes, links, shaded)
    sampled = base_normal.outputs['Value']  # B's (corner) normal at the closest point

    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    away = _vector(nodes, links, 'SUBTRACT', position, point)
    side = _vector(nodes, links, 'DOT_PRODUCT', away, sampled)
    # outside the surface the direction to the closest point *is* the normal (exact for flat faces,
    # whatever the shading); exactly on it the sampled normal takes over
    toward = _vector(nodes, links, 'NORMALIZE', away)
    outside = _math(nodes, links, 'GREATER_THAN', side, -1e-6)
    outward = _switch(nodes, links, 'VECTOR', outside, _vector(nodes, links, 'SCALE', toward, scale=-1.0), toward)
    # (p - q) is only a direction when p is clearly off the surface: a hair away it is noise along the surface and
    # tore the flare apart at the top and bottom of a pipe; there the sampled normal is used)
    off_surface = _math(nodes, links, 'GREATER_THAN', distance, _math(nodes, links, 'MULTIPLY', zone, 0.03))
    normal = _switch(nodes, links, 'VECTOR', off_surface, sampled, outward)

    # On a faceted curved mesh the closest point of a whole fan of points is one and the same vertex or seam, so
    # everything built on it piles up into radial streaks. Follow the normal back to the surface with a ray instead
    # and read the smooth (interpolated) normal where it lands: that point and that normal change continuously from
    # one facet to the next, which is what a smooth surface would give.
    reach = _math(nodes, links, 'ADD', _math(nodes, links, 'ADD', zone, _math(nodes, links, 'MULTIPLY', gin.outputs[STRENGTH],
                  gin.outputs[GLOBAL])), 0.01)
    probe = nodes.new('GeometryNodeRaycast')
    links.new(info.outputs['Geometry'], probe.inputs['Target Geometry'])
    links.new(_vector(nodes, links, 'ADD', position, _vector(nodes, links, 'SCALE', normal, scale=reach)), probe.inputs['Source Position'])
    links.new(_vector(nodes, links, 'SCALE', normal, scale=-1.0), probe.inputs['Ray Direction'])
    links.new(_math(nodes, links, 'MULTIPLY', reach, 2.0), probe.inputs['Ray Length'])
    hit_point = _switch(nodes, links, 'VECTOR', probe.outputs['Is Hit'], point, probe.outputs['Hit Position'])
    smooth_normal = _surface_normal_sampler(nodes, links, shaded)
    links.new(hit_point, smooth_normal.inputs['Sample Position'])
    # Off the surface, (p - q) *is* the exact outward normal (q is the nearest point): keep it. Sample Nearest Surface
    # is only used in the thin on/inside band where (p - q) is noise -- and even there it can return the wrong face's
    # normal on a horizontal cylinder (the cap's, over a wide swath of the crest), so it is a fallback, not the default.
    resampled = _switch(nodes, links, 'VECTOR', probe.outputs['Is Hit'], normal,
                        _vector(nodes, links, 'NORMALIZE', smooth_normal.outputs['Value']))
    point, normal = hit_point, _switch(nodes, links, 'VECTOR', off_surface, resampled, outward)

    # the spreading direction: the shape's own normal, flattened onto the other surface
    kept = nodes.new('GeometryNodeInputNamedAttribute')
    kept.data_type = 'FLOAT_VECTOR'
    kept.inputs['Name'].default_value = OWN_NORMAL
    own = _vector(nodes, links, 'NORMALIZE', kept.outputs['Attribute'])
    lift = _vector(nodes, links, 'SCALE', normal, scale=_vector(nodes, links, 'DOT_PRODUCT', own, normal))
    spread = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'SUBTRACT', own, lift))

    # profile: u = 0 on the surface, 1 at the edge of the zone
    radius = _math(nodes, links, 'MAXIMUM', zone, 1e-6)
    u = _math(nodes, links, 'DIVIDE', distance, radius, clamp=True)
    theta = _math(nodes, links, 'MULTIPLY', u, math.pi / 2.0)
    one_minus_u = _math(nodes, links, 'SUBTRACT', 1.0, u)
    smooth_h = _math(nodes, links, 'MULTIPLY', zone,
                     _math(nodes, links, 'SUBTRACT', 1.0, _math(nodes, links, 'COSINE', theta)))
    smooth_o = _math(nodes, links, 'MULTIPLY', width,
                     _math(nodes, links, 'SUBTRACT', 1.0, _math(nodes, links, 'SINE', theta)))
    soft_h = _math(nodes, links, 'MULTIPLY', zone, _math(nodes, links, 'MULTIPLY', u, u))
    linear_h = _math(nodes, links, 'MULTIPLY', zone, u)
    linear_o = _math(nodes, links, 'MULTIPLY', width, one_minus_u)  # also the Soft width

    def is_falloff(value):
        return _math(nodes, links, 'COMPARE', gin.outputs[FALLOFF], float(value), 0.1)

    # Sharp: the root hugs the surface and the wall climbs late (cubic, pointed); Sphere: a convex quarter ellipse, the
    # flare bulges out like the cap of a ball (height and width swap the sine and the cosine of Smooth)
    cube = lambda x: _math(nodes, links, 'MULTIPLY', x, _math(nodes, links, 'MULTIPLY', x, x))
    sharp_h = _math(nodes, links, 'MULTIPLY', zone, cube(u))
    sharp_o = _math(nodes, links, 'MULTIPLY', width, cube(one_minus_u))
    sphere_h = _math(nodes, links, 'MULTIPLY', zone, _math(nodes, links, 'SINE', theta))
    sphere_o = _math(nodes, links, 'MULTIPLY', width, _math(nodes, links, 'COSINE', theta))

    def pick(default, *choices):
        """``default`` unless the Falloff is one of ``choices`` ((falloff, value) pairs)."""
        result = default
        for value, option in choices:
            result = _switch(nodes, links, 'FLOAT', is_falloff(value), result, option)
        return result

    height = pick(linear_h, (SMOOTH, smooth_h), (SOFT, soft_h), (SHARP, sharp_h), (SPHERE, sphere_h))
    # Custom: the height rises linearly (like Linear), the drawn curve says how much of Strength is still spread there
    curve = nodes.new('ShaderNodeFloatCurve')
    curve.name = curve.label = CURVE_NODE
    curve.inputs['Factor'].default_value = 1.0
    links.new(u, curve.inputs['Value'])
    points = curve.mapping.curves[0].points
    for i, (x, y) in enumerate(DEFAULT_CURVE):
        knot = points[i] if i < len(points) else points.new(x, y)
        knot.location = (x, y)
        knot.handle_type = 'AUTO'
    curve.mapping.update()
    custom_o = _math(nodes, links, 'MULTIPLY', width, curve.outputs[0])
    across = pick(linear_o, (SMOOTH, smooth_o), (SHARP, sharp_o), (SPHERE, sphere_o), (CUSTOM, custom_o))

    # Inside the surface (down to Radius) the part that is hidden follows the same shape: sunk by its depth,
    # spread by the linear width, so every vertex lies on one continuous surface (no jagged edge where
    # the mesh crosses the other surface)
    depth = _math(nodes, links, 'MULTIPLY', distance, -1.0)
    height = _switch(nodes, links, 'FLOAT', outside, depth, height)
    across = _switch(nodes, links, 'FLOAT', outside, linear_o, across)
    slid = _vector(nodes, links, 'ADD', point, _vector(nodes, links, 'SCALE', spread, scale=across))
    # `slid` is on B's tangent plane: on a curved B it has left the surface. Drop it back onto B and
    # lift it by the profile height along the normal there, so the flare follows the curvature.
    # A ray along the normal, not the nearest point: the nearest point of a faceted curved mesh piles a whole cone of
    # points up on each seam between two facets, which cracked the flare along radial lines.
    ray = nodes.new('GeometryNodeRaycast')
    links.new(info.outputs['Geometry'], ray.inputs['Target Geometry'])
    links.new(_vector(nodes, links, 'ADD', slid, _vector(nodes, links, 'SCALE', normal, scale=reach)), ray.inputs['Source Position'])
    links.new(_vector(nodes, links, 'SCALE', normal, scale=-1.0), ray.inputs['Ray Direction'])
    links.new(_math(nodes, links, 'MULTIPLY', reach, 2.0), ray.inputs['Ray Length'])
    landed = _switch(nodes, links, 'VECTOR', ray.outputs['Is Hit'], slid, ray.outputs['Hit Position'])
    landing_normal = _surface_normal_sampler(nodes, links, shaded)
    links.new(landed, landing_normal.inputs['Sample Position'])
    lift_direction = landing_normal.outputs['Value']
    moved = _vector(nodes, links, 'ADD', landed, _vector(nodes, links, 'SCALE', lift_direction, scale=height))
    # At the edge of the zone the shape must be left exactly where it is. On a faceted curved mesh the smooth normal is
    # a few degrees off the facet's, so (surface point + normal * distance) misses the vertex by a facet-periodic
    # offset; that miss is faded in towards the edge (it is zero on flat meshes and on the contact line).
    signed = _switch(nodes, links, 'FLOAT', outside, depth, distance)
    miss = _vector(nodes, links, 'SUBTRACT', position,
                   _vector(nodes, links, 'ADD', point, _vector(nodes, links, 'SCALE', normal, scale=signed)))
    moved = _vector(nodes, links, 'ADD', moved, _vector(nodes, links, 'SCALE', miss, scale=_math(nodes, links, 'MULTIPLY', u, u)))
    near = _math(nodes, links, 'LESS_THAN', distance, zone)
    target = _switch(nodes, links, 'VECTOR', near, position, moved)

    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(subdivide.outputs[0], set_position.inputs['Geometry'])
    links.new(target, set_position.inputs['Position'])

    faces = nodes.new('GeometryNodeAttributeDomainSize')
    faces.component = 'MESH'
    links.new(info.outputs['Geometry'], faces.inputs[0])
    has_base = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'GREATER_THAN', faces.outputs['Face Count'], 0.5),
                     _math(nodes, links, 'GREATER_THAN', zone, 1e-6))  # nothing to spread over, or switched off
    result = _switch(nodes, links, 'GEOMETRY', has_base, gin.outputs['Geometry'], set_position.outputs[0])
    links.new(joins.smooth_by_angle(nodes, links, result), gout.inputs[0])


def _build_flare(ng):
    """The flare of the Rows method.

    The shape arrives cut along the contact and bevelled into rows (see ``_add_rows_merge``): rows that run
    parallel to the contact line, a distance ``w`` apart measured along the wall. The *spread edge* is the contact
    line itself, moved out onto the other mesh by ``Spread``; the *original edge* is the row ``Original Height``
    up the wall, which does not move. The rows in between slide along a fillet curve that joins the two edges and is
    tangent to the wall and to the other surface: a conic through the corner with a weight set by ``Profile``
    (0 a straight chamfer, 0.5 round, 1 a sharp corner), or a curve drawn by hand.
    """
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape this one spreads over")
    cn._new_input(ng, GLOBAL, 'NodeSocketFloat', 1.0, 0.0, 100.0,
                  "Scales Original Height and Spread together")
    cn._new_input(ng, HEIGHT, 'NodeSocketFloat', 0.15, 0.0, 1000.0,
                  "Height of the original edge: where the shape starts to bend, measured up its wall from the contact")
    cn._new_input(ng, SPREAD, 'NodeSocketFloat', 0.3, 0.0, 1000.0,
                  "How far the spread edge lies from the contact, over the other surface")
    cn._new_input(ng, PROFILE, 'NodeSocketFloat', 0.5, 0.0, 1.0,
                  "Shape between the two edges: 0 straight chamfer, 0.5 round, 1 sharp corner (like Bevel's profile)")
    cn._new_input(ng, FALLOFF, 'NodeSocketInt', SMOOTH, SMOOTH, CUSTOM,
                  "0 Round (conic with Profile), 2 Linear (chamfer), 3 Custom curve")
    cn._new_input(ng, LIFT, 'NodeSocketFloat', 0.0, -1000.0, 1000.0,
                  "Height of the spread edge above the other surface (0: it lies on it)")
    cn._new_input(ng, SUBDIVISION, 'NodeSocketInt', ROWS_SUBDIVISION, MIN_SUBDIVISION, MAX_SUBDIVISION,
                  "Subdivision of the shape (the rows already follow the contact: only its columns need some)")
    cn._new_input(ng, SMOOTH_ANGLE, 'NodeSocketFloat', math.radians(35.0), 0.0, math.pi,
                  "Edges sharper than this stay sharp (the original and the spread edge, when the profile makes them)")
    ng.interface.items_tree[SMOOTH_ANGLE].subtype = 'ANGLE'

    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])
    other = info.outputs['Geometry']
    height = _math(nodes, links, 'MULTIPLY', gin.outputs[HEIGHT], gin.outputs[GLOBAL])
    spread_len = _math(nodes, links, 'MULTIPLY', gin.outputs[SPREAD], gin.outputs[GLOBAL])

    # the faces that lie on the other mesh (the cap of the cut and its Bevel rows) go; their vertices are the contact
    on_other = nodes.new('GeometryNodeProximity')
    on_other.target_element = 'FACES'
    links.new(other, on_other.inputs['Target'])
    box = nodes.new('GeometryNodeBoundBox')
    links.new(gin.outputs['Geometry'], box.inputs[0])
    span = nodes.new('ShaderNodeVectorMath')
    span.operation = 'DISTANCE'
    links.new(box.outputs['Min'], span.inputs[0])
    links.new(box.outputs['Max'], span.inputs[1])
    tolerance = _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'MULTIPLY', span.outputs['Value'], 1e-5), 1e-7)
    lying = _math(nodes, links, 'LESS_THAN', on_other.outputs['Distance'], tolerance)
    contact = nodes.new('GeometryNodeSeparateGeometry')
    contact.domain = 'POINT'
    links.new(gin.outputs['Geometry'], contact.inputs['Geometry'])
    links.new(lying, contact.inputs['Selection'])
    dropped = nodes.new('GeometryNodeDeleteGeometry')
    dropped.domain = 'FACE'
    links.new(gin.outputs['Geometry'], dropped.inputs['Geometry'])
    face_on_other = nodes.new('GeometryNodeProximity')
    face_on_other.target_element = 'FACES'
    links.new(other, face_on_other.inputs['Target'])
    links.new(_math(nodes, links, 'LESS_THAN', face_on_other.outputs['Distance'], tolerance), dropped.inputs['Selection'])

    subdivide = nodes.new('GeometryNodeSubdivideMesh')
    links.new(dropped.outputs[0], subdivide.inputs[0])
    links.new(gin.outputs[SUBDIVISION], subdivide.inputs['Level'])

    # where is the contact line: its nearest point L, and the distance w to it (along the wall)
    nearest = nodes.new('GeometryNodeProximity')
    nearest.target_element = 'POINTS'
    links.new(contact.outputs['Selection'], nearest.inputs['Target'])
    landmark, wall_length = nearest.outputs['Position'], nearest.outputs['Distance']
    surface = nodes.new('GeometryNodeSampleNearestSurface')
    surface.data_type = 'FLOAT_VECTOR'
    links.new(other, surface.inputs['Mesh'])
    links.new(nodes.new('GeometryNodeInputNormal').outputs[0], surface.inputs['Value'])
    links.new(landmark, surface.inputs['Sample Position'])
    ground_normal = surface.outputs['Value']

    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    wall = _vector(nodes, links, 'SUBTRACT', position, landmark)  # from the contact up the wall
    wall_up = _vector(nodes, links, 'DOT_PRODUCT', wall, ground_normal)
    wall_flat = _vector(nodes, links, 'SUBTRACT', wall, _vector(nodes, links, 'SCALE', ground_normal, scale=wall_up))
    own = nodes.new('GeometryNodeInputNormal').outputs[0]
    lift = _vector(nodes, links, 'SCALE', ground_normal, scale=_vector(nodes, links, 'DOT_PRODUCT', own, ground_normal))
    outward = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'SUBTRACT', own, lift))
    # Spread is a distance from the wall, not along the (averaged) vertex normal: at a corner of a box the vertex
    # normal runs diagonally and the edge would come out 0.7 too narrow. A miter stretches it by 1 / cos(half the turn),
    # which the angle between the vertex normal and the normal of a face at the vertex gives.
    corner = nodes.new('GeometryNodeCornersOfVertex')
    corner.inputs['Sort Index'].default_value = 0
    links.new(nodes.new('GeometryNodeInputIndex').outputs[0], corner.inputs['Vertex Index'])
    of_corner = nodes.new('GeometryNodeFaceOfCorner')
    links.new(corner.outputs['Corner Index'], of_corner.inputs['Corner Index'])
    face_normal = nodes.new('GeometryNodeSampleIndex')
    face_normal.data_type = 'FLOAT_VECTOR'
    face_normal.domain = 'FACE'
    links.new(subdivide.outputs[0], face_normal.inputs['Geometry'])
    links.new(nodes.new('GeometryNodeInputNormal').outputs[0], face_normal.inputs['Value'])
    links.new(of_corner.outputs['Face Index'], face_normal.inputs['Index'])
    face_flat = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'SUBTRACT', face_normal.outputs[0], _vector(
        nodes, links, 'SCALE', ground_normal, scale=_vector(nodes, links, 'DOT_PRODUCT', face_normal.outputs[0], ground_normal))))
    miter = _math(nodes, links, 'DIVIDE', 1.0, _math(nodes, links, 'MAXIMUM',
                  _vector(nodes, links, 'DOT_PRODUCT', outward, face_flat), 0.7))  # up to a right-angle corner

    # u: 0 on the contact (the spread edge), 1 at the original edge. a: how much of Spread, b: how much of the wall.
    u = _math(nodes, links, 'DIVIDE', wall_length, _math(nodes, links, 'MAXIMUM', height, 1e-6), clamp=True)
    v = _math(nodes, links, 'SUBTRACT', 1.0, u)
    weight = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'DIVIDE',
                   _math(nodes, links, 'MINIMUM', gin.outputs[PROFILE], 0.999),
                   _math(nodes, links, 'SUBTRACT', 1.0, _math(nodes, links, 'MINIMUM', gin.outputs[PROFILE], 0.999))), 0.7071)
    denominator = _math(nodes, links, 'ADD', _math(nodes, links, 'ADD', _math(nodes, links, 'MULTIPLY', u, u),
                        _math(nodes, links, 'MULTIPLY', v, v)),
                        _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', weight, 2.0),
                              _math(nodes, links, 'MULTIPLY', u, v)))
    denominator = _math(nodes, links, 'MAXIMUM', denominator, 1e-6)
    round_b = _math(nodes, links, 'DIVIDE', _math(nodes, links, 'MULTIPLY', u, u), denominator)
    round_a = _math(nodes, links, 'DIVIDE', _math(nodes, links, 'MULTIPLY', v, v), denominator)

    curve = nodes.new('ShaderNodeFloatCurve')
    curve.name = curve.label = CURVE_NODE
    curve.inputs['Factor'].default_value = 1.0
    links.new(u, curve.inputs['Value'])
    points = curve.mapping.curves[0].points
    for i, (x, y) in enumerate(DEFAULT_CURVE):
        knot = points[i] if i < len(points) else points.new(x, y)
        knot.location = (x, y)
        knot.handle_type = 'AUTO'
    curve.mapping.update()

    def is_falloff(value):
        return _math(nodes, links, 'COMPARE', gin.outputs[FALLOFF], float(value), 0.1)

    along = _switch(nodes, links, 'FLOAT', is_falloff(CUSTOM), _switch(
        nodes, links, 'FLOAT', is_falloff(LINEAR), round_a, v), curve.outputs[0])
    up = _switch(nodes, links, 'FLOAT', is_falloff(CUSTOM), _switch(
        nodes, links, 'FLOAT', is_falloff(LINEAR), round_b, u), u)

    # the new position: on the tangent plane of the contact, then dropped onto the other mesh and lifted
    scale = _math(nodes, links, 'DIVIDE', _math(nodes, links, 'MULTIPLY', up, height),
                  _math(nodes, links, 'MAXIMUM', wall_length, 1e-6))
    slid = _vector(nodes, links, 'ADD', _vector(nodes, links, 'ADD', landmark, _vector(
        nodes, links, 'SCALE', outward, scale=_math(nodes, links, 'MULTIPLY', _math(
            nodes, links, 'MULTIPLY', along, spread_len), miter))),
        _vector(nodes, links, 'SCALE', wall_flat, scale=scale))
    # Drop it onto the other mesh along the ground normal: a ray, unlike the nearest point, does not pile the points
    # up on the seams between the facets of a curved mesh (that made a scalloped spread edge)
    margin = _math(nodes, links, 'ADD', _math(nodes, links, 'ADD', height, spread_len), 0.01)
    ray = nodes.new('GeometryNodeRaycast')
    links.new(other, ray.inputs['Target Geometry'])
    links.new(_vector(nodes, links, 'ADD', slid, _vector(nodes, links, 'SCALE', ground_normal, scale=margin)),
              ray.inputs['Source Position'])
    links.new(_vector(nodes, links, 'SCALE', ground_normal, scale=-1.0), ray.inputs['Ray Direction'])
    links.new(_math(nodes, links, 'MULTIPLY', margin, 2.0), ray.inputs['Ray Length'])
    landed = _switch(nodes, links, 'VECTOR', ray.outputs['Is Hit'], slid, ray.outputs['Hit Position'])
    landing_normal = nodes.new('GeometryNodeSampleNearestSurface')
    landing_normal.data_type = 'FLOAT_VECTOR'
    links.new(other, landing_normal.inputs['Mesh'])
    links.new(nodes.new('GeometryNodeInputNormal').outputs[0], landing_normal.inputs['Value'])
    links.new(landed, landing_normal.inputs['Sample Position'])
    raise_by = _math(nodes, links, 'ADD', _math(nodes, links, 'MULTIPLY', wall_up, scale),
                     _math(nodes, links, 'MULTIPLY', along, gin.outputs[LIFT]))
    moved = _vector(nodes, links, 'ADD', landed, _vector(
        nodes, links, 'SCALE', landing_normal.outputs['Value'], scale=raise_by))
    inside = _math(nodes, links, 'LESS_THAN', wall_length, height)
    target = _switch(nodes, links, 'VECTOR', inside, position, moved)

    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(subdivide.outputs[0], set_position.inputs['Geometry'])
    links.new(target, set_position.inputs['Position'])

    # no contact (the shapes do not touch) or nothing to spread: the shape is only cut
    count = nodes.new('GeometryNodeAttributeDomainSize')
    count.component = 'MESH'
    links.new(contact.outputs['Selection'], count.inputs[0])
    touching = _math(nodes, links, 'GREATER_THAN', count.outputs['Point Count'], 0.5)
    active = _math(nodes, links, 'MULTIPLY', touching, _math(nodes, links, 'GREATER_THAN', height, 1e-6))
    result = _switch(nodes, links, 'GEOMETRY', active, subdivide.outputs[0], set_position.outputs[0])
    links.new(joins.smooth_by_angle(nodes, links, result, gin.outputs[SMOOTH_ANGLE]), gout.inputs[0])


def _build_prep(ng):
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape this one merges onto")
    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])
    union = joins._union(nodes, links, gin.outputs['Geometry'], info.outputs['Geometry'])
    clean = nodes.new('GeometryNodeMergeByDistance')  # the exact union can leave zero-length edges: Bevel clamps to 0 on them
    clean.inputs['Distance'].default_value = 1e-5
    links.new(union.outputs['Mesh'], clean.inputs['Geometry'])
    weights = nodes.new('GeometryNodeStoreNamedAttribute')
    weights.data_type = 'FLOAT'
    weights.domain = 'EDGE'
    weights.inputs['Name'].default_value = "bevel_weight_edge"
    links.new(clean.outputs['Geometry'], weights.inputs['Geometry'])
    links.new(union.outputs['Intersecting Edges'], weights.inputs['Value'])
    links.new(weights.outputs['Geometry'], gout.inputs[0])


def _build_finish(ng):
    """Drop what lies on the other mesh: its own faces, and the flat last row of the bevel."""
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape this one merges onto")
    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])
    on_base = nodes.new('GeometryNodeProximity')
    on_base.target_element = 'FACES'
    links.new(info.outputs['Geometry'], on_base.inputs['Target'])
    # (a face center that lies on the other mesh, within a hair of its size)
    box = nodes.new('GeometryNodeBoundBox')
    links.new(gin.outputs['Geometry'], box.inputs[0])
    span = nodes.new('ShaderNodeVectorMath')
    span.operation = 'DISTANCE'
    links.new(box.outputs['Min'], span.inputs[0])
    links.new(box.outputs['Max'], span.inputs[1])
    tolerance = _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'MULTIPLY', span.outputs['Value'], 1e-5), 1e-7)
    flat = _math(nodes, links, 'LESS_THAN', on_base.outputs['Distance'], tolerance)
    dropped = nodes.new('GeometryNodeDeleteGeometry')
    dropped.domain = 'FACE'
    links.new(gin.outputs['Geometry'], dropped.inputs['Geometry'])
    links.new(flat, dropped.inputs['Selection'])
    links.new(joins.smooth_by_angle(nodes, links, dropped.outputs[0]), gout.inputs[0])


def _build_rows_prep(ng):
    """Cut the shape with the other mesh and give the cut loop a Bevel weight (the cap stays: the loop is an edge)."""
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape this one merges onto")
    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])
    difference = nodes.new('GeometryNodeMeshBoolean')
    difference.operation = 'DIFFERENCE'
    if hasattr(difference, "solver"):
        difference.solver = 'EXACT'
    links.new(gin.outputs['Geometry'], difference.inputs[0])
    links.new(info.outputs['Geometry'], joins._multi_input(difference))
    clean = nodes.new('GeometryNodeMergeByDistance')
    clean.inputs['Distance'].default_value = 1e-5
    links.new(difference.outputs['Mesh'], clean.inputs['Geometry'])
    weights = nodes.new('GeometryNodeStoreNamedAttribute')
    weights.data_type = 'FLOAT'
    weights.domain = 'EDGE'
    weights.inputs['Name'].default_value = "bevel_weight_edge"
    links.new(clean.outputs['Geometry'], weights.inputs['Geometry'])
    links.new(difference.outputs['Intersecting Edges'], weights.inputs['Value'])
    links.new(weights.outputs['Geometry'], gout.inputs[0])
