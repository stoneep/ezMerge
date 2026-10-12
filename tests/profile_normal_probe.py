"""Final-stage normal-only probes, with a dynamic modifier fallback for 4.2."""


def add_normal_probe(obj, base, builder, *, mode):
    import bpy
    radius = 0.15
    if mode in ('transfer','transfer-replace'):
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
        print('TRANSFER default mix mode',transfer.mix_mode,flush=True)
        transfer.mix_mode = 'REPLACE' if mode=='transfer-replace' else 'MIX'
        transfer.mix_factor = 1.0
        transfer.vertex_group = group.name
        return [proximity, transfer], [], group
    if mode not in ('gn','gn-safe','gn-facing','gn-source','gn-bounded','gn-shared','gn-shared-fast','gn-shared-cached') or not hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
        raise ValueError('GN custom normals unavailable; use transfer on Blender 4.2')
    group = bpy.data.node_groups.new('Final contact normals', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    source_geometry = gin.outputs['Geometry']
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
    if mode in ('gn-safe','gn-bounded','gn-shared','gn-shared-fast','gn-shared-cached'):
        face = nodes.new('GeometryNodeFaceOfCorner')
        links.new(nodes.new('GeometryNodeInputIndex').outputs[0],face.inputs['Corner Index'])
        geometric = nodes.new('GeometryNodeSampleIndex')
        geometric.data_type,geometric.domain = 'FLOAT_VECTOR','FACE'
        links.new(gin.outputs['Geometry'],geometric.inputs['Geometry'])
        links.new(nodes.new('GeometryNodeInputNormal').outputs[0],geometric.inputs['Value'])
        links.new(face.outputs['Face Index'],geometric.inputs['Index'])
        if mode == 'gn-safe':
            reversed_normal = m(nodes,links,'LESS_THAN',v(nodes,links,'DOT_PRODUCT',geometric.outputs['Value'],target_normal),0.0)
            sign = builder._switch(nodes,links,'FLOAT',reversed_normal,1.0,-1.0)
            target_normal = v(nodes,links,'SCALE',target_normal,scale=sign)
        else:
            # A*(1-w)+B*w must remain in the geometric normal's hemisphere.
            # Bound w continuously using dot products; never flip B's normal.
            # Compatible targets retain w=1. Opposing targets get a small
            # safety margin to avoid an antiparallel zero-vector blend.
            a = m(nodes,links,'MAXIMUM',v(nodes,links,'DOT_PRODUCT',geometric.outputs['Value'],own),0.0)
            negative_b = m(nodes,links,'MAXIMUM',m(nodes,links,'MULTIPLY',
                           v(nodes,links,'DOT_PRODUCT',geometric.outputs['Value'],target_normal),-1.0),0.0)
            limit = m(nodes,links,'DIVIDE',a,m(nodes,links,'MAXIMUM',m(nodes,links,'ADD',a,negative_b),1e-6))
            limit = m(nodes,links,'MULTIPLY',limit,m(nodes,links,'SUBTRACT',1.0,
                       m(nodes,links,'MULTIPLY',negative_b,0.02)))
            if mode in ('gn-shared','gn-shared-fast','gn-shared-cached'):
                # All corners at a vertex use the most conservative bound.
                # Keep their own shading normals (including sharp splits).
                # Diagnostic fixtures assert valence <=64; this is not yet a
                # general production solution for arbitrary mesh valence.
                vertex = nodes.new('GeometryNodeVertexOfCorner')
                links.new(nodes.new('GeometryNodeInputIndex').outputs[0],vertex.inputs['Corner Index'])
                if mode=='gn-shared-cached':
                    stored = nodes.new('GeometryNodeStoreNamedAttribute')
                    stored.data_type,stored.domain = 'FLOAT','CORNER'
                    stored.inputs['Name'].default_value = 'probe_corner_normal_limit'
                    links.new(source_geometry,stored.inputs['Geometry'])
                    links.new(limit,stored.inputs['Value'])
                    source_geometry = stored.outputs['Geometry']
                    cached = nodes.new('GeometryNodeInputNamedAttribute')
                    cached.data_type = 'FLOAT'
                    cached.inputs['Name'].default_value = 'probe_corner_normal_limit'
                    limit = cached.outputs['Attribute']
                shared_limit = None
                for rank in range(64):
                    around = nodes.new('GeometryNodeCornersOfVertex')
                    links.new(vertex.outputs['Vertex Index'],around.inputs['Vertex Index'])
                    around.inputs['Sort Index'].default_value = rank
                    sampled = nodes.new('GeometryNodeSampleIndex')
                    sampled.data_type,sampled.domain = 'FLOAT','CORNER'
                    links.new(source_geometry,sampled.inputs['Geometry'])
                    links.new(limit,sampled.inputs['Value'])
                    links.new(around.outputs['Corner Index'],sampled.inputs['Index'])
                    exists = m(nodes,links,'LESS_THAN',float(rank),around.outputs['Total'])
                    item = builder._switch(nodes,links,'FLOAT',exists,1.0,sampled.outputs['Value'])
                    shared_limit = item if shared_limit is None else m(nodes,links,'MINIMUM',shared_limit,item)
                    if rank==7:
                        small_limit = shared_limit
                if mode in ('gn-shared-fast','gn-shared-cached'):
                    shared_limit = builder._switch(nodes,links,'FLOAT',
                         m(nodes,links,'GREATER_THAN',around.outputs['Total'],8.0),small_limit,shared_limit)
                limit = shared_limit
            weight = m(nodes,links,'MINIMUM',weight,limit)
            smooth = m(nodes,links,'SUBTRACT',1.0,weight)
    normal = v(nodes, links, 'NORMALIZE', v(nodes, links, 'ADD',
                v(nodes, links, 'SCALE', own, scale=smooth),
                v(nodes, links, 'SCALE', target_normal, scale=weight)))
    set_normal = nodes.new('GeometryNodeSetMeshNormal')
    set_normal.mode, set_normal.domain = 'FREE', 'CORNER'
    links.new(source_geometry, set_normal.inputs['Mesh'])
    links.new(normal, set_normal.inputs['Custom Normal'])
    links.new(set_normal.outputs['Mesh'], gout.inputs['Geometry'])
    modifier = obj.modifiers.new('Final Contact Normals', 'NODES')
    modifier.node_group = group
    return [modifier], [group], None
