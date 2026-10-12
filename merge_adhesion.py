"""Surface adhesion: deform A locally, project onto B, and optionally spread over B."""

import math

import bpy

from . import joins
from . import shapes as cn
from .merge_settings import *
from .merge_nodes import (
    _math, _vector, _switch, _smooth_normals, _surface_normal_sampler,
    _reset_bead_mapping,
)


def _falloff_weight(nodes, links, gin, distance, influence):
    """Pinned contact/outside weights for presets or the editable custom curve."""
    u = _math(nodes, links, 'DIVIDE', distance, _math(nodes, links, 'MAXIMUM', influence, 1e-6), clamp=True)
    x = _math(nodes, links, 'SUBTRACT', 1.0, u)
    square = lambda v: _math(nodes, links, 'MULTIPLY', v, v)
    cube = lambda v: _math(nodes, links, 'MULTIPLY', v, square(v))
    smooth = _math(nodes, links, 'MULTIPLY', square(x),
                   _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', x, 2.0)))
    sphere = _math(nodes, links, 'SQRT', _math(nodes, links, 'MAXIMUM', 0.0,
                   _math(nodes, links, 'SUBTRACT', 1.0, square(u))))
    weight = smooth
    for preset, value in ((SOFT, square(x)), (LINEAR, x), (SHARP, cube(x)), (SPHERE, sphere)):
        selected = _math(nodes, links, 'COMPARE', gin.outputs[FALLOFF], float(preset), 0.1)
        weight = _switch(nodes, links, 'FLOAT', selected, weight, value)
    curve = nodes.new('ShaderNodeFloatCurve')
    curve.name = curve.label = CURVE_NODE
    curve.inputs['Factor'].default_value = 1.0
    links.new(x, curve.inputs['Value'])
    _reset_bead_mapping(curve.mapping)
    # A curve may change the shoulder, but cannot detach the contact or move
    # the unaffected region. Clamp values and explicitly pin both endpoints.
    custom = _math(nodes, links, 'MAXIMUM', 0.0, _math(nodes, links, 'MINIMUM', 1.0, curve.outputs[0]))
    custom = _switch(nodes, links, 'FLOAT', _math(nodes, links, 'LESS_THAN', x, 1e-6), custom, 0.0)
    custom = _switch(nodes, links, 'FLOAT', _math(nodes, links, 'GREATER_THAN', x, 1.0 - 1e-6), custom, 1.0)
    weight = _switch(nodes, links, 'FLOAT', gin.outputs[BEAD_CUSTOM_CURVE], weight, custom)
    return weight


def _normal_pull(nodes, links, gin, distance, merge, weight):
    """Normal displacement amount, independent of tangential Push/Spread."""
    square = lambda value: _math(nodes, links, 'MULTIPLY', value, value)
    cube = lambda value: _math(nodes, links, 'MULTIPLY', value, square(value))
    # Merge Radius independently determines the normal-pull band. Its cubic
    # height has zero derivative on B and rejoins A with derivative one.
    v = _math(nodes, links, 'DIVIDE', distance, _math(nodes, links, 'MAXIMUM', merge, 1e-6), clamp=True)
    height_fraction = _math(nodes, links, 'MULTIPLY', square(v), _math(nodes, links, 'SUBTRACT', 2.0, v))
    sharp_height = _math(nodes, links, 'MULTIPLY', cube(v),
                         _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', v, 2.0)))
    sphere_height = _math(nodes, links, 'SUBTRACT', 1.0,
                          _math(nodes, links, 'COSINE', _math(nodes, links, 'MULTIPLY', v, math.pi / 2.0)))
    for preset, value in ((SOFT, square(v)), (LINEAR, v), (SHARP, sharp_height), (SPHERE, sphere_height)):
        selected = _math(nodes, links, 'COMPARE', gin.outputs[FALLOFF], float(preset), 0.1)
        height_fraction = _switch(nodes, links, 'FLOAT', selected, height_fraction, value)
    h = _math(nodes, links, 'MULTIPLY', merge, height_fraction)
    pull = _math(nodes, links, 'MULTIPLY', weight,
                 _math(nodes, links, 'MAXIMUM', 0.0, _math(nodes, links, 'SUBTRACT', distance, h)))
    pull = _math(nodes, links, 'MULTIPLY', pull, _math(nodes, links, 'LESS_THAN', distance, merge))
    return pull


def _corner_transition(nodes, links, target, point, projected, direction, normal, next_normal, step, reach):
    """Probe the adjacent face when nearest projection stalls at a rim.

    Inset behind the old normal, ray back along the incoming tangent, and
    require surface support along the bridge. This can discover a cap without
    jumping blindly to an unrelated nearby surface. Rotate the incoming
    tangent by the minimal rotation between face normals instead of projecting
    it to zero at a perpendicular face.
    """
    if reach is None:
        return projected, next_normal, direction, 0.0, 1.0
    advance = _math(nodes, links, 'MAXIMUM', 0.0,
                    _vector(nodes, links, 'DOT_PRODUCT', _vector(nodes, links, 'SUBTRACT', projected, point), direction))
    stalled = _math(nodes, links, 'LESS_THAN', advance, _math(nodes, links, 'MULTIPLY', step, 0.75))
    abrupt = _math(nodes, links, 'LESS_THAN', _vector(nodes, links, 'DOT_PRODUCT', normal, next_normal), ADHESION_MAX_TURN_COS)
    attempt = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'GREATER_THAN', reach, 1e-8),
                    _math(nodes, links, 'MAXIMUM', stalled, abrupt))
    inset = _math(nodes, links, 'MINIMUM', reach,
                  _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'SUBTRACT', step, advance),
                        _math(nodes, links, 'MULTIPLY', step, 0.02)))
    probe_distance = _math(nodes, links, 'ADD', _math(nodes, links, 'ADD', step, reach), 1e-5)
    candidate = _vector(nodes, links, 'SUBTRACT', projected, _vector(nodes, links, 'SCALE', normal, scale=inset))
    ray = nodes.new('GeometryNodeRaycast')
    links.new(target, ray.inputs['Target Geometry'])
    links.new(_vector(nodes, links, 'ADD', candidate,
                      _vector(nodes, links, 'SCALE', direction, scale=probe_distance)), ray.inputs['Source Position'])
    links.new(_vector(nodes, links, 'SCALE', direction, scale=-1.0), ray.inputs['Ray Direction'])
    links.new(_math(nodes, links, 'MULTIPLY', probe_distance, 2.0), ray.inputs['Ray Length'])
    hit = ray.outputs['Hit Position']
    hit_normal = _vector(nodes, links, 'NORMALIZE', ray.outputs['Hit Normal'])
    cosine = _vector(nodes, links, 'DOT_PRODUCT', normal, hit_normal)
    turns = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'LESS_THAN', cosine, 0.95),
                  _math(nodes, links, 'GREATER_THAN', cosine, -0.5))
    support = nodes.new('GeometryNodeProximity')
    support.target_element = 'FACES'
    links.new(target, support.inputs['Target'])
    midpoint = _vector(nodes, links, 'SCALE', _vector(nodes, links, 'ADD', projected, hit), scale=0.5)
    links.new(midpoint, support.inputs['Source Position'])
    supported = _math(nodes, links, 'LESS_THAN', support.outputs['Distance'], 1e-5)
    bounded = _math(nodes, links, 'LESS_THAN',
                    _vector(nodes, links, 'LENGTH', _vector(nodes, links, 'SUBTRACT', hit, projected)),
                    _math(nodes, links, 'ADD', reach, 1e-5))
    valid = _switch(nodes, links, 'FLOAT', attempt, 0.0,
                    _math(nodes, links, 'MULTIPLY', ray.outputs['Is Hit'],
                          _math(nodes, links, 'MULTIPLY', turns, _math(nodes, links, 'MULTIPLY', supported, bounded))))
    axis = _vector(nodes, links, 'CROSS_PRODUCT', normal, hit_normal)
    cross = _vector(nodes, links, 'CROSS_PRODUCT', axis, direction)
    rotated = _vector(nodes, links, 'ADD', direction,
                      _vector(nodes, links, 'ADD', cross,
                              _vector(nodes, links, 'SCALE', _vector(nodes, links, 'CROSS_PRODUCT', axis, cross),
                                      scale=_math(nodes, links, 'DIVIDE', 1.0,
                                                  _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'ADD', 1.0, cosine), 0.5)))))
    rotated = _vector(nodes, links, 'NORMALIZE', rotated)
    ratio = _math(nodes, links, 'DIVIDE', reach, _math(nodes, links, 'MAXIMUM', step, 1e-6), clamp=True)
    strength = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', ratio, ratio),
                     _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', ratio, 2.0)))
    # Both the rim and hit are on the supported adjacent face. Fade the inset
    # and subsequent travel, not the normal rotation (which must stay tangent).
    softened_hit = _vector(nodes, links, 'ADD', projected,
                           _vector(nodes, links, 'SCALE', _vector(nodes, links, 'SUBTRACT', hit, projected), scale=strength))
    return (_switch(nodes, links, 'VECTOR', valid, projected, softened_hit),
            _switch(nodes, links, 'VECTOR', valid, next_normal, hit_normal),
            _switch(nodes, links, 'VECTOR', valid, direction, rotated), valid, strength)


def _spread_adaptive(nodes, links, shaded, target, landed, tangent, spread, weight, spreading, corner_reach=None):
    """Estimate local curvature, then walk in 1..8 bounded steps.

    Quarter/half-distance probes estimate normal rotation per chord length.
    Flat surfaces need one step; curved surfaces aim for 12 degrees per step.
    The finite budget is retained. Sharp turns stop unless Corner Reach finds
    a supported adjacent face; this is not an unrestricted geodesic solver.
    """
    def sample_normal(point):
        sample = _surface_normal_sampler(nodes, links, shaded)
        links.new(point, sample.inputs['Sample Position'])
        return _vector(nodes, links, 'NORMALIZE', sample.outputs['Value'])

    def project(point):
        node = nodes.new('GeometryNodeProximity')
        node.target_element = 'FACES'
        links.new(target, node.inputs['Target'])
        links.new(point, node.inputs['Source Position'])
        return node.outputs['Position']

    def transport(direction, normal):
        return _vector(nodes, links, 'SUBTRACT', direction,
                       _vector(nodes, links, 'SCALE', normal,
                               scale=_vector(nodes, links, 'DOT_PRODUCT', direction, normal)))

    start = landed
    length = _math(nodes, links, 'MULTIPLY', spread,
                   _math(nodes, links, 'MULTIPLY', weight, weight))
    start_normal = sample_normal(start)
    direction = _vector(nodes, links, 'NORMALIZE', transport(tangent, start_normal))
    curvature = 0.0
    for fraction in (0.25, 0.5):
        point = project(_vector(nodes, links, 'ADD', start,
                        _vector(nodes, links, 'SCALE', direction,
                                scale=_math(nodes, links, 'MULTIPLY', length, fraction))))
        cosine = _vector(nodes, links, 'DOT_PRODUCT', start_normal, sample_normal(point))
        cosine = _math(nodes, links, 'MAXIMUM', -1.0, _math(nodes, links, 'MINIMUM', 1.0, cosine))
        angle = _math(nodes, links, 'ARCCOSINE', cosine)
        chord = _vector(nodes, links, 'LENGTH', _vector(nodes, links, 'SUBTRACT', point, start))
        estimate = _math(nodes, links, 'DIVIDE', angle, _math(nodes, links, 'MAXIMUM', chord, 1e-6))
        curvature = _math(nodes, links, 'MAXIMUM', curvature, estimate)
    required = _math(nodes, links, 'CEIL',
                     _math(nodes, links, 'DIVIDE', _math(nodes, links, 'MULTIPLY', length, curvature),
                           ADHESION_SPREAD_TURN_RADIANS))
    planned = _math(nodes, links, 'MINIMUM', float(ADHESION_SPREAD_MAX_STEPS),
                    _math(nodes, links, 'MAXIMUM', 1.0, required))
    active = _math(nodes, links, 'MULTIPLY', spreading, _math(nodes, links, 'GREATER_THAN', length, 1e-8))
    # A lazy field switch avoids curvature probes outside the spreading mask.
    planned = _switch(nodes, links, 'FLOAT', active, 0.0, planned)
    nominal_step = _math(nodes, links, 'DIVIDE', length, _math(nodes, links, 'MAXIMUM', planned, 1.0))
    taken = 0.0
    previous_normal = start_normal
    continuation = 1.0
    for index in range(ADHESION_SPREAD_MAX_STEPS):
        step = _math(nodes, links, 'MULTIPLY', nominal_step, continuation)
        walk_n = previous_normal
        transported = transport(direction, walk_n)
        next_direction = _vector(nodes, links, 'NORMALIZE', transported)
        proposal = _vector(nodes, links, 'ADD', landed,
                           _vector(nodes, links, 'SCALE', next_direction, scale=step))
        next_point = project(proposal)
        next_n = sample_normal(next_point)
        next_point, next_n, outgoing, wrapped, corner_strength = _corner_transition(
            nodes, links, target, landed, next_point, next_direction, walk_n, next_n, step, corner_reach)
        safe_turn = _math(nodes, links, 'GREATER_THAN',
                          _vector(nodes, links, 'DOT_PRODUCT', walk_n, next_n),
                          ADHESION_MAX_TURN_COS)
        safe_turn = _math(nodes, links, 'MAXIMUM', safe_turn, wrapped)
        usable = _math(nodes, links, 'GREATER_THAN',
                       _vector(nodes, links, 'LENGTH', transported), ADHESION_MIN_TANGENT_LENGTH)
        enabled = _math(nodes, links, 'LESS_THAN', float(index), planned)
        # Multiplying by a zero mask still evaluates both input fields.
        # Use a lazy switch so unused iterations do not run surface probes.
        safe_step = _switch(nodes, links, 'FLOAT', enabled, 0.0,
                            _math(nodes, links, 'MULTIPLY', safe_turn, usable))
        landed = _switch(nodes, links, 'VECTOR', safe_step, landed, next_point)
        direction = _vector(nodes, links, 'SCALE', outgoing, scale=safe_step)
        previous_normal = _switch(nodes, links, 'VECTOR', safe_step, walk_n, next_n)
        if corner_reach is not None:
            continuation = _switch(nodes, links, 'FLOAT', wrapped, continuation,
                                   _math(nodes, links, 'MINIMUM', continuation, corner_strength))
        taken = _math(nodes, links, 'ADD', taken, safe_step)
    return (_switch(nodes, links, 'VECTOR', active, start, landed), planned,
            _switch(nodes, links, 'FLOAT', active, 0.0, taken))


def _spread_fixed(nodes, links, shaded, target, landed, tangent, spread, weight, spreading, corner_reach=None):
    """Fast bounded walk with optional adjacent-face discovery at sharp rims."""
    square = lambda value: _math(nodes, links, 'MULTIPLY', value, value)
    # Keep extra coverage distinct from Push: it is concentrated near the
    # contact (weight squared), and walks B in bounded reprojected steps.
    # Transport the direction in B's tangent plane instead of taking one long
    # jump that may cut across a cylinder or skip a cap edge.
    spread_start = landed
    taken = 0.0
    direction = tangent
    nominal_step = _math(nodes, links, 'MULTIPLY', spread,
                 _math(nodes, links, 'MULTIPLY', square(weight), 1.0 / ADHESION_SPREAD_STEPS))
    previous_normal = None
    continuation = 1.0
    for _ in range(ADHESION_SPREAD_STEPS):
        step = _math(nodes, links, 'MULTIPLY', nominal_step, continuation)
        walk_normal = _surface_normal_sampler(nodes, links, shaded)
        links.new(landed, walk_normal.inputs['Sample Position'])
        walk_n = (_vector(nodes, links, 'NORMALIZE', walk_normal.outputs['Value'])
                  if previous_normal is None else previous_normal)
        transported = _vector(nodes, links, 'SUBTRACT', direction,
                              _vector(nodes, links, 'SCALE', walk_n,
                                      scale=_vector(nodes, links, 'DOT_PRODUCT', direction, walk_n)))
        direction = _vector(nodes, links, 'NORMALIZE', transported)
        proposal = _vector(nodes, links, 'ADD', landed, _vector(nodes, links, 'SCALE', direction, scale=step))
        project = nodes.new('GeometryNodeProximity')
        project.target_element = 'FACES'
        links.new(target, project.inputs['Target'])
        links.new(proposal, project.inputs['Source Position'])
        # A sudden normal turn is a cap/sharp edge, not a smooth continuation.
        next_normal = _surface_normal_sampler(nodes, links, shaded)
        links.new(project.outputs['Position'], next_normal.inputs['Sample Position'])
        next_point, next_n, outgoing, wrapped, corner_strength = _corner_transition(
            nodes, links, target, landed, project.outputs['Position'], direction, walk_n,
            _vector(nodes, links, 'NORMALIZE', next_normal.outputs['Value']), step, corner_reach)
        safe_turn = _math(nodes, links, 'GREATER_THAN',
                          _vector(nodes, links, 'DOT_PRODUCT', walk_n, next_n), ADHESION_MAX_TURN_COS)
        safe_turn = _math(nodes, links, 'MAXIMUM', safe_turn, wrapped)
        usable = _math(nodes, links, 'GREATER_THAN', _vector(nodes, links, 'LENGTH', transported), ADHESION_MIN_TANGENT_LENGTH)
        safe_step = _math(nodes, links, 'MULTIPLY', safe_turn, usable)
        landed = _switch(nodes, links, 'VECTOR', safe_step, landed, next_point)
        direction = _vector(nodes, links, 'SCALE', outgoing, scale=safe_step)
        previous_normal = _switch(nodes, links, 'VECTOR', safe_step, walk_n, next_n)
        if corner_reach is not None:
            continuation = _switch(nodes, links, 'FLOAT', wrapped, continuation,
                                   _math(nodes, links, 'MINIMUM', continuation, corner_strength))
        taken = _math(nodes, links, 'ADD', taken, safe_step)
    landed = _switch(nodes, links, 'VECTOR', spreading, spread_start, landed)
    active = _math(nodes, links, 'MULTIPLY', spreading,
                   _math(nodes, links, 'GREATER_THAN', _math(nodes, links, 'MULTIPLY', spread, square(weight)), 1e-8))
    return (_switch(nodes, links, 'VECTOR', active, spread_start, landed),
            _switch(nodes, links, 'FLOAT', active, 0.0, 4.0),
            _switch(nodes, links, 'FLOAT', active, 0.0, taken))


def _spread_over_surface(nodes, links, shaded, target, landed, tangent, spread, weight, spreading, adaptive=None, corner_reach=None):
    """Select quality-oriented adaptive walking or the fast fixed-4 path."""
    refined = _spread_adaptive(nodes, links, shaded, target, landed, tangent, spread, weight, spreading, corner_reach)
    if adaptive is None:
        return refined
    fixed = _spread_fixed(nodes, links, shaded, target, landed, tangent, spread, weight, spreading, corner_reach)
    return tuple(_switch(nodes, links, dtype, adaptive, old, new)
                 for dtype, old, new in zip(('VECTOR', 'FLOAT', 'FLOAT'), fixed, refined))


def _match_contact_normals(nodes, links, result, gin, shaded, merge):
    """Version-gated corner normals; returns unchanged geometry on Blender 4.2."""
    square = lambda value: _math(nodes, links, 'MULTIPLY', value, value)
    if hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
        # Read the captured distance, not the new distance after deformation.
        captured = nodes.new('GeometryNodeInputNamedAttribute')
        captured.data_type = 'FLOAT'
        captured.inputs['Name'].default_value = ADHESION_DISTANCE
        normal_weight = _math(nodes, links, 'SUBTRACT', 1.0,
                              _math(nodes, links, 'DIVIDE', captured.outputs['Attribute'],
                                    _math(nodes, links, 'MAXIMUM', merge, 1e-6), clamp=True), clamp=True)
        normal_weight = square(normal_weight)
        final_sample = _surface_normal_sampler(nodes, links, shaded)
        mixed = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'ADD',
                         _vector(nodes, links, 'SCALE', nodes.new('GeometryNodeInputNormal').outputs[0],
                                 scale=_math(nodes, links, 'SUBTRACT', 1.0, normal_weight)),
                         _vector(nodes, links, 'SCALE', final_sample.outputs['Value'], scale=normal_weight)))
        custom_normal = nodes.new('GeometryNodeSetMeshNormal')
        custom_normal.mode, custom_normal.domain = 'FREE', 'CORNER'
        links.new(result, custom_normal.inputs['Mesh'])
        links.new(mixed, custom_normal.inputs['Custom Normal'])
        result = _switch(nodes, links, 'GEOMETRY', gin.outputs[BEAD_SYNC_NORMALS], result, custom_normal.outputs['Mesh'])
    return result


def _local_subdivide(nodes, links, geometry, target, influence, level, label):
    """Conforming adaptive SIMPLE refinement, without open patch seams.

    Seed quads once. Select conservatively using face-center distance plus
    the radius of all four corners; small targets inside long faces cannot
    be missed by testing only the vertices or center. Fine patch boundaries
    collapse onto existing coarse vertices, creating transition triangles
    rather than leaving unconnected T-junctions. No smoothing of A is done.
    """
    def simple(mesh, amount):
        node = nodes.new('GeometryNodeSubdivideMesh')
        node.label = label
        links.new(mesh, node.inputs['Mesh'])
        if isinstance(amount, (int, float)):
            node.inputs['Level'].default_value = amount
        else:
            links.new(amount, node.inputs['Level'])
        return node.outputs['Mesh']

    source = geometry
    geometry = simple(geometry, _math(nodes, links, 'MINIMUM', level, 1.0))
    for depth in range(1, 5):
        # Limit topology-adaptation passes. Repeated nearest-vertex seam
        # collapse can create overlapping transition fans at high levels.
        # Further levels refine the already reduced, conforming mesh normally.
        if depth > 2:
            geometry = _switch(nodes, links, 'GEOMETRY',
                               _math(nodes, links, 'GREATER_THAN', level, float(depth)),
                               geometry, simple(geometry, 1))
            continue
        center = nodes.new('GeometryNodeInputPosition').outputs[0]
        near = nodes.new('GeometryNodeProximity')
        near.target_element = 'FACES'
        links.new(target, near.inputs['Target'])
        links.new(center, near.inputs['Source Position'])
        radius = None
        for corner_index in range(4):
            corner = nodes.new('GeometryNodeCornersOfFace')
            links.new(nodes.new('GeometryNodeInputIndex').outputs[0], corner.inputs['Face Index'])
            corner.inputs['Sort Index'].default_value = corner_index
            vertex = nodes.new('GeometryNodeVertexOfCorner')
            links.new(corner.outputs['Corner Index'], vertex.inputs['Corner Index'])
            sample = nodes.new('GeometryNodeSampleIndex')
            sample.data_type, sample.domain = 'FLOAT_VECTOR', 'POINT'
            links.new(geometry, sample.inputs['Geometry'])
            links.new(center, sample.inputs['Value'])
            links.new(vertex.outputs['Vertex Index'], sample.inputs['Index'])
            distance = _vector(nodes, links, 'LENGTH', _vector(nodes, links, 'SUBTRACT', sample.outputs['Value'], center))
            radius = distance if radius is None else _math(nodes, links, 'MAXIMUM', radius, distance)
        selection = _math(nodes, links, 'LESS_THAN', near.outputs['Distance'],
                          _math(nodes, links, 'ADD', radius, _math(nodes, links, 'MULTIPLY', influence, 1.25)))
        separate = nodes.new('GeometryNodeSeparateGeometry')
        separate.domain = 'FACE'
        links.new(geometry, separate.inputs['Geometry'])
        links.new(selection, separate.inputs['Selection'])
        coarse = separate.outputs['Inverted']
        fine = simple(separate.outputs['Selection'], 1)
        # A fine boundary is stitched only if it lies on a coarse edge.
        # This deliberately excludes actual open/contact boundaries.
        coarse_edge = nodes.new('GeometryNodeProximity')
        coarse_edge.target_element = 'EDGES'
        links.new(coarse, coarse_edge.inputs['Target'])
        coarse_vertex = nodes.new('GeometryNodeProximity')
        coarse_vertex.target_element = 'POINTS'
        links.new(coarse, coarse_vertex.inputs['Target'])
        neighbors = nodes.new('GeometryNodeInputMeshEdgeNeighbors')
        on_boundary = _math(nodes, links, 'LESS_THAN', neighbors.outputs['Face Count'], 1.99)
        on_coarse = _math(nodes, links, 'LESS_THAN', coarse_edge.outputs['Distance'], 1e-6)
        size = nodes.new('GeometryNodeAttributeDomainSize')
        size.component = 'MESH'
        links.new(coarse, size.inputs['Geometry'])
        valid = _math(nodes, links, 'GREATER_THAN', size.outputs['Point Count'], 0.0)
        stitch = nodes.new('GeometryNodeSetPosition')
        links.new(fine, stitch.inputs['Geometry'])
        links.new(_math(nodes, links, 'MULTIPLY', valid,
                        _math(nodes, links, 'MULTIPLY', on_boundary, on_coarse)), stitch.inputs['Selection'])
        links.new(coarse_vertex.outputs['Position'], stitch.inputs['Position'])
        join = nodes.new('GeometryNodeJoinGeometry')
        links.new(coarse, join.inputs['Geometry'])
        links.new(stitch.outputs['Geometry'], join.inputs['Geometry'])
        weld = nodes.new('GeometryNodeMergeByDistance')
        weld.inputs['Distance'].default_value = 1e-6
        links.new(join.outputs['Geometry'], weld.inputs['Geometry'])
        links.new(_math(nodes, links, 'LESS_THAN', coarse_vertex.outputs['Distance'], 1e-6), weld.inputs['Selection'])
        # If every face is selected, skip the stitching work altogether.
        statistic = nodes.new('GeometryNodeAttributeStatistic')
        statistic.data_type, statistic.domain = 'FLOAT', 'FACE'
        links.new(geometry, statistic.inputs['Geometry'])
        links.new(selection, statistic.inputs['Attribute'])
        refined = _switch(nodes, links, 'GEOMETRY', _math(nodes, links, 'GREATER_THAN', statistic.outputs['Mean'], 0.999999),
                          weld.outputs['Geometry'], simple(geometry, 1))
        refined = _switch(nodes, links, 'GEOMETRY', _math(nodes, links, 'GREATER_THAN', statistic.outputs['Mean'], 0.0),
                          geometry, refined)
        geometry = _switch(nodes, links, 'GEOMETRY', _math(nodes, links, 'GREATER_THAN', level, float(depth)),
                           geometry, refined)
    # Conservative safety gate: curved patch boundaries can invert a
    # transition fan. Never emit an invalid preview; fall back to full simple
    # refinement if winding, face area, or edge manifoldness fails.
    original_normals = _smooth_normals(nodes, links, source, explicit_faces=True)
    original_sample = _surface_normal_sampler(nodes, links, original_normals)
    flipped = _math(nodes, links, 'LESS_THAN',
                    _vector(nodes, links, 'DOT_PRODUCT', nodes.new('GeometryNodeInputNormal').outputs[0],
                            original_sample.outputs['Value']), 0.05)
    area = nodes.new('GeometryNodeInputMeshFaceArea')
    collapsed = _math(nodes, links, 'LESS_THAN', area.outputs['Area'], 1e-12)
    face_check = nodes.new('GeometryNodeAttributeStatistic')
    face_check.data_type, face_check.domain = 'FLOAT', 'FACE'
    links.new(geometry, face_check.inputs['Geometry'])
    links.new(_math(nodes, links, 'MAXIMUM', flipped, collapsed), face_check.inputs['Attribute'])
    neighbors = nodes.new('GeometryNodeInputMeshEdgeNeighbors')
    source_edge_check = nodes.new('GeometryNodeAttributeStatistic')
    source_edge_check.data_type, source_edge_check.domain = 'FLOAT', 'EDGE'
    links.new(source, source_edge_check.inputs['Geometry'])
    links.new(_math(nodes, links, 'LESS_THAN', neighbors.outputs['Face Count'], 2.0),
              source_edge_check.inputs['Attribute'])
    source_closed = _math(nodes, links, 'LESS_THAN', source_edge_check.outputs['Mean'], 1e-8)
    edge_check = nodes.new('GeometryNodeAttributeStatistic')
    edge_check.data_type, edge_check.domain = 'FLOAT', 'EDGE'
    links.new(geometry, edge_check.inputs['Geometry'])
    bad_edge = _math(nodes, links, 'MAXIMUM',
                     _math(nodes, links, 'GREATER_THAN', neighbors.outputs['Face Count'], 2.0),
                     _math(nodes, links, 'MULTIPLY', source_closed,
                           _math(nodes, links, 'LESS_THAN', neighbors.outputs['Face Count'], 2.0)))
    links.new(bad_edge, edge_check.inputs['Attribute'])
    invalid = _math(nodes, links, 'MAXIMUM', face_check.outputs['Mean'], edge_check.outputs['Mean'])
    return _switch(nodes, links, 'GEOMETRY', _math(nodes, links, 'GREATER_THAN', invalid, 0.0),
                   geometry, simple(source, level))


def _relax_spread(nodes, links, geometry, target, shaded, strength, passes, influence, spreading, gap):
    """Bounded displacement relaxation, rebuilt from the live input each evaluation.

    Smooth deformation, NOT positions, to avoid shrinking A's undeformed
    cylinder. Each pass enforces pins anew; there is no frame state/cache or
    dependency on yesterday's Boolean vertex indices. Blur runs on topology,
    but expensive target queries/position updates are selected locally.
    """
    def attribute(name, data_type='FLOAT'):
        node = nodes.new('GeometryNodeInputNamedAttribute')
        node.data_type = data_type
        node.inputs['Name'].default_value = name
        return node.outputs['Attribute']

    source = attribute(ADHESION_SOURCE_POSITION, 'FLOAT_VECTOR')
    weight = attribute(ADHESION_WEIGHT)
    distance = attribute(ADHESION_DISTANCE)
    # Quadratic bell: zero value AND slope at either boundary. Preserve the
    # exact cut/contact vertices regardless of custom curve or subdivision.
    bell = _math(nodes, links, 'MULTIPLY', 4.0,
                 _math(nodes, links, 'MULTIPLY', weight, _math(nodes, links, 'SUBTRACT', 1.0, weight)))
    amount = _math(nodes, links, 'MULTIPLY', strength, _math(nodes, links, 'MULTIPLY', bell, bell))
    selection = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'GREATER_THAN', amount, 1e-8),
                      _math(nodes, links, 'GREATER_THAN', distance, 1e-6))
    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    for index in range(4):
        delta = _vector(nodes, links, 'SUBTRACT', position, source)
        blur = nodes.new('GeometryNodeBlurAttribute')
        blur.data_type = 'FLOAT_VECTOR'
        blur.label = f'Live spread displacement relax {index + 1}'
        blur.inputs['Iterations'].default_value = 1
        links.new(delta, blur.inputs['Value'])
        # Pins still supply their displacement to adjacent interior points,
        # but do not diffuse their own values. Set Position below enforces
        # the same mask. This is a field mask, not a separate mesh snapshot.
        links.new(selection, blur.inputs['Weight'])
        correction = _vector(nodes, links, 'SUBTRACT', blur.outputs['Value'], delta)
        length = _vector(nodes, links, 'LENGTH', correction)
        scale = _math(nodes, links, 'MINIMUM', amount,
                      _math(nodes, links, 'DIVIDE', _math(nodes, links, 'MULTIPLY', influence, 0.02),
                            _math(nodes, links, 'MAXIMUM', length, 1e-8)))
        candidate = _vector(nodes, links, 'ADD', position,
                            _vector(nodes, links, 'SCALE', correction, scale=scale))
        near = nodes.new('GeometryNodeProximity')
        near.target_element = 'FACES'
        links.new(target, near.inputs['Target'])
        links.new(candidate, near.inputs['Source Position'])
        sample = _surface_normal_sampler(nodes, links, shaded)
        links.new(near.outputs['Position'], sample.inputs['Sample Position'])
        normal = _vector(nodes, links, 'NORMALIZE', sample.outputs['Value'])
        signed = _vector(nodes, links, 'DOT_PRODUCT',
                         _vector(nodes, links, 'SUBTRACT', candidate, near.outputs['Position']), normal)
        # Don't pull every interior point onto B: preserve its height unless
        # this step crosses the surface. Weighted clearance fades toward A.
        clearance = _math(nodes, links, 'MULTIPLY', gap, _math(nodes, links, 'MULTIPLY', weight, weight))
        safe = _switch(nodes, links, 'VECTOR', _math(nodes, links, 'LESS_THAN', signed, clearance), candidate,
                       _vector(nodes, links, 'ADD', near.outputs['Position'],
                               _vector(nodes, links, 'SCALE', normal, scale=clearance)))
        # A nearest-face change at a cap can request a large corrective snap.
        # Reject that pass instead of creating a new fold/long stretched face.
        final_length = _vector(nodes, links, 'LENGTH', _vector(nodes, links, 'SUBTRACT', safe, position))
        budget = _math(nodes, links, 'ADD', _math(nodes, links, 'MULTIPLY', influence, 0.02), 1e-6)
        safe = _switch(nodes, links, 'VECTOR', _math(nodes, links, 'GREATER_THAN', final_length, budget), safe, position)
        move = nodes.new('GeometryNodeSetPosition')
        links.new(geometry, move.inputs['Geometry'])
        links.new(selection, move.inputs['Selection'])
        links.new(safe, move.inputs['Position'])
        enabled = _math(nodes, links, 'MULTIPLY', spreading,
                        _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'GREATER_THAN', strength, 1e-8),
                              _math(nodes, links, 'GREATER_THAN', passes, float(index))))
        geometry = _switch(nodes, links, 'GEOMETRY', enabled, geometry, move.outputs['Geometry'])
    return geometry


def _build_adhesion(ng):
    """Deform A's exposed contact patch, rather than overlaying a ribbon.

    All lengths are A-local. The exact cut removes only the part hidden in B.
    Influence, normal pull, and tangential push have independent controls.
    B and the source datablocks remain untouched; this is evaluated geometry.
    """
    ng.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name=BASE, in_out='INPUT', socket_type='NodeSocketObject')
    cn._new_input(ng, GLOBAL, 'NodeSocketFloat', 1.0, 0.0, 100.0,
                  'Scales influence, merge, push, spread and clearance together; preserves their ratios')
    for name, value, description in (
        (INFLUENCE_RADIUS, 0.16, 'Outer distance from B affected by adhesion, measured in A local units'),
        (MERGE_RADIUS, 0.08, 'Distance band reshaped toward B; not bead height'),
        (PUSH_STRENGTH, 0.08, 'Maximum outward travel along B; not normal displacement'),
        (SURFACE_SPREAD, 0.0, 'Extra travel along B near the contact; capped at Influence Radius for stability'),
        (SURFACE_CORNER_REACH, 0.08, 'Adjacent-face recognition and continuation range at rims; increases gradually, 0 stops at sharp edges'),
        (SURFACE_CLEARANCE, 0.0001, 'Small separation from B only when Surface Spread is active; A-local units')):
        cn._new_input(ng, name, 'NodeSocketFloat', value, 0.0, 1000.0, description)
    cn._new_input(ng, FALLOFF, 'NodeSocketInt', SMOOTH, SMOOTH, SPHERE,
                  '0 Smooth, 1 Soft, 2 Linear, 4 Sharp, 5 Sphere; shapes the adhesion transition')
    cn._new_input(ng, BEAD_CUSTOM_CURVE, 'NodeSocketBool', False, 0, 1,
                  'Custom falloff: x=0 outside, x=1 contact; diagonal uses a linear transition')
    cn._new_input(ng, ADHESION_ADAPTIVE_SPREAD, 'NodeSocketBool', True, 0, 1,
                  'Choose 1-8 walking steps from curvature; off uses fixed 4 steps for cheaper previews')
    cn._new_input(ng, SURFACE_RELAX, 'NodeSocketFloat', 0.35, 0.0, 1.0,
                  'Live displacement relaxation inside Surface Spread; 0 restores the unsmoothed result')
    cn._new_input(ng, SURFACE_RELAX_PASSES, 'NodeSocketInt', 2, 0, 4,
                  'Bounded stateless relaxation passes; 0 bypasses, fewer passes for interactive previews')
    cn._new_input(ng, BEAD_SYNC_NORMALS, 'NodeSocketBool', True, 0, 1,
                  'Blend contact shading toward B while retaining A shading outside the influence band')
    cn._new_input(ng, ADHESION_LEVELS, 'NodeSocketInt', ADHESION_DEFAULT_LEVELS, 0, 5,
                  'Simple subdivision of A: each level multiplies faces by four; lower for moving previews')
    cn._new_input(ng, ADHESION_LOCAL_SUBDIVISION, 'NodeSocketBool', False, 0, 1,
                  'Experimental: locally refine near B; transition shading can differ, disable for final rendering')
    cn._new_input(ng, BEAD_SUBDIVISION_ORDER, 'NodeSocketInt', BEFORE_SNAP, AFTER_SNAP, BEFORE_SNAP,
                  'Before Snap fits every subdivided point; After Snap interpolates the fitted coarse patch')
    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    square = lambda value: _math(nodes, links, 'MULTIPLY', value, value)
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    links.new(gin.outputs[BASE], info.inputs['Object'])
    target = info.outputs['Geometry']
    influence = _math(nodes, links, 'MULTIPLY', gin.outputs[INFLUENCE_RADIUS], gin.outputs[GLOBAL])
    merge = _math(nodes, links, 'MULTIPLY', gin.outputs[MERGE_RADIUS], gin.outputs[GLOBAL])
    push = _math(nodes, links, 'MULTIPLY', gin.outputs[PUSH_STRENGTH], gin.outputs[GLOBAL])
    spread = _math(nodes, links, 'MINIMUM', influence,
                   _math(nodes, links, 'MULTIPLY', gin.outputs[SURFACE_SPREAD], gin.outputs[GLOBAL]))
    corner_reach = _math(nodes, links, 'MINIMUM', spread,
                         _math(nodes, links, 'MULTIPLY', gin.outputs[SURFACE_CORNER_REACH], gin.outputs[GLOBAL]))
    spreading = _math(nodes, links, 'GREATER_THAN', spread, 1e-8)
    before = _math(nodes, links, 'GREATER_THAN', gin.outputs[BEAD_SUBDIVISION_ORDER], 0.5)

    def subdivide(geometry, level, label):
        node = nodes.new('GeometryNodeSubdivideMesh')
        node.label = label
        links.new(geometry, node.inputs['Mesh'])
        links.new(level, node.inputs['Level'])
        local = _local_subdivide(nodes, links, geometry, target, influence, level, label)
        return _switch(nodes, links, 'GEOMETRY', gin.outputs[ADHESION_LOCAL_SUBDIVISION], node.outputs['Mesh'], local)

    # Capture A's spreading direction before Boolean-generated normals can
    # contaminate it with the cut surface of B.
    own_store = nodes.new('GeometryNodeStoreNamedAttribute')
    own_store.data_type, own_store.domain = 'FLOAT_VECTOR', 'POINT'
    own_store.inputs['Name'].default_value = OWN_NORMAL
    links.new(gin.outputs['Geometry'], own_store.inputs['Geometry'])
    links.new(nodes.new('GeometryNodeInputNormal').outputs[0], own_store.inputs['Value'])
    difference = nodes.new('GeometryNodeMeshBoolean')
    difference.operation = 'DIFFERENCE'
    if hasattr(difference, 'solver'):
        difference.solver = 'EXACT'
    # Preserve A's regular rows: subdividing Boolean-created n-gons fans
    # triangles across the entire influence band and causes radial wrinkles.
    dense_source = subdivide(own_store.outputs['Geometry'],
                             _switch(nodes, links, 'INT', before, 0, gin.outputs[ADHESION_LEVELS]), 'Before adhesion')
    links.new(dense_source, difference.inputs[0])
    links.new(target, joins._multi_input(difference))
    cut_proximity = nodes.new('GeometryNodeProximity')
    cut_proximity.target_element = 'FACES'
    links.new(target, cut_proximity.inputs['Target'])
    drop = nodes.new('GeometryNodeDeleteGeometry')
    drop.domain = 'FACE'
    links.new(difference.outputs['Mesh'], drop.inputs['Geometry'])
    links.new(_math(nodes, links, 'LESS_THAN', cut_proximity.outputs['Distance'], 1e-6), drop.inputs['Selection'])
    prepared = drop.outputs['Geometry']
    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    proximity = nodes.new('GeometryNodeProximity')
    proximity.target_element = 'FACES'
    links.new(target, proximity.inputs['Target'])
    links.new(position, proximity.inputs['Source Position'])
    q, distance = proximity.outputs['Position'], proximity.outputs['Distance']
    shaded = _smooth_normals(nodes, links, target, explicit_faces=True)
    sample = _surface_normal_sampler(nodes, links, shaded)
    links.new(q, sample.inputs['Sample Position'])
    sampled_normal = _vector(nodes, links, 'NORMALIZE', sample.outputs['Value'])
    away = _vector(nodes, links, 'SUBTRACT', position, q)
    near = _math(nodes, links, 'DIVIDE', distance,
                 _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'MULTIPLY', influence, ADHESION_NORMAL_BLEND_FRACTION), 1e-6), clamp=True)
    near = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', near, near),
                 _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', near, 2.0)))
    normal = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'ADD',
                     _vector(nodes, links, 'SCALE', sampled_normal, scale=_math(nodes, links, 'SUBTRACT', 1.0, near)),
                     _vector(nodes, links, 'SCALE', _vector(nodes, links, 'NORMALIZE', away), scale=near)))
    # New cut vertices can inherit a zero attribute from B. Sample A's
    # untouched surface instead of trusting Boolean attribute interpolation.
    own_shaded = _smooth_normals(nodes, links, gin.outputs['Geometry'], explicit_faces=True)
    own = _surface_normal_sampler(nodes, links, own_shaded)
    links.new(position, own.inputs['Sample Position'])
    own_normal = _vector(nodes, links, 'NORMALIZE', own.outputs['Value'])
    tangent = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'SUBTRACT', own_normal,
                      _vector(nodes, links, 'SCALE', normal,
                              scale=_vector(nodes, links, 'DOT_PRODUCT', own_normal, normal))))
    weight = _falloff_weight(nodes, links, gin, distance, influence)
    pull = _normal_pull(nodes, links, gin, distance, merge, weight)
    slid = _vector(nodes, links, 'ADD', q, _vector(nodes, links, 'SCALE', tangent,
                   scale=_math(nodes, links, 'MULTIPLY', push, weight)))
    landing = nodes.new('GeometryNodeProximity')
    landing.target_element = 'FACES'
    links.new(target, landing.inputs['Target'])
    links.new(slid, landing.inputs['Source Position'])
    # Nearest projection piles points onto faceted cylinder seams. A ray
    # preserves their tangential spacing while following the curved target.
    reach = _math(nodes, links, 'ADD', _math(nodes, links, 'ADD', influence, push), 0.01)
    ray = nodes.new('GeometryNodeRaycast')
    links.new(target, ray.inputs['Target Geometry'])
    links.new(_vector(nodes, links, 'ADD', slid, _vector(nodes, links, 'SCALE', normal, scale=reach)),
              ray.inputs['Source Position'])
    links.new(_vector(nodes, links, 'SCALE', normal, scale=-1.0), ray.inputs['Ray Direction'])
    links.new(_math(nodes, links, 'MULTIPLY', reach, 2.0), ray.inputs['Ray Length'])
    landed = _switch(nodes, links, 'VECTOR', ray.outputs['Is Hit'], landing.outputs['Position'], ray.outputs['Hit Position'])
    landed, spread_planned, spread_taken = _spread_over_surface(
        nodes, links, shaded, target, landed, tangent, spread, weight, spreading,
        gin.outputs[ADHESION_ADAPTIVE_SPREAD], corner_reach)
    landed_normal = _surface_normal_sampler(nodes, links, shaded)
    links.new(landed, landed_normal.inputs['Sample Position'])
    target_lift = _vector(nodes, links, 'NORMALIZE', landed_normal.outputs['Value'])
    bend = _math(nodes, links, 'MULTIPLY', weight,
                 _math(nodes, links, 'MINIMUM', 1.0,
                       _math(nodes, links, 'DIVIDE', _math(nodes, links, 'ADD', push, spread),
                             _math(nodes, links, 'MAXIMUM', influence, 1e-6))))
    lift_normal = _vector(nodes, links, 'NORMALIZE', _vector(nodes, links, 'ADD',
                          _vector(nodes, links, 'SCALE', normal, scale=_math(nodes, links, 'SUBTRACT', 1.0, bend)),
                          _vector(nodes, links, 'SCALE', target_lift, scale=bend)))
    # Apply a displacement to A rather than reconstructing A from a sampled
    # shading normal. Reconstruction introduced a facet-sized jump at the
    # influence boundary even when push and pull had already faded to zero.
    travelled = _math(nodes, links, 'GREATER_THAN',
                      _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'ADD', push, spread), weight), 1e-8)
    landed = _switch(nodes, links, 'VECTOR', travelled, q, landed)
    shift = _vector(nodes, links, 'SUBTRACT', landed, q)
    rotation = _vector(nodes, links, 'SCALE', _vector(nodes, links, 'SUBTRACT', lift_normal, normal), scale=distance)
    fitted = _vector(nodes, links, 'SUBTRACT',
                     _vector(nodes, links, 'ADD', position, _vector(nodes, links, 'ADD', shift, rotation)),
                     _vector(nodes, links, 'SCALE', lift_normal, scale=pull))
    clearance = _math(nodes, links, 'MULTIPLY', spreading,
                      _math(nodes, links, 'MULTIPLY', square(weight),
                            _math(nodes, links, 'MULTIPLY', gin.outputs[SURFACE_CLEARANCE], gin.outputs[GLOBAL])))
    fitted = _vector(nodes, links, 'ADD', fitted, _vector(nodes, links, 'SCALE', target_lift, scale=clearance))
    # Preserve positions outside the influence band EXACTLY, including caps.
    fitted = _switch(nodes, links, 'VECTOR', _math(nodes, links, 'GREATER_THAN', weight, 1e-8), position, fitted)
    geometry = prepared
    source_position = nodes.new('GeometryNodeStoreNamedAttribute')
    source_position.data_type, source_position.domain = 'FLOAT_VECTOR', 'POINT'
    source_position.inputs['Name'].default_value = ADHESION_SOURCE_POSITION
    links.new(geometry, source_position.inputs['Geometry'])
    links.new(position, source_position.inputs['Value'])
    geometry = source_position.outputs['Geometry']
    for name, value in ((ADHESION_WEIGHT, weight), (ADHESION_DISTANCE, distance),
                        (ADHESION_SPREAD_PLANNED, spread_planned), (ADHESION_SPREAD_TAKEN, spread_taken)):
        store = nodes.new('GeometryNodeStoreNamedAttribute')
        store.data_type, store.domain = 'FLOAT', 'POINT'
        store.inputs['Name'].default_value = name
        links.new(geometry, store.inputs['Geometry'])
        links.new(value, store.inputs['Value'])
        geometry = store.outputs['Geometry']
    move = nodes.new('GeometryNodeSetPosition')
    links.new(geometry, move.inputs['Geometry'])
    # Safe even with global refinement: evaluate the expensive fitting field
    # only for affected points, rather than rebuilding untouched positions.
    links.new(_math(nodes, links, 'GREATER_THAN', weight, 1e-8), move.inputs['Selection'])
    links.new(fitted, move.inputs['Position'])
    result = subdivide(move.outputs['Geometry'],
                       _switch(nodes, links, 'INT', before, gin.outputs[ADHESION_LEVELS], 0), 'After adhesion')
    # After Snap interpolates a coarse curved boundary off B. Reattach just
    # that boundary; do not refit its interior or erase the order distinction.
    original_distance = nodes.new('GeometryNodeInputNamedAttribute')
    original_distance.data_type = 'FLOAT'
    original_distance.inputs['Name'].default_value = ADHESION_DISTANCE
    boundary_projection = nodes.new('GeometryNodeProximity')
    boundary_projection.target_element = 'FACES'
    links.new(target, boundary_projection.inputs['Target'])
    repair = nodes.new('GeometryNodeSetPosition')
    links.new(result, repair.inputs['Geometry'])
    links.new(_math(nodes, links, 'LESS_THAN', original_distance.outputs['Attribute'], 1e-6),
              repair.inputs['Selection'])
    repaired_sample = _surface_normal_sampler(nodes, links, shaded)
    links.new(boundary_projection.outputs['Position'], repaired_sample.inputs['Sample Position'])
    boundary_gap = _math(nodes, links, 'MULTIPLY', spreading,
                         _math(nodes, links, 'MULTIPLY', gin.outputs[SURFACE_CLEARANCE], gin.outputs[GLOBAL]))
    links.new(_vector(nodes, links, 'ADD', boundary_projection.outputs['Position'],
                      _vector(nodes, links, 'SCALE', _vector(nodes, links, 'NORMALIZE', repaired_sample.outputs['Value']),
                              scale=boundary_gap)), repair.inputs['Position'])
    result = _switch(nodes, links, 'GEOMETRY', before, repair.outputs['Geometry'], result)
    result = _relax_spread(nodes, links, result, target, shaded, gin.outputs[SURFACE_RELAX],
                           gin.outputs[SURFACE_RELAX_PASSES], influence, spreading, boundary_gap)
    result = joins.smooth_by_angle(nodes, links, result, NORMAL_ANGLE)
    result = _match_contact_normals(nodes, links, result, gin, shaded, merge)
    valid = nodes.new('GeometryNodeAttributeDomainSize')
    valid.component = 'MESH'
    links.new(target, valid.inputs['Geometry'])
    active = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'GREATER_THAN', influence, 1e-6),
                   _math(nodes, links, 'GREATER_THAN', valid.outputs['Face Count'], 0.0))
    effect = _math(nodes, links, 'GREATER_THAN',
                   _math(nodes, links, 'ADD', merge, _math(nodes, links, 'ADD', push, spread)), 1e-6)
    active = _math(nodes, links, 'MULTIPLY', active, effect)
    links.new(_switch(nodes, links, 'GEOMETRY', active, gin.outputs['Geometry'], result), gout.inputs['Geometry'])
