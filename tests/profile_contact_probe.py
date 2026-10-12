"""Diagnostic post-subdivision reattachment; no snapshots or topology edits."""


def add_contact_probe(obj, base, builder, *, mode):
    import bpy
    if mode not in ('hard', 'soft'):
        raise ValueError('Unknown contact probe')
    group = bpy.data.node_groups.new('Contact reattachment '+mode, 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    info.inputs['Object'].default_value = base
    distance = nodes.new('GeometryNodeInputNamedAttribute')
    distance.data_type = 'FLOAT'
    distance.inputs['Name'].default_value = builder.ADHESION_DISTANCE
    d = distance.outputs['Attribute']
    m, v = builder._math, builder._vector
    if mode == 'hard':
        weight = m(nodes, links, 'LESS_THAN', d, 1e-6)
    else:
        # Physical source-distance support, not a fixed number of topology rows.
        u = m(nodes, links, 'DIVIDE', d, 0.25, clamp=True)
        smooth = m(nodes, links, 'MULTIPLY', m(nodes, links, 'MULTIPLY', u, u),
                   m(nodes, links, 'SUBTRACT', 3.0, m(nodes, links, 'MULTIPLY', u, 2.0)))
        weight = m(nodes, links, 'SUBTRACT', 1.0, smooth)
    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    near = nodes.new('GeometryNodeProximity')
    near.target_element = 'FACES'
    links.new(info.outputs['Geometry'], near.inputs['Target'])
    links.new(position, near.inputs['Source Position'])
    fitted = v(nodes, links, 'ADD', position,
               v(nodes, links, 'SCALE', v(nodes, links, 'SUBTRACT', near.outputs['Position'], position), scale=weight))
    move = nodes.new('GeometryNodeSetPosition')
    links.new(gin.outputs['Geometry'], move.inputs['Geometry'])
    links.new(m(nodes, links, 'GREATER_THAN', weight, 1e-8), move.inputs['Selection'])
    links.new(fitted, move.inputs['Position'])
    links.new(move.outputs['Geometry'], gout.inputs['Geometry'])
    mod = obj.modifiers.new('Contact '+mode, 'NODES')
    mod.node_group = group
    return mod, group
