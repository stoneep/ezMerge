"""Measured radial-profile GN backend for experimental overlapping sphere pairs."""
import math
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

def hermite_curve_group():
    """Shared vector Hermite kernel used by sphere/sphere and plane-contact tests."""
    name='EZ Sphere Hermite v1'
    if name in bpy.data.node_groups:return bpy.data.node_groups[name]
    g=bpy.data.node_groups.new(name,'GeometryNodeTree')
    for name in ['P0','P1','T0','T1']:g.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    g.interface.new_socket(name='t',in_out='INPUT',socket_type='NodeSocketFloat')
    g.interface.new_socket(name='Position',in_out='OUTPUT',socket_type='NodeSocketVector')
    ns=g.nodes;ls=g.links;inp=ns.new('NodeGroupInput');out=ns.new('NodeGroupOutput')
    def m(op,a,b):
        n=ns.new('ShaderNodeMath');n.operation=op
        for socket,v in zip(n.inputs,[a,b]):
            if isinstance(v,(int,float)):socket.default_value=v
            else:ls.new(v,socket)
        return n.outputs[0]
    t=inp.outputs['t'];t2=m('MULTIPLY',t,t);t3=m('MULTIPLY',t2,t)
    h=[m('ADD',m('SUBTRACT',m('MULTIPLY',2.,t3),m('MULTIPLY',3.,t2)),1.),m('ADD',m('MULTIPLY',-2.,t3),m('MULTIPLY',3.,t2)),m('ADD',m('SUBTRACT',t3,m('MULTIPLY',2.,t2)),t),m('SUBTRACT',t3,t2)]
    values=[]
    for name,w in zip(['P0','P1','T0','T1'],h):
        n=ns.new('ShaderNodeVectorMath');n.operation='SCALE';ls.new(inp.outputs[name],n.inputs[0]);ls.new(w,n.inputs['Scale']);values.append(n.outputs['Vector'])
    total=values[0]
    for value in values[1:]:
        n=ns.new('ShaderNodeVectorMath');n.operation='ADD';ls.new(total,n.inputs[0]);ls.new(value,n.inputs[1]);total=n.outputs['Vector']
    ls.new(total,out.inputs['Position'])
    return g

def install(result, center, lo, hi, lower, upper):
    mesh=result.data;mesh.calc_loop_triangles()
    tree=BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices],[tuple(t.vertices) for t in mesh.loop_triangles],all_triangles=True)
    count=257;step=(hi-lo)/(count-1);radii=[]
    extent=max(v.co.length for v in mesh.vertices)*2
    for i in range(count):
        z=lo+i*step;hits=[]
        for j in range(32):
            d=Vector((math.cos(j*math.tau/32),math.sin(j*math.tau/32),0))
            hit=tree.ray_cast(d*extent+Vector((0,0,z)),-d,extent*2)[0]
            if hit is not None:hits.append(math.hypot(hit.x,hit.y))
        if len(hits)!=32:raise ValueError('Profile does not cover all radial rays')
        radii.append(sum(hits)/len(hits))
    # Finite-window derivative reduces single-facet slope changes without
    # replacing the input mesh. Radius itself stays measured and unfiltered.
    slopes=[(radii[min(count-1,i+3)]-radii[max(0,i-3)])/((min(count-1,i+3)-max(0,i-3))*step) for i in range(count)]
    lookup_mesh=bpy.data.meshes.new('Measured original sphere union radial profile')
    lookup_mesh.from_pydata([(r,s,lo+i*step) for i,(r,s) in enumerate(zip(radii,slopes))],[],[])
    lookup=bpy.data.objects.new('INTERNAL measured profile LUT - do not edit',lookup_mesh);bpy.context.collection.objects.link(lookup);lookup.matrix_world=result.matrix_world.copy();lookup.hide_render=True;lookup.hide_set(True)
    g=bpy.data.node_groups.new('EZ Sphere Junction Experiment','GeometryNodeTree');g.is_modifier=True
    params=[('Strength',1.,0.,1.,'0 restores the original Boolean geometry.'),('Region Center Z',center,lo+(hi-lo)*.1,hi-(hi-lo)*.1,'Local Z coordinate of the influence region center; not a new intersection detection.'),('Lower Reach',lower,(hi-lo)*.01,(hi-lo)*.8,'Distance toward the large sphere.'),('Upper Reach',upper,(hi-lo)*.01,(hi-lo)*.8,'Distance toward the small sphere.'),('Edge Fade',.2,.05,.45,'Fraction of the region used to return to the original surface at each end.')]
    for name,io,kind in [('Geometry','INPUT','NodeSocketGeometry'),('Geometry','OUTPUT','NodeSocketGeometry')]:g.interface.new_socket(name=name,in_out=io,socket_type=kind)
    for name,value,minimum,maximum,description in params:
        s=g.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat');s.default_value=value;s.min_value=minimum;s.max_value=maximum;s.description=description
    s=g.interface.new_socket(name='Show Region',in_out='INPUT',socket_type='NodeSocketBool');s.default_value=False;s.description='Orange marks affected faces; does not change coordinates.'
    ns=g.nodes;ls=g.links;gin=ns.new('NodeGroupInput');gout=ns.new('NodeGroupOutput')
    def scalar(op,a,b=0.):
        n=ns.new('ShaderNodeMath');n.operation=op
        for socket,v in zip(n.inputs,[a,b]):
            if isinstance(v,(int,float)):socket.default_value=v
            else:ls.new(v,socket)
        return n.outputs[0]
    def vector(op,a,b=None):
        n=ns.new('ShaderNodeVectorMath');n.operation=op
        for socket,v in [(n.inputs[0],a),(n.inputs['Scale'] if op=='SCALE' else n.inputs[1],b)]:
            if v is None:continue
            if isinstance(v,(int,float,tuple)):socket.default_value=v
            else:ls.new(v,socket)
        return n.outputs['Value'] if op=='LENGTH' else n.outputs['Vector']
    def clamp(x,minimum=0.,maximum=1.):return scalar('MINIMUM',maximum,scalar('MAXIMUM',minimum,x))
    def smooth(x):
        x=clamp(x);return scalar('MULTIPLY',scalar('MULTIPLY',x,x),scalar('SUBTRACT',3.,scalar('MULTIPLY',2.,x)))
    pos=ns.new('GeometryNodeInputPosition').outputs[0]
    info=ns.new('GeometryNodeObjectInfo');info.transform_space='RELATIVE';info.inputs['Object'].default_value=lookup
    def sample(z):
        index=clamp(scalar('DIVIDE',scalar('SUBTRACT',z,lo),step),0.,count-1.000001)
        lower=scalar('FLOOR',index);f=scalar('SUBTRACT',index,lower)
        values=[]
        for idx in [lower,scalar('ADD',lower,1.)]:
            n=ns.new('GeometryNodeSampleIndex');n.data_type='FLOAT_VECTOR';n.domain='POINT';n.clamp=True
            ls.new(info.outputs['Geometry'],n.inputs['Geometry']);ls.new(pos,n.inputs['Value']);ls.new(idx,n.inputs['Index']);values.append(n.outputs['Value'])
        value=vector('ADD',values[0],vector('SCALE',vector('SUBTRACT',values[1],values[0]),f))
        split=ns.new('ShaderNodeSeparateXYZ');ls.new(value,split.inputs[0]);return split.outputs['X'],split.outputs['Y']
    # Clamp the tested UI region to measured support away from the sphere poles.
    a=clamp(scalar('SUBTRACT',gin.outputs['Region Center Z'],gin.outputs['Lower Reach']),lo,hi-(hi-lo)*.05)
    b=clamp(scalar('ADD',gin.outputs['Region Center Z'],gin.outputs['Upper Reach']),lo+(hi-lo)*.05,hi)
    b=scalar('MAXIMUM',b,scalar('ADD',a,(hi-lo)*.01));length=scalar('SUBTRACT',b,a)
    r0,m0=sample(a);r1,m1=sample(b)
    c0=r0;c1=scalar('MULTIPLY',length,m0)
    c2=scalar('SUBTRACT',scalar('ADD',scalar('MULTIPLY',-3.,r0),scalar('MULTIPLY',3.,r1)),scalar('ADD',scalar('MULTIPLY',2.,c1),scalar('MULTIPLY',length,m1)))
    c3=scalar('ADD',scalar('ADD',scalar('MULTIPLY',2.,r0),scalar('MULTIPLY',-2.,r1)),scalar('ADD',c1,scalar('MULTIPLY',length,m1)))
    sep=ns.new('ShaderNodeSeparateXYZ');ls.new(pos,sep.inputs[0]);z=sep.outputs['Z']
    t=clamp(scalar('DIVIDE',scalar('SUBTRACT',z,a),length))
    # Same vector-valued Hermite kernel can also connect a flat plane to a sphere.
    curve=ns.new('GeometryNodeGroup');curve.node_tree=hermite_curve_group();ls.new(t,curve.inputs['t'])
    for name,value in [('P0',r0),('P1',r1),('T0',c1),('T1',scalar('MULTIPLY',length,m1))]:
        n=ns.new('ShaderNodeCombineXYZ');ls.new(value,n.inputs['X']);ls.new(n.outputs[0],curve.inputs[name])
    split=ns.new('ShaderNodeSeparateXYZ');ls.new(curve.outputs['Position'],split.inputs[0]);target=split.outputs['X']
    # Do not let a fitted cubic cross the axis. This guard is not a fold solver.
    target=scalar('MAXIMUM',target,(hi-lo)*.001)
    fade=clamp(gin.outputs['Edge Fade'],.05,.45)
    window=scalar('MULTIPLY',smooth(scalar('DIVIDE',t,fade)),smooth(scalar('DIVIDE',scalar('SUBTRACT',1.,t),fade)))
    w=scalar('MULTIPLY',window,clamp(gin.outputs['Strength']))
    radial=vector('MULTIPLY',pos,(1,1,0));rho=vector('LENGTH',radial)
    offset=vector('SCALE',radial,scalar('DIVIDE',scalar('MULTIPLY',w,scalar('SUBTRACT',target,rho)),scalar('MAXIMUM',rho,1e-8)))
    geometry=gin.outputs['Geometry']
    for name,value in [('region_weight',w),('region_lower_z',a),('region_upper_z',b)]:
        store=ns.new('GeometryNodeStoreNamedAttribute');store.data_type='FLOAT';store.domain='POINT';store.inputs['Name'].default_value=name;ls.new(geometry,store.inputs['Geometry']);ls.new(value,store.inputs['Value']);geometry=store.outputs['Geometry']
    move=ns.new('GeometryNodeSetPosition');ls.new(geometry,move.inputs['Geometry']);ls.new(offset,move.inputs['Offset'])
    material=bpy.data.materials.new('TEST orange influence region');material.diffuse_color=(1.,.25,.035,1.)
    color=ns.new('GeometryNodeSetMaterial');color.inputs['Material'].default_value=material;ls.new(move.outputs['Geometry'],color.inputs['Geometry'])
    selection=scalar('MULTIPLY',gin.outputs['Show Region'],scalar('GREATER_THAN',w,.001));ls.new(selection,color.inputs['Selection']);ls.new(color.outputs['Geometry'],gout.inputs['Geometry'])
    for i,n in enumerate(ns):n.location=((i%10)*190,-(i//10)*160)
    for mod in list(result.modifiers):
        if mod.type=='NODES' and mod.node_group and mod.node_group.name.startswith('Measured 4 to 1'):result.modifiers.remove(mod)
    mod=result.modifiers.new('Sphere Junction (Experimental)','NODES');mod.node_group=g
    # Geometry displacement must precede Smooth by Angle.
    bpy.context.view_layer.objects.active=result
    index=result.modifiers.find(mod.name)
    if index>0:
        result.modifiers.move(index,0)
    sockets={i.name:i.identifier for i in g.interface.items_tree if i.item_type=='SOCKET' and i.in_out=='INPUT'}
    return mod,sockets,dict(samples=count,height_min_m=lo,height_max_m=hi,ray_directions=32,source='Undeformed actual Boolean mesh'),lookup
