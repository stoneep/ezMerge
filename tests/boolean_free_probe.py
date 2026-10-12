"""Diagnostic only: bypass the pre-deformation cut in a private node group.

No new user-facing mode/default is introduced until the result is evaluated.
"""

import inspect
import math
import types


def _coherent_displacement(builder, nodes, links, geometry, target, shaded, influence, count):
    """Diagnostic Jacobi passes with a signed-side guard, no frame state.

    Higher budget than production Relax deliberately tests whether spatial
    continuity is relevant. Not a production solver or a self-intersection fix.
    """
    m, v, switch = builder._math, builder._vector, builder._switch
    def attr(name, dtype):
        node = nodes.new('GeometryNodeInputNamedAttribute')
        node.data_type = dtype
        node.inputs['Name'].default_value = name
        return node.outputs['Attribute']
    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    original = attr(builder.ADHESION_SOURCE_POSITION, 'FLOAT_VECTOR')
    weight = attr(builder.ADHESION_WEIGHT, 'FLOAT')
    distance = attr(builder.ADHESION_DISTANCE, 'FLOAT')
    selection = m(nodes, links, 'MULTIPLY', m(nodes, links, 'GREATER_THAN', weight, 1e-8),
                  m(nodes, links, 'GREATER_THAN', distance, 1e-6))
    bell = m(nodes, links, 'MULTIPLY', 4.0, m(nodes, links, 'MULTIPLY', weight, m(nodes, links, 'SUBTRACT', 1.0, weight)))
    amount = m(nodes, links, 'MULTIPLY', 0.65, bell)
    def signed(point):
        near = nodes.new('GeometryNodeProximity')
        near.target_element = 'FACES'
        links.new(target, near.inputs['Target'])
        links.new(point, near.inputs['Source Position'])
        sample = builder._surface_normal_sampler(nodes, links, shaded)
        links.new(near.outputs['Position'], sample.inputs['Sample Position'])
        normal = v(nodes, links, 'NORMALIZE', sample.outputs['Value'])
        return m(nodes, links, 'LESS_THAN', v(nodes, links, 'DOT_PRODUCT', v(nodes, links, 'SUBTRACT', point, near.outputs['Position']), normal), 0.0)
    was_inside = signed(original)
    for _ in range(count):
        delta = v(nodes, links, 'SUBTRACT', position, original)
        blur = nodes.new('GeometryNodeBlurAttribute')
        blur.data_type = 'FLOAT_VECTOR'
        blur.inputs['Iterations'].default_value = 1
        links.new(delta, blur.inputs['Value'])
        links.new(selection, blur.inputs['Weight'])
        correction = v(nodes, links, 'SUBTRACT', blur.outputs['Value'], delta)
        length = v(nodes, links, 'LENGTH', correction)
        scale = m(nodes, links, 'MINIMUM', amount,
                  m(nodes, links, 'DIVIDE', m(nodes, links, 'MULTIPLY', influence, 0.1), m(nodes, links, 'MAXIMUM', length, 1e-8)))
        candidate = v(nodes, links, 'ADD', position, v(nodes, links, 'SCALE', correction, scale=scale))
        crossed = m(nodes, links, 'COMPARE', signed(candidate), was_inside, 0.1)
        # Reject crossings on either side, rather than snapping hidden points
        # onto the visible surface. This preserves side but can limit smoothing.
        candidate = switch(nodes, links, 'VECTOR', crossed, position, candidate)
        move = nodes.new('GeometryNodeSetPosition')
        links.new(geometry, move.inputs['Geometry'])
        links.new(selection, move.inputs['Selection'])
        links.new(candidate, move.inputs['Position'])
        geometry = move.outputs['Geometry']
    return geometry


def _limit_landing(builder, nodes, links, geometry, fitted, count, preserve_order, *, target=None, shaded=None, soft=False):
    """Back off unsafe candidate displacements before the final Set Position.

    Source-edge direction is a conservative chart-order proxy, not a general
    fold/self-intersection test: a legitimate large rotation can fail it too.
    This deliberately trades coverage for safety, to measure that tradeoff.
    """
    m, v, switch = builder._math, builder._vector, builder._switch
    candidate_name = 'probe_landing_candidate'
    def attr(name, dtype='FLOAT_VECTOR'):
        node = nodes.new('GeometryNodeInputNamedAttribute')
        node.data_type = dtype
        node.inputs['Name'].default_value = name
        return node.outputs['Attribute']
    def store(mesh, value):
        node = nodes.new('GeometryNodeStoreNamedAttribute')
        node.data_type, node.domain = 'FLOAT_VECTOR', 'POINT'
        node.inputs['Name'].default_value = candidate_name
        links.new(mesh, node.inputs['Geometry'])
        links.new(value, node.inputs['Value'])
        return node.outputs['Geometry']
    original = attr(builder.ADHESION_SOURCE_POSITION)
    current = attr(candidate_name)
    weight = attr(builder.ADHESION_WEIGHT, 'FLOAT')
    distance = attr(builder.ADHESION_DISTANCE, 'FLOAT')
    eligible = m(nodes, links, 'MULTIPLY', m(nodes, links, 'GREATER_THAN', weight, 1e-8),
                 m(nodes, links, 'GREATER_THAN', distance, 1e-6))
    geometry = store(geometry, fitted)
    for _ in range(count):
        vertices = nodes.new('GeometryNodeInputMeshEdgeVertices')
        def at(field, index):
            sample = nodes.new('GeometryNodeSampleIndex')
            sample.data_type, sample.domain = 'FLOAT_VECTOR', 'POINT'
            links.new(geometry, sample.inputs['Geometry'])
            links.new(field, sample.inputs['Value'])
            links.new(index, sample.inputs['Index'])
            return sample.outputs['Value']
        a, b = vertices.outputs['Vertex Index 1'], vertices.outputs['Vertex Index 2']
        rest = v(nodes, links, 'SUBTRACT', at(original, b), at(original, a))
        planned = v(nodes, links, 'SUBTRACT', at(current, b), at(current, a))
        rest_length = v(nodes, links, 'LENGTH', rest)
        long_edge = m(nodes, links, 'GREATER_THAN', v(nodes, links, 'LENGTH', planned), m(nodes, links, 'MULTIPLY', rest_length, 4.0))
        invalid = long_edge
        if preserve_order:
            reference = rest
            reliable = 1.0
            if target is not None:
                def normal(point):
                    sample = builder._surface_normal_sampler(nodes, links, shaded)
                    links.new(point, sample.inputs['Sample Position'])
                    return v(nodes, links, 'NORMALIZE', sample.outputs['Value'])
                n0 = v(nodes, links, 'NORMALIZE', v(nodes, links, 'ADD', normal(at(original, a)), normal(at(original, b))))
                n1 = v(nodes, links, 'NORMALIZE', v(nodes, links, 'ADD', normal(at(current, a)), normal(at(current, b))))
                axis = v(nodes, links, 'CROSS_PRODUCT', n0, n1)
                cosine = v(nodes, links, 'DOT_PRODUCT', n0, n1)
                cross = v(nodes, links, 'CROSS_PRODUCT', axis, rest)
                reference = v(nodes, links, 'ADD', rest, v(nodes, links, 'ADD', cross,
                              v(nodes, links, 'SCALE', v(nodes, links, 'CROSS_PRODUCT', axis, cross),
                                scale=m(nodes, links, 'DIVIDE', 1.0, m(nodes, links, 'MAXIMUM', m(nodes, links, 'ADD', 1.0, cosine), 0.2)))))
                # Opposing normals have no unique minimal rotation axis.
                # Don't pretend this local frame is reliable in that case.
                reliable = m(nodes, links, 'GREATER_THAN', cosine, -0.8)
            reversed_edge = m(nodes, links, 'LESS_THAN', v(nodes, links, 'DOT_PRODUCT', planned, reference),
                              m(nodes, links, 'MULTIPLY', v(nodes, links, 'DOT_PRODUCT', rest, rest), 0.05))
            reversed_edge = m(nodes, links, 'MULTIPLY', reversed_edge, reliable)
            invalid = m(nodes, links, 'MAXIMUM', invalid, reversed_edge)
        invalid = m(nodes, links, 'MULTIPLY', invalid, m(nodes, links, 'GREATER_THAN', rest_length, 1e-6))
        edge_field = nodes.new('GeometryNodeFieldOnDomain')
        edge_field.data_type, edge_field.domain = 'FLOAT', 'EDGE'
        links.new(invalid, edge_field.inputs['Value'])
        point_field = nodes.new('GeometryNodeFieldOnDomain')
        point_field.data_type, point_field.domain = 'FLOAT', 'POINT'
        links.new(edge_field.outputs['Value'], point_field.inputs['Value'])
        unsafe = m(nodes, links, 'MULTIPLY', eligible,
                   m(nodes, links, 'GREATER_THAN', point_field.outputs['Value'], 1e-8))
        if soft:
            blur = nodes.new('GeometryNodeBlurAttribute')
            blur.data_type = 'FLOAT'
            blur.inputs['Iterations'].default_value = 2
            links.new(unsafe, blur.inputs['Value'])
            fraction = m(nodes, links, 'MULTIPLY', eligible, blur.outputs['Value'])
            retain = m(nodes, links, 'SUBTRACT', 1.0, m(nodes, links, 'MULTIPLY', 0.5, fraction))
            backed_off = v(nodes, links, 'ADD', original,
                            v(nodes, links, 'SCALE', v(nodes, links, 'SUBTRACT', current, original), scale=retain))
            geometry = store(geometry, backed_off)
            continue
        backed_off = v(nodes, links, 'ADD', original,
                        v(nodes, links, 'SCALE', v(nodes, links, 'SUBTRACT', current, original), scale=0.5))
        geometry = store(geometry, switch(nodes, links, 'VECTOR', unsafe, current, backed_off))
    return geometry, attr(candidate_name)


def bypass_adhesion_boolean(mod):
    group = mod.node_group
    cuts = [n for n in group.nodes if n.bl_idname == 'GeometryNodeMeshBoolean' and n.operation == 'DIFFERENCE']
    if len(cuts) != 1 or group.users != 1:
        raise ValueError('Probe requires a private adhesion group with one Difference node.')
    cut = cuts[0]
    source = cut.inputs[0].links[0].from_socket
    drops = [link.to_node for link in cut.outputs['Mesh'].links
             if link.to_node.bl_idname == 'GeometryNodeDeleteGeometry']
    if len(drops) != 1:
        raise ValueError('Cannot identify the cut-face removal stage.')
    output = drops[0].outputs['Geometry']
    consumers = [link.to_socket for link in output.links]
    if not consumers:
        raise ValueError('No downstream adhesion consumer found.')
    for destination in consumers:
        group.links.new(source, destination)
    # Remove the unused cut/removal nodes so the probe contains no Boolean
    # operation at all, rather than relying only on evaluation laziness.
    group.nodes.remove(drops[0])
    group.nodes.remove(cut)
    group.update_tag()
    return group


def build_signed_probe(builder, group, *, mode='signed'):
    """Compile a guarded diagnostic variant; production builder stays intact.

    Nearest outward-normal sign is only a local inside/outside estimate, not
    an exact SDF for arbitrary concave/open/non-manifold targets. Test closed
    cylinders first. Keep unsigned distance for influence and stored metadata,
    but use its sign for the deformation frame and normal displacement.
    """
    source = inspect.getsource(builder._build_adhesion)
    replacements = (
        ("    near = _math(nodes, links, 'DIVIDE', distance,",
         "    signed_dot = _vector(nodes, links, 'DOT_PRODUCT', away, sampled_normal)\n"
         "    orientation = _switch(nodes, links, 'FLOAT', _math(nodes, links, 'LESS_THAN', signed_dot, 0.0), 1.0, -1.0)\n"
         "    signed_distance = _math(nodes, links, 'MULTIPLY', distance, orientation)\n"
         "    away = _vector(nodes, links, 'SCALE', away, scale=orientation)\n"
         "    near = _math(nodes, links, 'DIVIDE', distance,"),
        ("    pull = _normal_pull(nodes, links, gin, distance, merge, weight)",
         "    pull = _math(nodes, links, 'MULTIPLY', _normal_pull(nodes, links, gin, distance, merge, weight), orientation)"),
        ("scale=distance)\n    fitted =", "scale=signed_distance)\n    fitted ="),
        ("    fitted = _vector(nodes, links, 'ADD', fitted, _vector(nodes, links, 'SCALE', target_lift, scale=clearance))",
         "    clearance = _math(nodes, links, 'MULTIPLY', clearance, _math(nodes, links, 'GREATER_THAN', signed_distance, 0.0))\n"
         "    fitted = _vector(nodes, links, 'ADD', fitted, _vector(nodes, links, 'SCALE', target_lift, scale=clearance))"),
    )
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError('Production builder changed; review the signed-distance probe.')
        source = source.replace(before, after)
    variants = {
        'signed': None,
        'no-rotation': ("scale=signed_distance)\n    fitted =", "scale=_math(nodes, links, 'MULTIPLY', signed_distance, 0.0))\n    fitted ="),
        'nearest-landing': ("    landed = _switch(nodes, links, 'VECTOR', ray.outputs['Is Hit'], landing.outputs['Position'], ray.outputs['Hit Position'])",
                            "    landed = landing.outputs['Position']"),
        'smooth-target': ("    shaded = _smooth_normals(nodes, links, target, explicit_faces=True)",
                          "    shaded = _probe_target_normals(nodes, links, target, explicit_faces=True)"),
        'coherent4': ("    result = joins.smooth_by_angle(nodes, links, result, NORMAL_ANGLE)",
                      "    result = _probe_coherent(nodes, links, result, target, shaded, influence, 4)\n    result = joins.smooth_by_angle(nodes, links, result, NORMAL_ANGLE)"),
        'coherent8': ("    result = joins.smooth_by_angle(nodes, links, result, NORMAL_ANGLE)",
                      "    result = _probe_coherent(nodes, links, result, target, shaded, influence, 8)\n    result = joins.smooth_by_angle(nodes, links, result, NORMAL_ANGLE)"),
        'limit-stretch': ("    move = nodes.new('GeometryNodeSetPosition')",
                          "    geometry, fitted = _probe_limit(nodes, links, geometry, fitted, 8, False)\n    move = nodes.new('GeometryNodeSetPosition')"),
        'limit-order': ("    move = nodes.new('GeometryNodeSetPosition')",
                        "    geometry, fitted = _probe_limit(nodes, links, geometry, fitted, 8, True)\n    move = nodes.new('GeometryNodeSetPosition')"),
        'transport-order': ("    move = nodes.new('GeometryNodeSetPosition')",
                            "    geometry, fitted = _probe_transport(nodes, links, geometry, fitted, target, shaded, False)\n    move = nodes.new('GeometryNodeSetPosition')"),
        'transport-soft': ("    move = nodes.new('GeometryNodeSetPosition')",
                           "    geometry, fitted = _probe_transport(nodes, links, geometry, fitted, target, shaded, True)\n    move = nodes.new('GeometryNodeSetPosition')"),
        'order-soft': ("    move = nodes.new('GeometryNodeSetPosition')",
                       "    geometry, fitted = _probe_limit_soft(nodes, links, geometry, fitted)\n    move = nodes.new('GeometryNodeSetPosition')"),
        'tangent-damped': None,
        'tangent-soft': None,
    }
    if mode not in variants:
        raise ValueError('Unknown diagnostic mode: ' + mode)
    if variants[mode]:
        before, after = variants[mode]
        if source.count(before) != 1:
            raise ValueError('Production builder changed; review corner diagnostic.')
        source = source.replace(before, after)
    if mode.startswith('tangent-'):
        before = "    weight = _falloff_weight(nodes, links, gin, distance, influence)"
        if source.count(before) != 1:
            raise ValueError('Production tangent probe anchor changed.')
        attenuation = (
            "    tangent_length = _vector(nodes, links, 'LENGTH', _vector(nodes, links, 'SUBTRACT', own_normal,\n"
            "                          _vector(nodes, links, 'SCALE', normal, scale=_vector(nodes, links, 'DOT_PRODUCT', own_normal, normal))))\n"
            "    attenuation = _math(nodes, links, 'MINIMUM', tangent_length, 1.0)\n")
        if mode == 'tangent-soft':
            attenuation += (
                "    u = _math(nodes, links, 'DIVIDE', attenuation, 0.25, clamp=True)\n"
                "    attenuation = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', u, u),\n"
                "                        _math(nodes, links, 'SUBTRACT', 3.0, _math(nodes, links, 'MULTIPLY', u, 2.0)))\n")
        source = source.replace(before, attenuation +
            "    control_push, control_spread = push, spread\n"
            "    push = _math(nodes, links, 'MULTIPLY', push, attenuation)\n"
            "    spread = _math(nodes, links, 'MULTIPLY', spread, attenuation)\n" + before)
        before_effect = "_math(nodes, links, 'ADD', merge, _math(nodes, links, 'ADD', push, spread)), 1e-6)"
        if source.count(before_effect) != 1:
            raise ValueError('Production effect gate changed.')
        # Geometry Switch cannot use a per-point field. The bypass gate must
        # use uniform controls, not the attenuated per-point travel lengths.
        source = source.replace(before_effect,
            "_math(nodes, links, 'ADD', merge, _math(nodes, links, 'ADD', control_push, control_spread)), 1e-6)")
    # Capture actual raw tangent magnitudes at the original vertices. This
    # tests whether the worst edge lies near the normalization singularity.
    before_trace = "for name, value in ((ADHESION_WEIGHT, weight), (ADHESION_DISTANCE, distance),"
    if source.count(before_trace) != 1:
        raise ValueError('Production metadata loop changed.')
    source = source.replace(before_trace,
        "for name, value in (('probe_raw_tangent_length', _vector(nodes, links, 'LENGTH',\n"
        "                            _vector(nodes, links, 'SUBTRACT', own_normal, _vector(nodes, links, 'SCALE', normal,\n"
        "                                scale=_vector(nodes, links, 'DOT_PRODUCT', own_normal, normal))))),\n"
        "                        (ADHESION_WEIGHT, weight), (ADHESION_DISTANCE, distance),")
    namespace = dict(vars(builder))
    namespace['_probe_coherent'] = lambda *args: _coherent_displacement(builder, *args)
    namespace['_probe_limit'] = lambda *args: _limit_landing(builder, *args)
    namespace['_probe_transport'] = lambda nodes, links, geometry, fitted, target, shaded, soft: _limit_landing(
        builder, nodes, links, geometry, fitted, 8, True, target=target, shaded=shaded, soft=soft)
    namespace['_probe_limit_soft'] = lambda nodes, links, geometry, fitted: _limit_landing(
        builder, nodes, links, geometry, fitted, 8, True, soft=True)
    helper = builder._smooth_normals
    # Private helper globals: smooth only B's sampled field across its cap,
    # never alter B's mesh or the production/source normal policy.
    namespace['_probe_target_normals'] = types.FunctionType(
        helper.__code__, dict(helper.__globals__, NORMAL_ANGLE=math.radians(100)),
        helper.__name__, helper.__defaults__, helper.__closure__)
    exec(compile(source, '<diagnostic signed adhesion>', 'exec'), namespace)
    namespace['_build_adhesion'](group)
