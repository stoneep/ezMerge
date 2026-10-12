"""Diagnostic separation of coplanar cap faces and the raised underside lip."""


def add_underface_probe(obj, builder, mode):
    import bpy
    if mode not in ('remove-cap', 'bury-contact', 'bury-lip'):
        raise ValueError('Unknown underface diagnostic')
    group = bpy.data.node_groups.new('Underface '+mode,'GeometryNodeTree')
    group.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    nodes,links = group.nodes,group.links
    gin,gout = nodes.new('NodeGroupInput'),nodes.new('NodeGroupOutput')
    distance = nodes.new('GeometryNodeInputNamedAttribute')
    distance.data_type = 'FLOAT'
    distance.inputs['Name'].default_value = builder.ADHESION_DISTANCE
    m,v = builder._math,builder._vector
    if mode == 'remove-cap':
        # All corners of the coplanar bottom cap have captured distance zero.
        # FACE domain averages nonnegative captured values: zero selects only
        # those faces. ONLY_FACE intentionally retains vertex indices.
        face_value = nodes.new('GeometryNodeFieldOnDomain')
        face_value.data_type,face_value.domain = 'FLOAT','FACE'
        links.new(distance.outputs['Attribute'],face_value.inputs['Value'])
        delete = nodes.new('GeometryNodeDeleteGeometry')
        delete.domain,delete.mode = 'FACE','ONLY_FACE'
        links.new(gin.outputs['Geometry'],delete.inputs['Geometry'])
        links.new(m(nodes,links,'LESS_THAN',face_value.outputs['Value'],1e-6),delete.inputs['Selection'])
        result = delete.outputs['Geometry']
    else:
        # Plane-only diagnostic: move the contact/support band a little below
        # z=0, while leaving the rest unchanged. Not a generic curved fitter.
        u = m(nodes,links,'DIVIDE',distance.outputs['Attribute'],0.25,clamp=True)
        smooth = m(nodes,links,'MULTIPLY',m(nodes,links,'MULTIPLY',u,u),
                   m(nodes,links,'SUBTRACT',3.0,m(nodes,links,'MULTIPLY',u,2.0)))
        weight = m(nodes,links,'SUBTRACT',1.0,smooth)
        move = nodes.new('GeometryNodeSetPosition')
        links.new(gin.outputs['Geometry'],move.inputs['Geometry'])
        links.new(m(nodes,links,'GREATER_THAN',weight,1e-8),move.inputs['Selection'])
        combine = nodes.new('ShaderNodeCombineXYZ')
        links.new(m(nodes,links,'MULTIPLY',weight,-0.05 if mode == 'bury-lip' else -0.002),combine.inputs['Z'])
        links.new(combine.outputs[0],move.inputs['Offset'])
        result = move.outputs['Geometry']
    links.new(result,gout.inputs['Geometry'])
    modifier = obj.modifiers.new('Underface '+mode,'NODES')
    modifier.node_group = group
    return modifier,group
