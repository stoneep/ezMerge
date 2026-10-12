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
    if mode not in ('gn','gn-safe','gn-facing','gn-source') or not hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
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
    mask_distance = near.outputs['Distance']
    if mode == 'gn-source':
        source = nodes.new('GeometryNodeInputNamedAttribute')
        source.data_type = 'FLOAT'
        source.inputs['Name'].default_value = builder.ADHESION_DISTANCE
        mask_distance = source.outputs['Attribute']
    u = m(nodes, links, 'DIVIDE', mask_distance, radius, clamp=True)
    smooth = m(nodes, links, 'MULTIPLY', m(nodes, links, 'MULTIPLY', u, u),
               m(nodes, links, 'SUBTRACT', 3.0, m(nodes, links, 'MULTIPLY', u, 2.0)))
    weight = m(nodes, links, 'SUBTRACT', 1.0, smooth)
    own = nodes.new('GeometryNodeInputNormal').outputs[0]
    target_normal = sample.outputs['Value']
    if mode == 'gn-facing':
        # Never reverse B's normal on the underside. Fade its influence using
        # A's continuous shading normal, rather than a per-face sign switch.
        facing = m(nodes, links, 'DIVIDE',
                   v(nodes, links, 'DOT_PRODUCT', own, target_normal), 0.5, clamp=True)
        facing = m(nodes, links, 'MULTIPLY', m(nodes, links, 'MULTIPLY', facing, facing),
                   m(nodes, links, 'SUBTRACT', 3.0, m(nodes, links, 'MULTIPLY', facing, 2.0)))
        weight = m(nodes, links, 'MULTIPLY', weight, facing)
        smooth = m(nodes, links, 'SUBTRACT', 1.0, weight)
    if mode == 'gn-safe':
        face = nodes.new('GeometryNodeFaceOfCorner')
        links.new(nodes.new('GeometryNodeInputIndex').outputs[0],face.inputs['Corner Index'])
        geometric = nodes.new('GeometryNodeSampleIndex')
        geometric.data_type,geometric.domain = 'FLOAT_VECTOR','FACE'
        links.new(gin.outputs['Geometry'],geometric.inputs['Geometry'])
        links.new(nodes.new('GeometryNodeInputNormal').outputs[0],geometric.inputs['Value'])
        links.new(face.outputs['Face Index'],geometric.inputs['Index'])
        reversed_normal = m(nodes,links,'LESS_THAN',v(nodes,links,'DOT_PRODUCT',geometric.outputs['Value'],target_normal),0.0)
        sign = builder._switch(nodes,links,'FLOAT',reversed_normal,1.0,-1.0)
        target_normal = v(nodes,links,'SCALE',target_normal,scale=sign)
    normal = v(nodes, links, 'NORMALIZE', v(nodes, links, 'ADD',
                v(nodes, links, 'SCALE', own, scale=smooth),
                v(nodes, links, 'SCALE', target_normal, scale=weight)))
    set_normal = nodes.new('GeometryNodeSetMeshNormal')
    set_normal.mode, set_normal.domain = 'FREE', 'CORNER'
    links.new(gin.outputs['Geometry'], set_normal.inputs['Mesh'])
    links.new(normal, set_normal.inputs['Custom Normal'])
    links.new(set_normal.outputs['Mesh'], gout.inputs['Geometry'])
    modifier = obj.modifiers.new('Final Contact Normals', 'NODES')
    modifier.node_group = group
    return [modifier], [group], None
