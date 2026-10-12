"""Same tangent profile, four external subdivision stacks, no Boolean/normal transfer."""
import importlib
import math
from pathlib import Path
import statistics
import sys
import time
import types

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boolean_free_probe import bypass_adhesion_boolean
from profile_tangent_probe import build_tangent_profile
root = Path(__file__).resolve().parents[1]
package = types.ModuleType('subdivision_profile')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('subdivision_profile.merges')
builder = importlib.import_module('subdivision_profile.merge_adhesion')
cn = merges.cn
output = root / 'artifacts' / 'profile-subdivision'
output.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1, location=(0,0,-0.15))
base = bpy.context.object
base.scale = (4,4,0.3)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
base.name = 'B Flat Slab'
n, rings = 32, 8
vertices = [(0.6*math.cos(2*math.pi*i/n),0.6*math.sin(2*math.pi*i/n),2*j/rings)
            for j in range(rings+1) for i in range(n)]
faces = [(j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i) for j in range(rings) for i in range(n)]
faces += [tuple(reversed(range(n))),tuple(rings*n+i for i in range(n))]
mesh = bpy.data.meshes.new('Identical coarse A')
mesh.from_pydata(vertices,[],faces)
mesh.update()
obj = bpy.data.objects.new('A Profile Cylinder',mesh)
bpy.context.collection.objects.link(obj)
for p in mesh.polygons:
    p.use_smooth = len(p.vertices)==4
original_a, original_b = [tuple(v.co) for v in mesh.vertices], [tuple(v.co) for v in base.data.vertices]


def capture():
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    try:
        points = [v.co.copy() for v in result.vertices]
        assert all(math.isfinite(c) for p in points for c in p)
        topology = (len(points), [tuple(p.vertices) for p in result.polygons])
        distance = result.attributes.get(merges.ADHESION_DISTANCE)
        d = [v.value for v in distance.data] if distance else None
        return points, topology, d
    finally:
        evaluated.to_mesh_clear()


render_case = sys.argv[sys.argv.index('--render')+1] if '--render' in sys.argv else None
direct_source = '--direct-source-normals' in sys.argv
for case, kind, before in (('linear-before','SIMPLE',True),('linear-after','SIMPLE',False),
                           ('catmull-before','CATMULL_CLARK',True),('catmull-after','CATMULL_CLARK',False)):
    if render_case and case != render_case:
        continue
    subdivide = obj.modifiers.new('Subdivision '+case,'SUBSURF') if before else None
    mod = obj.modifiers.new('Tangent adhesion','NODES')
    group = bpy.data.node_groups.new('Profile '+case,'GeometryNodeTree')
    build_tangent_profile(builder,group,direct_source_normals=direct_source)
    mod.node_group = group
    for name,value in ((merges.BASE,base),(merges.INFLUENCE_RADIUS,1),(merges.MERGE_RADIUS,0.6),
                       (merges.PUSH_STRENGTH,0.3),(merges.SURFACE_SPREAD,0),(merges.SURFACE_RELAX,0),
                       (merges.BEAD_SYNC_NORMALS,False),(merges.ADHESION_LEVELS,0)):
        cn.set_input(mod,name,value)
    bypass_adhesion_boolean(mod)
    if not before:
        subdivide = obj.modifiers.new('Subdivision '+case,'SUBSURF')
    subdivide.subdivision_type = kind
    subdivide.levels = subdivide.render_levels = 2
    cn.set_input(mod,merges.GLOBAL,0)
    reference, reference_topology, _ = capture()
    cn.set_input(mod,merges.GLOBAL,1)
    points, topology, distances = capture()
    assert topology == reference_topology, 'Adhesion changed connectivity'
    assert distances is not None
    foot = [p for p,d in zip(points,distances) if d < 1e-6]
    exact_source_contact = bool(foot)
    if not foot:
        # Catmull BEFORE deformation can lift every source contact vertex;
        # this is a measured limitation, not a reason to skip the variant.
        closest = min(distances)
        foot = [p for p,d in zip(points,distances) if d < closest+1e-6]
    # Measure the OUTER contact ring, not just the bottom cap center that can
    # remain on the slab while the useful contact perimeter lifts off it.
    outer_radius = max(math.hypot(p.x,p.y) for p in foot)
    perimeter = [p for p in foot if math.hypot(p.x,p.y) > outer_radius-1e-4]
    outside_delta = max((p-q).length for p,q,d in zip(points,reference,distances) if d > 1.4)
    assert outside_delta < 2e-6
    shape = [tuple(p) for p in points]
    base.location.z -= 0.025
    moved, moved_topology, _ = capture()
    assert moved_topology == topology and max((p-q).length for p,q in zip(points,moved)) > 1e-4
    base.location.z += 0.025
    assert max((p-q).length for p,q in zip(points,capture()[0])) < 1e-7
    times = []
    for _ in range(5):
        obj.update_tag()
        start = time.perf_counter()
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        evaluated.to_mesh()
        evaluated.to_mesh_clear()
        times.append((time.perf_counter()-start)*1000)
    print('STACK',case,'direct source',direct_source,'points',len(points),'max radius',max(math.hypot(p.x,p.y) for p in points),
          'exact source contact',exact_source_contact,'foot candidate radius',outer_radius,
          'perimeter gap z',max(p.z for p in perimeter),'minimum evaluated z',min(p.z for p in points),
          'top shoulder z',[round(p.z,6) for p in points if math.hypot(p.x,p.y)>0.595 and p.z>1.9][:3],
          'outside delta',outside_delta,'median ms',statistics.median(times),flush=True)
    assert [tuple(v.co) for v in mesh.vertices] == original_a
    assert [tuple(v.co) for v in base.data.vertices] == original_b
    if render_case:
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 24
        scene.cycles.use_denoising = True
        scene.render.resolution_x = scene.render.resolution_y = 640
        scene.render.resolution_percentage = 100
        scene.world.color = (0.15,0.15,0.15)
        mat = bpy.data.materials.new('Neutral Profile')
        mat.diffuse_color = (0.4,0.44,0.48,1)
        obj.data.materials.append(mat)
        base.data.materials.append(mat)
        def aim(item):
            item.rotation_euler = (Vector((0,0,0.75))-item.location).to_track_quat('-Z','Y').to_euler()
        bpy.ops.object.camera_add(location=(4,-6,3))
        camera = bpy.context.object
        aim(camera)
        camera.data.type, camera.data.ortho_scale = 'ORTHO',3.2
        scene.camera = camera
        for position,power in (((-3,-4,5),900),((3,1,4),700)):
            bpy.ops.object.light_add(type='AREA',location=position)
            light = bpy.context.object
            light.data.energy,light.data.size = power,4
            aim(light)
        artifact = case+('-direct-source' if direct_source else '')
        scene.render.filepath = str(output/(artifact+'.png'))
        bpy.ops.wm.save_as_mainfile(filepath=str(output/(artifact+'.blend')))
        bpy.ops.render.render(write_still=True)
        print('PREVIEW',scene.render.filepath,flush=True)
    obj.modifiers.remove(mod)
    obj.modifiers.remove(subdivide)
    bpy.data.node_groups.remove(group)
print('ALL SUBDIVISION STACK TESTS PASSED',bpy.app.version_string,flush=True)
