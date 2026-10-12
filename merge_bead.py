"""Original contact-ribbon builder; both source meshes remain undeformed."""

import math

import bpy

from . import joins
from . import shapes as cn
from .merge_settings import *
from .merge_nodes import (
    _math, _vector, _switch, _smooth_normals, _surface_normal_sampler,
    _reset_bead_mapping,
)


def _cleanup_bead_boundary(nodes, links, geometry, parameter, width):
    """Remove coincident boundary vertices and vanishing bead faces only.

    This is not a Boolean union or a general self-intersection solver. Keep
    the two borders separate, and run before joining the original mesh.
    """
    tolerance = _math(nodes, links, 'MULTIPLY', width, 1e-5)
    for selection in (_math(nodes, links, 'LESS_THAN', parameter, 1e-6),
                      _math(nodes, links, 'GREATER_THAN', parameter, 1.0 - 1e-6)):
        weld = nodes.new('GeometryNodeMergeByDistance')
        if hasattr(weld, 'mode'):
            weld.mode = 'ALL'
        elif 'Mode' in weld.inputs:
            weld.inputs['Mode'].default_value = 'All'
        links.new(geometry, weld.inputs['Geometry'])
        links.new(selection, weld.inputs['Selection'])
        links.new(tolerance, weld.inputs['Distance'])
        geometry = weld.outputs['Geometry']
    cleanup = nodes.new('GeometryNodeDeleteGeometry')
    cleanup.domain = 'FACE'
    cleanup.mode = 'ALL'
    links.new(geometry, cleanup.inputs['Geometry'])
    area = nodes.new('GeometryNodeInputMeshFaceArea').outputs[0]
    links.new(_math(nodes, links, 'LESS_THAN', area,
                   _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', width, width), 1e-12)),
              cleanup.inputs['Selection'])
    return cleanup.outputs['Geometry']


def _build_bead(ng):
    """A bounded, surface-tangent fillet along the exact contact of A and B.

    Boolean intersecting edges supply the contact curve. A ribbon is swept
    along it, with both profile endpoints projected onto the source surfaces.
    Two cubic halves follow the landed surface tangents and meet at an
    independently controlled center height. Both original meshes keep their topology.
    """
    ng.interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject',
                            description="The shape this one merges onto")
    cn._new_input(ng, GLOBAL, 'NodeSocketFloat', 1.0, 0.0, 100.0,
                  "Scales Contact Width and Contact Height together (0 turns the bead off)")
    cn._new_input(ng, BEAD_WIDTH, 'NodeSocketFloat', DEFAULT_BEAD_RADIUS, 0.0, 1000.0,
                  "Reach of the bead along each contact tangent plane, before surface projection")
    cn._new_input(ng, BEAD_HEIGHT, 'NodeSocketFloat', DEFAULT_BEAD_RADIUS * 0.42, 0.0, 1000.0,
                  "Center height above the contact along the outward bisector; surface clearance is enforced")
    cn._new_input(ng, BEAD_CUSTOM_CURVE, 'NodeSocketBool', False, 0, 1,
                  "Shape both shoulders with the custom curve; borders, center height and endpoint tangency stay fixed")
    cn._new_input(ng, BEAD_SYNC_NORMALS, 'NodeSocketBool', True, 0, 1,
                  "Continuously match bead boundary shading to both source surfaces (Blender 4.5+)")
    cn._new_input(ng, BEAD_PROFILE_RESOLUTION, 'NodeSocketInt', DEFAULT_BEAD_RESOLUTION, MIN_BEAD_RESOLUTION, MAX_BEAD_RESOLUTION,
                  "Points along the bead cross-section (more is smoother and heavier)")
    cn._new_input(ng, BEAD_SEAM_SAMPLES, 'NodeSocketInt', 2, 0, 4,
                  "Extra samples along each contact edge (smoother curved boundaries; 0 is faster)")
    cn._new_input(ng, BEAD_SURFACE_SUBDIVISION, 'NodeSocketBool', False, 0, 1,
                  "Subdivide only the bead surface in both seam and profile directions; source meshes stay unchanged")
    cn._new_input(ng, BEAD_SURFACE_LEVELS, 'NodeSocketInt', 2, 0, 3,
                  "Subdivision levels: each level multiplies bead faces by four; lower for interactive editing")
    cn._new_input(ng, BEAD_SUBDIVISION_ORDER, 'NodeSocketInt', AFTER_SNAP, AFTER_SNAP, BEFORE_SNAP,
                  "0: smooth after snapping; 1: subdivide first, then fit all samples to the surfaces")
    ng.interface.new_socket(name=BEAD_MATERIAL, in_out='INPUT', socket_type='NodeSocketMaterial',
                            description="Bead material (initially the merged object's active material)")

    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])

    # Exact UNION so Intersecting Edges fires; the mesh output is thrown away (we only want the edges).
    bo = nodes.new('GeometryNodeMeshBoolean')
    bo.operation = 'UNION'
    if hasattr(bo, "solver"):
        bo.solver = 'EXACT'
    links.new(gin.outputs['Geometry'], joins._multi_input(bo))
    links.new(info.outputs['Geometry'], joins._multi_input(bo))

    # Keep only the intersecting edges and stitch them into a polyline
    edges = nodes.new('GeometryNodeSeparateGeometry')
    edges.domain = 'EDGE'
    links.new(bo.outputs['Mesh'], edges.inputs['Geometry'])
    links.new(bo.outputs['Intersecting Edges'], edges.inputs['Selection'])
    to_curve = nodes.new('GeometryNodeMeshToCurve')
    links.new(edges.outputs['Selection'], to_curve.inputs['Mesh'])
    contact_subdivide = nodes.new('GeometryNodeSubdivideCurve')
    links.new(to_curve.outputs['Curve'], contact_subdivide.inputs['Curve'])
    links.new(gin.outputs[BEAD_SEAM_SAMPLES], contact_subdivide.inputs['Cuts'])

    # Build a stable profile frame from the two surface normals: the outward
    # bisector and their difference. With cos(2β) = n_A · n_B, the provisional
    # endpoints are Width * (sinβ * bisector ± cosβ * binormal), each in its
    # source tangent plane. The center is independently Height * bisector.
    # Store the frame on the contact curve for the swept profile vertices.
    # Shading flags alone do not give interpolated corner normals in Blender
    # 4.2. Build an explicit angle-filtered corner field on the original meshes,
    # then interpolate it at the contact points (not on the Boolean result).
    shaded_a = _smooth_normals(nodes, links, gin.outputs['Geometry'], explicit_faces=True)
    shaded_b = _smooth_normals(nodes, links, info.outputs['Geometry'], explicit_faces=True)
    contact_position = nodes.new('GeometryNodeInputPosition').outputs[0]
    def sample_normal(mesh):
        s = _surface_normal_sampler(nodes, links, mesh)
        links.new(contact_position, s.inputs['Sample Position'])
        # Interpolation of unit corner normals need not have unit length.
        return _vector(nodes, links, 'NORMALIZE', s.outputs['Value'])
    n_a = sample_normal(shaded_a)
    n_b = sample_normal(shaded_b)
    seam_tangent = nodes.new('GeometryNodeInputTangent').outputs[0]
    summed = _vector(nodes, links, 'ADD', n_a, n_b)
    original_bisector = _vector(nodes, links, 'NORMALIZE', summed)
    projected_sum = _vector(nodes, links, 'SUBTRACT', summed,
                            _vector(nodes, links, 'SCALE', seam_tangent,
                                    scale=_vector(nodes, links, 'DOT_PRODUCT', summed, seam_tangent)))
    corrected_bisector = _vector(nodes, links, 'NORMALIZE', projected_sum)
    statistic = nodes.new('GeometryNodeAttributeStatistic')
    statistic.data_type = 'FLOAT'
    statistic.domain = 'POINT'
    links.new(contact_subdivide.outputs['Curve'], statistic.inputs['Geometry'])
    links.new(_vector(nodes, links, 'DOT_PRODUCT', n_a, n_b), statistic.inputs['Attribute'])
    def stable_frame(original, corrected, alignment):
        # Cross-section projection is ill-conditioned for shallow contacts.
        # Blend over a band around cos(45°), using the whole contact set so
        # adjacent rows cannot choose incompatible frames. Smoothstep prevents
        # a hard shape jump while moving either object through this band.
        u = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'SUBTRACT', alignment, 0.68), 20.0, clamp=True)
        weight = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', u, u),
                       _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', u, 2.0)))
        return _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'ADD',
                        _vector(nodes, links, 'SCALE', original, scale=weight),
                        _vector(nodes, links, 'SCALE', corrected,
                                scale=_math(nodes, links, 'SUBTRACT', 1.0, weight))))

    bisector = stable_frame(original_bisector, corrected_bisector, statistic.outputs['Mean'])
    # Anchor the transverse sign to A/B normals, not Boolean curve direction.
    difference = _vector(nodes, links, 'SUBTRACT', n_a, n_b)
    transverse = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'CROSS_PRODUCT', seam_tangent, bisector))
    reversed_frame = _math(nodes, links, 'LESS_THAN',
                           _vector(nodes, links, 'DOT_PRODUCT', transverse, difference), 0.0)
    binormal = _switch(nodes, links, 'VECTOR', reversed_frame, transverse,
                       _vector(nodes, links, 'SCALE', transverse, scale=-1.0))
    binormal = stable_frame(_vector(nodes, links, 'NORMALIZE', difference), binormal, statistic.outputs['Mean'])
    # β from normals; keep the cosine nonzero near opposed surface normals.
    dot_ab = _math(nodes, links, 'MAXIMUM',
                   _math(nodes, links, 'MINIMUM', _vector(nodes, links, 'DOT_PRODUCT', n_a, n_b), 1.0), -1.0)
    cos_beta = _math(nodes, links, 'MAXIMUM',
                     _math(nodes, links, 'SQRT', _math(nodes, links, 'MULTIPLY',
                           _math(nodes, links, 'ADD', 1.0, dot_ab), 0.5)),
                     1e-3)
    sin_beta = _math(nodes, links, 'SQRT', _math(nodes, links, 'MULTIPLY',
                     _math(nodes, links, 'SUBTRACT', 1.0, dot_ab), 0.5))
    # Orient the curve's own Normal to the bisector (keeps Curve to Mesh's frame stable for later attribute
    # propagation; the Set Position below is what actually places every vertex, so this is cosmetic).
    oriented = nodes.new('GeometryNodeSetCurveNormal')
    if hasattr(oriented, "mode"):
        oriented.mode = 'FREE'                                             # 4.x: node attribute
    elif "Mode" in oriented.inputs:
        oriented.inputs["Mode"].default_value = 'Free'                     # 5.x: menu input socket
    links.new(contact_subdivide.outputs['Curve'], oriented.inputs['Curve'])
    links.new(bisector, oriented.inputs['Normal'])

    # Store the per-point frame on the curve so Curve to Mesh carries it to every swept vertex.
    curve_pos = nodes.new('GeometryNodeInputPosition').outputs[0]
    stored = oriented.outputs['Curve']
    for name, value, dtype in ((BEAD_BISECTOR, bisector, 'FLOAT_VECTOR'),
                               (BEAD_BINORMAL, binormal, 'FLOAT_VECTOR'),
                               (BEAD_NORMAL_A, n_a, 'FLOAT_VECTOR'),
                               (BEAD_CURVE_POS, curve_pos, 'FLOAT_VECTOR'),
                               (BEAD_SIN_BETA, sin_beta, 'FLOAT'),
                               (BEAD_CONTACT_ALIGNMENT, statistic.outputs['Mean'], 'FLOAT'),
                               (BEAD_SEAM_TANGENT, seam_tangent, 'FLOAT_VECTOR'),
                               (BEAD_COS_BETA, cos_beta, 'FLOAT')):
        store = nodes.new('GeometryNodeStoreNamedAttribute')
        store.data_type = dtype
        store.domain = 'POINT'
        store.inputs['Name'].default_value = name
        links.new(stored, store.inputs['Geometry'])
        links.new(value, store.inputs['Value'])
        stored = store.outputs[0]

    # Build the sweep profile as a straight line resampled to Resolution points, carrying a per-point parameter
    # t in [0, 1] along it. Curve to Mesh gives a ribbon mesh of (contact_points × Resolution) vertices; every
    # vertex ends up with the curve-point frame AND its own t. Set Position below reshapes the ribbon into the
    # independently sized per-point profile.
    line = nodes.new('GeometryNodeCurvePrimitiveLine')
    line.mode = 'POINTS'
    line.inputs['Start'].default_value = (0.0, 0.0, 0.0)
    line.inputs['End'].default_value = (0.0, 1.0, 0.0)
    resample = nodes.new('GeometryNodeResampleCurve')
    if hasattr(resample, "mode"):              # 4.x: node attribute
        resample.mode = 'COUNT'
    elif "Mode" in resample.inputs:            # 5.x: menu input socket (default is already Count, but set it anyway)
        resample.inputs["Mode"].default_value = 'Count'
    links.new(line.outputs['Curve'], resample.inputs['Curve'])
    links.new(gin.outputs[BEAD_PROFILE_RESOLUTION], resample.inputs['Count'])
    arc_t_store = nodes.new('GeometryNodeStoreNamedAttribute')
    arc_t_store.data_type = 'FLOAT'
    arc_t_store.domain = 'POINT'
    arc_t_store.inputs['Name'].default_value = BEAD_ARC_T
    links.new(resample.outputs['Curve'], arc_t_store.inputs['Geometry'])
    # t = profile point's y coordinate (the line runs from (0,0,0) to (0,1,0))
    sep = nodes.new('ShaderNodeSeparateXYZ')
    links.new(nodes.new('GeometryNodeInputPosition').outputs[0], sep.inputs[0])
    links.new(sep.outputs['Y'], arc_t_store.inputs['Value'])

    sweep = nodes.new('GeometryNodeCurveToMesh')
    links.new(stored, sweep.inputs['Curve'])
    links.new(arc_t_store.outputs[0], sweep.inputs['Profile Curve'])
    sweep.inputs['Fill Caps'].default_value = False     # the contact line is a closed loop (or several)

    # Read the carried frame and profile parameter, land the endpoints, then
    # fit a surface-tangent cubic cross-section between them.
    radius = _math(nodes, links, 'MULTIPLY', gin.outputs[BEAD_WIDTH], gin.outputs[GLOBAL])
    safe_radius = _math(nodes, links, 'MAXIMUM', radius, 1e-6)
    profile_height = _math(nodes, links, 'MAXIMUM',
                           _math(nodes, links, 'MULTIPLY', gin.outputs[BEAD_HEIGHT], gin.outputs[GLOBAL]), 1e-6)
    def read_attribute(name, dtype):
        attr = nodes.new('GeometryNodeInputNamedAttribute')
        attr.data_type = dtype
        attr.inputs['Name'].default_value = name
        return attr.outputs['Attribute']
    bi = _vector(nodes, links, 'NORMALIZE', read_attribute(BEAD_BISECTOR, 'FLOAT_VECTOR'))
    bn = _vector(nodes, links, 'NORMALIZE', read_attribute(BEAD_BINORMAL, 'FLOAT_VECTOR'))
    tangent = _vector(nodes, links, 'NORMALIZE', read_attribute(BEAD_SEAM_TANGENT, 'FLOAT_VECTOR'))
    fitted_bi = _vector(nodes, links, 'NORMALIZE',
                        _vector(nodes, links, 'SUBTRACT', bi, _vector(nodes, links, 'SCALE', tangent,
                                scale=_vector(nodes, links, 'DOT_PRODUCT', bi, tangent))))
    fitted_bn = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'CROSS_PRODUCT', tangent, fitted_bi))
    fitted_bn = _switch(nodes, links, 'VECTOR',
                        _math(nodes, links, 'LESS_THAN', _vector(nodes, links, 'DOT_PRODUCT', fitted_bn, bn), 0.0),
                        fitted_bn, _vector(nodes, links, 'SCALE', fitted_bn, scale=-1.0))
    bi = stable_frame(bi, fitted_bi, read_attribute(BEAD_CONTACT_ALIGNMENT, 'FLOAT'))
    bn = stable_frame(bn, fitted_bn, read_attribute(BEAD_CONTACT_ALIGNMENT, 'FLOAT'))
    cp = read_attribute(BEAD_CURVE_POS, 'FLOAT_VECTOR')
    sb = read_attribute(BEAD_SIN_BETA, 'FLOAT')
    cb = read_attribute(BEAD_COS_BETA, 'FLOAT')
    at = read_attribute(BEAD_ARC_T, 'FLOAT')
    subdivision_level = _switch(nodes, links, 'INT', gin.outputs[BEAD_SURFACE_SUBDIVISION],
                                0, gin.outputs[BEAD_SURFACE_LEVELS])
    before_snap = _math(nodes, links, 'GREATER_THAN', gin.outputs[BEAD_SUBDIVISION_ORDER], 0.5)
    border = _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'LESS_THAN', at, 1e-6),
                   _math(nodes, links, 'GREATER_THAN', at, 1.0 - 1e-6))

    def subdivide(mesh, level, name):
        node = nodes.new('GeometryNodeSubdivisionSurface')
        node.name = node.label = name
        links.new(mesh, node.inputs['Mesh'])
        links.new(level, node.inputs['Level'])
        # Preserve the two attachment boundaries, not all interior edges.
        links.new(border, node.inputs['Edge Crease'])
        links.new(border, node.inputs['Vertex Crease'])
        if 'Limit Surface' in node.inputs:
            node.inputs['Limit Surface'].default_value = False
        return node.outputs['Mesh']

    prepared = subdivide(sweep.outputs['Mesh'],
                         _switch(nodes, links, 'INT', before_snap, 0, subdivision_level), 'Before Snap')
    # Width specifies tangent reach directly instead of the radius of a circle,
    # so changing the contact angle cannot amplify it through tan(beta).
    end_x = _math(nodes, links, 'MULTIPLY', safe_radius, sb)
    end_y = _math(nodes, links, 'MULTIPLY', safe_radius, cb)
    end_center = _vector(nodes, links, 'ADD', cp, _vector(nodes, links, 'SCALE', bi, scale=end_x))
    positive = _vector(nodes, links, 'ADD', end_center, _vector(nodes, links, 'SCALE', bn, scale=end_y))
    negative = _vector(nodes, links, 'SUBTRACT', end_center, _vector(nodes, links, 'SCALE', bn, scale=end_y))
    # Curve direction can reverse: identify A's end using its normal rather
    # than assuming that t=0 always belongs to A.
    normal_a = read_attribute(BEAD_NORMAL_A, 'FLOAT_VECTOR')
    positive_is_b = _math(nodes, links, 'GREATER_THAN',
                          _vector(nodes, links, 'DOT_PRODUCT', bn, normal_a), 0.0)
    # Freeze endpoint ownership before fitting. Later subdivision, welding,
    # and frame normalization must not switch an already attached border.
    ownership = nodes.new('GeometryNodeStoreNamedAttribute')
    ownership.data_type = 'BOOLEAN'
    ownership.domain = 'POINT'
    ownership.inputs['Name'].default_value = BEAD_POSITIVE_IS_B
    links.new(prepared, ownership.inputs['Geometry'])
    links.new(positive_is_b, ownership.inputs['Value'])
    prepared = ownership.outputs['Geometry']
    positive_is_b = read_attribute(BEAD_POSITIVE_IS_B, 'BOOLEAN')
    end_a = _switch(nodes, links, 'VECTOR', positive_is_b, positive, negative)
    end_b = _switch(nodes, links, 'VECTOR', positive_is_b, negative, positive)

    def endpoint_landing(mesh, smooth_mesh, endpoint):
        proximity = nodes.new('GeometryNodeProximity')
        proximity.target_element = 'FACES'
        links.new(mesh, proximity.inputs['Target'])
        links.new(endpoint, proximity.inputs['Source Position'])
        landed = proximity.outputs['Position']
        sampler = _surface_normal_sampler(nodes, links, smooth_mesh)
        links.new(landed, sampler.inputs['Sample Position'])
        return landed, _vector(nodes, links, 'NORMALIZE', sampler.outputs['Value'])

    landed_a, landing_a_normal = endpoint_landing(gin.outputs['Geometry'], shaded_a, end_a)
    landed_b, landing_b_normal = endpoint_landing(info.outputs['Geometry'], shaded_b, end_b)
    start = _switch(nodes, links, 'VECTOR', positive_is_b, landed_a, landed_b)
    end = _switch(nodes, links, 'VECTOR', positive_is_b, landed_b, landed_a)
    start_normal = _switch(nodes, links, 'VECTOR', positive_is_b, landing_a_normal, landing_b_normal)
    end_normal = _switch(nodes, links, 'VECTOR', positive_is_b, landing_b_normal, landing_a_normal)
    chord = _vector(nodes, links, 'SUBTRACT', end, start)

    def plane_tangent(direction, normal):
        projected = _vector(nodes, links, 'SUBTRACT', direction,
                            _vector(nodes, links, 'SCALE', normal,
                                    scale=_vector(nodes, links, 'DOT_PRODUCT', direction, normal)))
        return _vector(nodes, links, 'NORMALIZE', projected)
    # Two cubic halves share an explicitly positioned center and tangent.
    # Unlike one width-scaled circular/cubic arc, the center stays at Height
    # when Width changes. Only necessary collision clearance can raise it.
    center = _vector(nodes, links, 'ADD', cp, _vector(nodes, links, 'SCALE', bi, scale=profile_height))
    to_center = _vector(nodes, links, 'SUBTRACT', center, start)
    from_center = _vector(nodes, links, 'SUBTRACT', end, center)
    center_tangent = plane_tangent(chord, bi)
    start_handle = _math(nodes, links, 'MULTIPLY', _vector(nodes, links, 'LENGTH', to_center), 0.4)
    end_handle = _math(nodes, links, 'MULTIPLY', _vector(nodes, links, 'LENGTH', from_center), 0.4)
    center_handle = _math(nodes, links, 'MINIMUM',
                          _math(nodes, links, 'MULTIPLY', _vector(nodes, links, 'LENGTH', chord), 0.2),
                          _math(nodes, links, 'MULTIPLY',
                                _math(nodes, links, 'DIVIDE', _math(nodes, links, 'MULTIPLY', profile_height, cb),
                                      _math(nodes, links, 'MAXIMUM', sb, 0.01)), 0.5))
    control_start = _vector(nodes, links, 'ADD', start,
                            _vector(nodes, links, 'SCALE', plane_tangent(to_center, start_normal), scale=start_handle))
    control_end = _vector(nodes, links, 'SUBTRACT', end,
                          _vector(nodes, links, 'SCALE', plane_tangent(from_center, end_normal), scale=end_handle))
    center_before = _vector(nodes, links, 'SUBTRACT', center,
                            _vector(nodes, links, 'SCALE', center_tangent, scale=center_handle))
    center_after = _vector(nodes, links, 'ADD', center,
                           _vector(nodes, links, 'SCALE', center_tangent, scale=center_handle))
    half_t = _math(nodes, links, 'MULTIPLY', at, 2.0)

    def cubic(p0, p1, p2, p3, parameter):
        inv = _math(nodes, links, 'SUBTRACT', 1.0, parameter)
        square = _math(nodes, links, 'MULTIPLY', parameter, parameter)
        inv_square = _math(nodes, links, 'MULTIPLY', inv, inv)
        weights = (_math(nodes, links, 'MULTIPLY', inv_square, inv),
                   _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', inv_square, parameter), 3.0),
                   _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', inv, square), 3.0),
                   _math(nodes, links, 'MULTIPLY', square, parameter))
        terms = [_vector(nodes, links, 'SCALE', point, scale=weight)
                 for point, weight in zip((p0, p1, p2, p3), weights)]
        return _vector(nodes, links, 'ADD', _vector(nodes, links, 'ADD', terms[0], terms[1]),
                       _vector(nodes, links, 'ADD', terms[2], terms[3]))

    first_half = cubic(start, control_start, center_before, center, half_t)
    second_half = cubic(center, center_after, control_end, end, _math(nodes, links, 'SUBTRACT', half_t, 1.0))
    world = _switch(nodes, links, 'VECTOR', _math(nodes, links, 'GREATER_THAN', at, 0.5), first_half, second_half)

    # A symmetric shoulder adjustment, not just resampling the same curve.
    # x runs from the surface border (0) to the center (1). The diagonal
    # is neutral. The envelope and its derivative vanish at both ends,
    # preserving landed borders, center height, and the cubic tangents.
    shoulder_u = _math(nodes, links, 'MULTIPLY',
                       _math(nodes, links, 'MINIMUM', at, _math(nodes, links, 'SUBTRACT', 1.0, at)), 2.0)
    curve = nodes.new('ShaderNodeFloatCurve')
    curve.name = curve.label = CURVE_NODE
    curve.inputs['Factor'].default_value = 1.0
    links.new(shoulder_u, curve.inputs['Value'])
    _reset_bead_mapping(curve.mapping)
    mapped = _math(nodes, links, 'MINIMUM', _math(nodes, links, 'MAXIMUM', curve.outputs[0], 0.0), 1.0)
    envelope = _math(nodes, links, 'MULTIPLY', shoulder_u,
                     _math(nodes, links, 'SUBTRACT', 1.0, shoulder_u))
    envelope = _math(nodes, links, 'MULTIPLY',
                     _math(nodes, links, 'MULTIPLY', envelope, envelope), 16.0)
    offset = _math(nodes, links, 'MULTIPLY', profile_height,
                   _math(nodes, links, 'MULTIPLY', envelope,
                         _math(nodes, links, 'SUBTRACT', mapped, shoulder_u)))
    shaped = _vector(nodes, links, 'ADD', world, _vector(nodes, links, 'SCALE', bi, scale=offset))
    world = _switch(nodes, links, 'VECTOR', gin.outputs[BEAD_CUSTOM_CURVE], world, shaped)

    # Tangents built from interpolated normals can dip slightly through a
    # polygonal source even when both endpoints land exactly. Classify with
    # geometric FACE normals (not shading normals) and escape along the
    # outward bisector. Two bounded passes handle the two curved constraints.
    def geometric_surface(mesh):
        # Disconnect faces only in the sampling copy, so converting a FACE
        # normal to the sampling domain cannot average cap and side normals.
        split = nodes.new('GeometryNodeSplitEdges')
        links.new(mesh, split.inputs['Mesh'])
        store = nodes.new('GeometryNodeStoreNamedAttribute')
        store.data_type = 'FLOAT_VECTOR'
        store.domain = 'FACE'
        store.inputs['Name'].default_value = BEAD_GEOMETRIC_NORMAL
        links.new(split.outputs['Mesh'], store.inputs['Geometry'])
        links.new(nodes.new('GeometryNodeInputNormal').outputs[0], store.inputs['Value'])
        return store.outputs['Geometry']

    surface_a = geometric_surface(gin.outputs['Geometry'])
    surface_b = geometric_surface(info.outputs['Geometry'])

    def geometric_normal(mesh, point):
        sample = nodes.new('GeometryNodeSampleNearestSurface')
        sample.data_type = 'FLOAT_VECTOR'
        links.new(mesh, sample.inputs['Mesh'])
        links.new(read_attribute(BEAD_GEOMETRIC_NORMAL, 'FLOAT_VECTOR'), sample.inputs['Value'])
        links.new(point, sample.inputs['Sample Position'])
        return _vector(nodes, links, 'NORMALIZE', sample.outputs['Value'])

    def escape_distance(mesh, point, direction=None):
        proximity = nodes.new('GeometryNodeProximity')
        proximity.target_element = 'FACES'
        links.new(mesh, proximity.inputs['Target'])
        links.new(point, proximity.inputs['Source Position'])
        normal = geometric_normal(mesh, proximity.outputs['Position'])
        away = _vector(nodes, links, 'SUBTRACT', point, proximity.outputs['Position'])
        depth = _math(nodes, links, 'MAXIMUM', 0.0,
                      _math(nodes, links, 'MULTIPLY', _vector(nodes, links, 'DOT_PRODUCT', away, normal), -1.0))
        alignment = _math(nodes, links, 'MAXIMUM',
                          _vector(nodes, links, 'DOT_PRODUCT', bi if direction is None else direction, normal), 0.05)
        return _math(nodes, links, 'DIVIDE', depth, alignment)

    def protect_position(point):
        for _ in range(2):
            escape = _math(nodes, links, 'MAXIMUM', escape_distance(surface_a, point), escape_distance(surface_b, point))
            escape = _math(nodes, links, 'MINIMUM', escape, _math(nodes, links, 'MULTIPLY', radius, 0.5))
            point = _vector(nodes, links, 'ADD', point, _vector(nodes, links, 'SCALE', bi, scale=escape))
        return point

    world = protect_position(world)
    set_pos = nodes.new('GeometryNodeSetPosition')
    links.new(prepared, set_pos.inputs['Geometry'])
    links.new(world, set_pos.inputs['Position'])

    refined = subdivide(set_pos.outputs['Geometry'],
                        _switch(nodes, links, 'INT', before_snap, subdivision_level, 0), 'After Snap')
    # After Snap smooths the actual fitted shape rather than refitting its
    # interior. Newly interpolated boundary points still need a boundary-only
    # attachment repair; both modes receive the same intrusion safety checks.
    final_position = nodes.new('GeometryNodeInputPosition').outputs[0]
    def attach_boundary(mesh, other, point):
        # An interpolated boundary can be inside the other object near a cap
        # or sharp source edge. Escape tangentially on its OWN surface rather
        # than lifting it off that surface along the bisector.
        for _ in range(8):
            proximity = nodes.new('GeometryNodeProximity')
            proximity.target_element = 'FACES'
            links.new(mesh, proximity.inputs['Target'])
            links.new(point, proximity.inputs['Source Position'])
            point = proximity.outputs['Position']
            normal = geometric_normal(mesh, point)
            other_proximity = nodes.new('GeometryNodeProximity')
            other_proximity.target_element = 'FACES'
            links.new(other, other_proximity.inputs['Target'])
            links.new(point, other_proximity.inputs['Source Position'])
            tangent = plane_tangent(geometric_normal(other, other_proximity.outputs['Position']), normal)
            # Over-relax because reprojecting across a cap/side edge can undo
            # part of a tangent step. Every step remains bounded by half Width.
            distance = _math(nodes, links, 'MINIMUM',
                             _math(nodes, links, 'MULTIPLY', escape_distance(other, point, tangent), 3.0),
                             _math(nodes, links, 'MULTIPLY', radius, 0.5))
            point = _vector(nodes, links, 'ADD', point, _vector(nodes, links, 'SCALE', tangent, scale=distance))
        proximity = nodes.new('GeometryNodeProximity')
        proximity.target_element = 'FACES'
        links.new(mesh, proximity.inputs['Target'])
        links.new(point, proximity.inputs['Source Position'])
        return proximity.outputs['Position']

    final_a = attach_boundary(surface_a, surface_b, final_position)
    final_b = attach_boundary(surface_b, surface_a, final_position)
    start_final = _switch(nodes, links, 'VECTOR', positive_is_b, final_a, final_b)
    end_final = _switch(nodes, links, 'VECTOR', positive_is_b, final_b, final_a)
    border_final = _switch(nodes, links, 'VECTOR', _math(nodes, links, 'GREATER_THAN', at, 0.5), start_final, end_final)
    repair = nodes.new('GeometryNodeSetPosition')
    links.new(refined, repair.inputs['Geometry'])
    repaired_position = _switch(nodes, links, 'VECTOR', border, protect_position(final_position), border_final)
    links.new(repaired_position, repair.inputs['Position'])
    refined = _switch(nodes, links, 'GEOMETRY',
                      _math(nodes, links, 'GREATER_THAN', subdivision_level, 0.5),
                      set_pos.outputs['Geometry'], repair.outputs['Geometry'])
    # Subdivision interpolates vector attributes; restore their unit length.
    for name, value in ((BEAD_BISECTOR, bi), (BEAD_BINORMAL, bn)):
        frame_store = nodes.new('GeometryNodeStoreNamedAttribute')
        frame_store.data_type = 'FLOAT_VECTOR'
        frame_store.domain = 'POINT'
        frame_store.inputs['Name'].default_value = name
        links.new(refined, frame_store.inputs['Geometry'])
        links.new(value, frame_store.inputs['Value'])
        refined = frame_store.outputs['Geometry']

    # An outside vertex test alone misses polygons spanning through a curved
    # source between their corners. Sample face centers too and lift only the
    # interior profile rows, keeping the landed boundary rows fixed.
    guarded = refined
    for _ in range(2):
        face_position = nodes.new('GeometryNodeInputPosition').outputs[0]
        face_escape = _math(nodes, links, 'MAXIMUM',
                            escape_distance(surface_a, face_position), escape_distance(surface_b, face_position))
        stored_escape = nodes.new('GeometryNodeStoreNamedAttribute')
        stored_escape.data_type = 'FLOAT'
        stored_escape.domain = 'FACE'
        stored_escape.inputs['Name'].default_value = BEAD_FACE_ESCAPE
        links.new(guarded, stored_escape.inputs['Geometry'])
        links.new(face_escape, stored_escape.inputs['Value'])
        maximum = None
        for rank in range(4):  # A swept quad ribbon has at most four incident faces.
            around = nodes.new('GeometryNodeCornersOfVertex')
            links.new(nodes.new('GeometryNodeInputIndex').outputs[0], around.inputs['Vertex Index'])
            around.inputs['Sort Index'].default_value = rank
            face = nodes.new('GeometryNodeFaceOfCorner')
            links.new(around.outputs['Corner Index'], face.inputs['Corner Index'])
            sample = nodes.new('GeometryNodeSampleIndex')
            sample.data_type = 'FLOAT'
            sample.domain = 'FACE'
            links.new(stored_escape.outputs['Geometry'], sample.inputs['Geometry'])
            links.new(read_attribute(BEAD_FACE_ESCAPE, 'FLOAT'), sample.inputs['Value'])
            links.new(face.outputs['Face Index'], sample.inputs['Index'])
            exists = _math(nodes, links, 'LESS_THAN', float(rank), around.outputs['Total'])
            value = _math(nodes, links, 'MULTIPLY', exists, sample.outputs['Value'])
            maximum = value if maximum is None else _math(nodes, links, 'MAXIMUM', maximum, value)
        interior = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'GREATER_THAN', at, 1e-6),
                         _math(nodes, links, 'LESS_THAN', at, 1.0 - 1e-6))
        # A boundary face has only two movable corners, hence >2x correction.
        lift = _math(nodes, links, 'MULTIPLY', interior,
                     _math(nodes, links, 'MINIMUM', _math(nodes, links, 'MULTIPLY', maximum, 2.5),
                           _math(nodes, links, 'MULTIPLY', radius, 0.5)))
        raised = nodes.new('GeometryNodeSetPosition')
        links.new(stored_escape.outputs['Geometry'], raised.inputs['Geometry'])
        links.new(_vector(nodes, links, 'SCALE', bi, scale=lift), raised.inputs['Offset'])
        guarded = raised.outputs['Geometry']

    guarded = _cleanup_bead_boundary(nodes, links, guarded, at, safe_radius)
    # Welding a collapsed border averages its point attributes as well. Keep
    # the shading frame normalized after that final topology operation.
    for name in (BEAD_BISECTOR, BEAD_BINORMAL):
        frame_store = nodes.new('GeometryNodeStoreNamedAttribute')
        frame_store.data_type = 'FLOAT_VECTOR'
        frame_store.domain = 'POINT'
        frame_store.inputs['Name'].default_value = name
        links.new(guarded, frame_store.inputs['Geometry'])
        links.new(_vector(nodes, links, 'NORMALIZE', read_attribute(name, 'FLOAT_VECTOR')), frame_store.inputs['Value'])
        guarded = frame_store.outputs['Geometry']

    # The contact curve's direction controls sweep winding, whereas our frame
    # is tied to A/B normals. Fix winding BEFORE shading or setting custom
    # normals: otherwise the ribbon's center normal points into the objects.
    flipped = nodes.new('GeometryNodeFlipFaces')
    links.new(guarded, flipped.inputs['Mesh'])
    inward = _math(nodes, links, 'LESS_THAN',
                   _vector(nodes, links, 'DOT_PRODUCT', nodes.new('GeometryNodeInputNormal').outputs[0], bi), 0.0)
    links.new(inward, flipped.inputs['Selection'])

    # The whole ribbon is a smooth surface, even where low resolution makes
    # neighboring polygons differ by more than the usual sharp-edge threshold.
    beaded = joins.smooth_by_angle(nodes, links, flipped.outputs['Mesh'], math.pi)

    # Only the outer quarter of each profile is influenced by its source.
    # Cubic weights reach 1 at the edge and 0 with zero slope in the interior.
    def boundary_weight(parameter):
        u = _math(nodes, links, 'SUBTRACT', 1.0,
                  _math(nodes, links, 'MULTIPLY', parameter, 4.0), clamp=True)
        return _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', u, u),
                     _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', u, 2.0)))

    weight_start = boundary_weight(at)
    weight_end = boundary_weight(_math(nodes, links, 'SUBTRACT', 1.0, at))
    weight_a = _switch(nodes, links, 'FLOAT', positive_is_b, weight_start, weight_end)
    weight_b = _switch(nodes, links, 'FLOAT', positive_is_b, weight_end, weight_start)
    for name, value in ((BEAD_A_WEIGHT, weight_a), (BEAD_B_WEIGHT, weight_b)):
        mask = nodes.new('GeometryNodeStoreNamedAttribute')
        mask.data_type = 'FLOAT'
        mask.domain = 'POINT'
        mask.inputs['Name'].default_value = name
        links.new(beaded, mask.inputs['Geometry'])
        links.new(value, mask.inputs['Value'])
        beaded = mask.outputs['Geometry']

    if hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
        # Available since Blender 4.5. Read the source's real corner normals
        # (including its sharp/smooth flags and custom normals), at the FINAL
        # bead position, not at the old contact point. Work on the bead alone
        # so the original object's normals and any previous beads survive.
        def source_shading_normal(mesh):
            sampler = nodes.new('GeometryNodeSampleNearestSurface')
            sampler.data_type = 'FLOAT_VECTOR'
            links.new(mesh, sampler.inputs['Mesh'])
            links.new(nodes.new('GeometryNodeInputNormal').outputs[0], sampler.inputs['Value'])
            links.new(nodes.new('GeometryNodeInputPosition').outputs[0], sampler.inputs['Sample Position'])
            return _vector(nodes, links, 'NORMALIZE', sampler.outputs['Value'])

        shade_a = source_shading_normal(gin.outputs['Geometry'])
        shade_b = source_shading_normal(info.outputs['Geometry'])
        own_shading = nodes.new('GeometryNodeInputNormal').outputs[0]
        own_weight = _math(nodes, links, 'SUBTRACT', 1.0,
                           _math(nodes, links, 'ADD', weight_a, weight_b))
        blended = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'ADD',
                          _vector(nodes, links, 'SCALE', own_shading, scale=own_weight),
                          _vector(nodes, links, 'ADD',
                                  _vector(nodes, links, 'SCALE', shade_a, scale=weight_a),
                                  _vector(nodes, links, 'SCALE', shade_b, scale=weight_b))))
        stored_normal = nodes.new('GeometryNodeStoreNamedAttribute')
        stored_normal.data_type = 'FLOAT_VECTOR'
        stored_normal.domain = 'CORNER'
        stored_normal.inputs['Name'].default_value = BEAD_NORMAL
        links.new(beaded, stored_normal.inputs['Geometry'])
        links.new(blended, stored_normal.inputs['Value'])
        custom = nodes.new('GeometryNodeSetMeshNormal')
        custom.mode = 'FREE'
        custom.domain = 'CORNER'
        links.new(stored_normal.outputs['Geometry'], custom.inputs['Mesh'])
        links.new(read_attribute(BEAD_NORMAL, 'FLOAT_VECTOR'), custom.inputs['Custom Normal'])
        beaded = _switch(nodes, links, 'GEOMETRY', gin.outputs[BEAD_SYNC_NORMALS],
                         beaded, custom.outputs['Mesh'])

    material = nodes.new('GeometryNodeSetMaterial')
    links.new(beaded, material.inputs['Geometry'])
    links.new(gin.outputs[BEAD_MATERIAL], material.inputs['Material'])
    beaded = material.outputs['Geometry']

    # Disable completely at zero rather than leaving a tiny degenerate ribbon.
    joined = nodes.new('GeometryNodeJoinGeometry')
    links.new(gin.outputs['Geometry'], joined.inputs['Geometry'])
    links.new(beaded, joined.inputs['Geometry'])
    active = _math(nodes, links, 'GREATER_THAN', radius, 1e-6)
    links.new(_switch(nodes, links, 'GEOMETRY', active, gin.outputs['Geometry'], joined.outputs['Geometry']), gout.inputs[0])
