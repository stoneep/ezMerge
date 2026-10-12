"""Fixed open flare: compare shading only, without adhesion/Boolean/refinement.

The flare meets the plane at a non-tangent angle intentionally. This measures
the visual trick, not welded topology or a physically tangent joined surface.
Captured source distance is a synthetic pre-deformation distance for isolating
the mask; it is not reconstructed from a moving source object.
"""
import bpy
import importlib
import math
from pathlib import Path
import sys
import types
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
from profile_normal_probe import add_normal_probe
package = types.ModuleType('normal_continuity_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
builder = importlib.import_module(package.__name__+'.merge_adhesion')
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,-.15))
base = bpy.context.object
base.scale = (4,4,.3)
bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
n,rings = 64,48
vertices = []
for j in range(rings+1):
    z = j*1.8/rings
    u = min(1,z/.6)
    r = .6+.3*(1-u)**2
    vertices.extend((r*math.cos(2*math.pi*i/n),r*math.sin(2*math.pi*i/n),z) for i in range(n))
faces = [(j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i)
         for j in range(rings) for i in range(n)]
faces.append(tuple(rings*n+i for i in range(n)))
mesh = bpy.data.meshes.new('Fixed flare')
mesh.from_pydata(vertices,[],faces)
mesh.update()
for face in mesh.polygons:
    face.use_smooth = len(face.vertices)==4
attr = mesh.attributes.new(builder.ADHESION_DISTANCE,'FLOAT','POINT')
for item,point in zip(attr.data,vertices):
    item.value = .05+point[2]
obj = bpy.data.objects.new('Fixed A',mesh)
bpy.context.collection.objects.link(obj)
def capture():
    obj.update_tag()
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = ev.to_mesh()
    try:
        return ([v.co.copy() for v in result.vertices],[tuple(p.vertices) for p in result.polygons],
                [x.vector.copy() for x in result.corner_normals],
                [x.vertex_index for x in result.loops],
                [p.normal.copy() for p in result.polygons for _ in p.loop_indices])
    finally:
        ev.to_mesh_clear()
reference = capture()
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = 32
scene.render.resolution_x = scene.render.resolution_y = 640
scene.render.resolution_percentage = 100
scene.world.color = (.15,.15,.15)
mat = bpy.data.materials.new('Shared material')
mat.use_nodes = True
shader = mat.node_tree.nodes.get('Principled BSDF')
shader.inputs['Base Color'].default_value = (.22,.27,.33,1)
shader.inputs['Roughness'].default_value = .25
obj.data.materials.append(mat)
base.data.materials.append(mat)
def aim(item):
    item.rotation_euler = (Vector((0,0,.7))-item.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(4,-6,3))
camera = bpy.context.object
aim(camera)
camera.data.type,camera.data.ortho_scale = 'ORTHO',3.4
scene.camera = camera
for location,power in (((-3,-4,5),900),((3,1,4),700)):
    bpy.ops.object.light_add(type='AREA',location=location)
    light = bpy.context.object
    light.data.energy,light.data.size = power,3
    aim(light)
output = root/'artifacts'/'normal-continuity'
output.mkdir(parents=True,exist_ok=True)
modes = ('off','gn-source','gn','gn-bounded','gn-shared','gn-shared-fast','gn-shared-cached','transfer-replace') if hasattr(bpy.types,'GeometryNodeSetMeshNormal') else ('off','transfer','transfer-replace')
for mode in modes:
    modifiers,groups,vg = [],[],None
    if mode != 'off':
        modifiers,groups,vg = add_normal_probe(obj,base,builder,mode=mode)
    points,topology,normals,loops,geometric = capture()
    if mode in ('gn-shared','gn-shared-fast','gn-shared-cached'):
        counts = {}
        for vertex in loops:
            counts[vertex] = counts.get(vertex,0)+1
        assert max(counts.values())<=64
    assert topology==reference[1]
    assert max((p-q).length for p,q in zip(points,reference[0])) < 1e-7
    contact = [math.degrees(math.acos(max(-1,min(1,n.z))))
               for v,n in zip(loops,normals) if points[v].z<1e-6]
    opposed = sum(g.dot(n)<-1e-5 for g,n in zip(geometric,normals))
    outside = [(n-q).length for v,n,q in zip(loops,normals,reference[2]) if points[v].z>.2]
    assert opposed==0
    assert max(outside) < 2e-3
    if mode in ('gn','gn-bounded','gn-shared','gn-shared-fast','gn-shared-cached','transfer-replace'):
        assert max(contact)<.1
    print('NORMAL CONTINUITY',mode,'max seam angle to B',max(contact),
          'opposed corner normals',opposed,'positions/topology unchanged',flush=True)
    artifact = mode+('-4.2' if bpy.app.version < (4,5,0) else '')
    scene.render.filepath = str(output/(artifact+'.png'))
    bpy.ops.wm.save_as_mainfile(filepath=str(output/(artifact+'.blend')))
    bpy.ops.render.render(write_still=True)
    for mod in modifiers:
        obj.modifiers.remove(mod)
    for group in groups:
        bpy.data.node_groups.remove(group)
    if vg:
        obj.vertex_groups.remove(vg)
print('ALL NORMAL CONTINUITY TESTS PASSED',bpy.app.version_string,flush=True)
