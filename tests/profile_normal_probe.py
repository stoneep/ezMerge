"""Final-stage normal-only probes, with a dynamic modifier fallback for 4.2."""


def add_normal_probe(obj, base, builder, *, mode):
    import bpy
    radius = 0.15
    if mode == 'transfer':
        name = 'Probe Live Normal Mask'
        group = obj.vertex_groups.new(name=name)
        group.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')
        proximity = obj.modifiers.new('Live Normal Weight', 'VERTEX_WEIGHT_PROXIMITY')
        proximity.vertex_group = group.name
        proximity.target = base
        proximity.proximity_mode = 'GEOMETRY'
        proximity.proximity_geometry = {'FACE'}
        proximity.min_dist, proximity.max_dist = radius, 0.0
        proximity.falloff_type = 'SMOOTH'
        transfer = obj.modifiers.new('Contact Normal Transfer', 'DATA_TRANSFER')
        transfer.object = base
        transfer.use_loop_data = True
        transfer.data_types_loops = {'CUSTOM_NORMAL'}
        transfer.loop_mapping = 'POLYINTERP_NEAREST'
        transfer.mix_mode = 'MIX'
        transfer.mix_factor = 1.0
        transfer.vertex_group = group.name
        return [proximity, transfer], [], group
    if mode != 'gn' or not hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
        raise ValueError('GN custom normals unavailable; use transfer on Blender 4.2')
    group = bpy.data.node_groups.new('Final contact normals', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    info.inputs['Object'].default_value = base
    near = nodes.new('GeometryNodeProximity')
    near.target_element = 'FACES'
    links.new(info.outputs['Geometry'], near.inputs['Target'])
    shaded = builder._smooth_normals(nodes, links, info.outputs['Geometry'], explicit_faces=True)
    sample = builder._surface_normal_sampler(nodes, links, shaded)
    links.new(near.outputs['Position'], sample.inputs['Sample Position'])
    m, v = builder._math, builder._vector
    u = m(nodes, links, 'DIVIDE', near.outputs['Distance'], radius, clamp=True)
    smooth = m(nodes, links, 'MULTIPLY', m(nodes, links, 'MULTIPLY', u, u),
               m(nodes, links, 'SUBTRACT', 3.0, m(nodes, links, 'MULTIPLY', u, 2.0)))
    weight = m(nodes, links, 'SUBTRACT', 1.0, smooth)
    own = nodes.new('GeometryNodeInputNormal').outputs[0]
    normal = v(nodes, links, 'NORMALIZE', v(nodes, links, 'ADD',
                v(nodes, links, 'SCALE', own, scale=smooth),
                v(nodes, links, 'SCALE', sample.outputs['Value'], scale=weight)))
    set_normal = nodes.new('GeometryNodeSetMeshNormal')
    set_normal.mode, set_normal.domain = 'FREE', 'CORNER'
    links.new(gin.outputs['Geometry'], set_normal.inputs['Mesh'])
    links.new(normal, set_normal.inputs['Custom Normal'])
    links.new(set_normal.outputs['Mesh'], gout.inputs['Geometry'])
    modifier = obj.modifiers.new('Final Contact Normals', 'NODES')
    modifier.node_group = group
    return [modifier], [group], None
