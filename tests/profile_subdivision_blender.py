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
from profile_contact_probe import add_contact_probe
from profile_normal_probe import add_normal_probe
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


def capture(include_normals=False):
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
        if include_normals:
            return points, topology, d, [item.vector.copy() for item in result.corner_normals], [loop.vertex_index for loop in result.loops]
        return points, topology, d
    finally:
        evaluated.to_mesh_clear()


render_case = sys.argv[sys.argv.index('--render')+1] if '--render' in sys.argv else None
case_filter = render_case or (sys.argv[sys.argv.index('--case')+1] if '--case' in sys.argv else None)
levels = int(sys.argv[sys.argv.index('--levels')+1]) if '--levels' in sys.argv else 2
if not 0 <= levels <= 4:
    raise ValueError('Subdivision probe levels must be 0..4')
direct_source = '--direct-source-normals' in sys.argv
contact_mode = sys.argv[sys.argv.index('--contact')+1] if '--contact' in sys.argv else None
normal_mode = sys.argv[sys.argv.index('--normals')+1] if '--normals' in sys.argv else None
for case, kind, before in (('linear-before','SIMPLE',True),('linear-after','SIMPLE',False),
                           ('catmull-before','CATMULL_CLARK',True),('catmull-after','CATMULL_CLARK',False)):
    if case_filter and case != case_filter:
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
    subdivide.levels = subdivide.render_levels = levels
    cn.set_input(mod,merges.GLOBAL,0)
    reference, reference_topology, _ = capture()
    cn.set_input(mod,merges.GLOBAL,1)
    uncorrected, _, _ = capture()
    contact_modifier = contact_group = None
    if contact_mode:
        if case != 'catmull-after':
            raise ValueError('Contact probe is scoped to catmull-after; use --render catmull-after')
        contact_modifier, contact_group = add_contact_probe(obj,base,builder,mode=contact_mode)
    points, topology, distances = capture()
    normal_modifiers, normal_groups, normal_vertex_group = [], [], None
    if normal_mode:
        before_points, before_topology, _, before_normals, loop_vertices = capture(include_normals=True)
        normal_modifiers, normal_groups, normal_vertex_group = add_normal_probe(obj,base,builder,mode=normal_mode)
        points, topology, distances, after_normals, _ = capture(include_normals=True)
        assert topology == before_topology and all((p-q).length < 1e-7 for p,q in zip(points,before_points))
        changed = max((p-q).length for p,q in zip(before_normals,after_normals))
        assert changed > 0.01, 'Normal probe had no shading effect'
        inside_angles, outside_deltas = [], []
        for vertex,p,q in zip(loop_vertices,before_normals,after_normals):
            if points[vertex].z < 1e-6:
                inside_angles.append(math.degrees(math.acos(max(-1,min(1,q.z)))))
            if points[vertex].z > 0.2:
                outside_deltas.append((p-q).length)
        assert max(outside_deltas) < 2e-3, 'Normals outside support changed'
        if normal_mode == 'gn':
            assert max(inside_angles) < 1, 'GN contact normals did not match the target'
        print('NORMALS',normal_mode,'max change',changed,'median contact angle',statistics.median(inside_angles),
              'contact normals aligned fraction',sum(a<1 for a in inside_angles)/len(inside_angles),
              'outside delta',max(outside_deltas),'positions/topology unchanged',flush=True)
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
    edge_keys = {tuple(sorted((face[i],face[(i+1)%len(face)]))) for face in topology[1] for i in range(len(face))}
    contact_angles = []
    for a,b in edge_keys:
        if distances[b] < 1e-6:
            a,b = b,a
        if distances[a] < 1e-6 and 1e-6 <= distances[b] < 0.25:
            dr = abs(math.hypot(points[b].x,points[b].y)-math.hypot(points[a].x,points[a].y))
            dz = abs(points[b].z-points[a].z)
            if dr > 1e-6:
                contact_angles.append(math.degrees(math.atan2(dz,dr)))
    if contact_mode:
        assert max(abs(p.z) for p in perimeter) < 1e-6, 'Contact perimeter remains detached'
        assert min(p.z for p in points) > -1e-6
        assert max(abs(math.hypot(p.x,p.y)-math.hypot(q.x,q.y)) for p,q in zip(points,uncorrected)) < 2e-6
        assert max((p-q).length for p,q,d in zip(points,uncorrected,distances) if d >= 0.25) < 1e-7
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
    print('STACK',case,'direct source',direct_source,'contact mode',contact_mode,'levels',levels,
          'points',len(points),'max radius',max(math.hypot(p.x,p.y) for p in points),
          'exact source contact',exact_source_contact,'foot candidate radius',outer_radius,
          'perimeter gap z',max(p.z for p in perimeter),'minimum evaluated z',min(p.z for p in points),
          'top shoulder z',[round(p.z,6) for p in points if math.hypot(p.x,p.y)>0.595 and p.z>1.9][:3],
          'outside delta',outside_delta,'median contact secant angle',statistics.median(contact_angles) if contact_angles else None,
          'median ms',statistics.median(times),flush=True)
    assert [tuple(v.co) for v in mesh.vertices] == original_a
    assert [tuple(v.co) for v in base.data.vertices] == original_b
    if render_case:
        scene = bpy.context.scene
        if '--eevee' in sys.argv:
            try:
                scene.render.engine = 'BLENDER_EEVEE'
            except TypeError:
                scene.render.engine = 'BLENDER_EEVEE_NEXT'
        else:
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
        artifact = case+('-direct-source' if direct_source else '')+('-contact-'+contact_mode if contact_mode else '')
        artifact += ('-normals-'+normal_mode) if normal_mode else ''
        artifact += '-eevee' if '--eevee' in sys.argv else ''
        scene.render.filepath = str(output/(artifact+'.png'))
        bpy.ops.wm.save_as_mainfile(filepath=str(output/(artifact+'.blend')))
        bpy.ops.render.render(write_still=True)
        print('PREVIEW',scene.render.filepath,flush=True)
    for normal_modifier in reversed(normal_modifiers):
        obj.modifiers.remove(normal_modifier)
    for normal_group in normal_groups:
        bpy.data.node_groups.remove(normal_group)
    if normal_vertex_group:
        obj.vertex_groups.remove(normal_vertex_group)
    if contact_modifier:
        obj.modifiers.remove(contact_modifier)
        bpy.data.node_groups.remove(contact_group)
    obj.modifiers.remove(mod)
    obj.modifiers.remove(subdivide)
    bpy.data.node_groups.remove(group)
print('ALL SUBDIVISION STACK TESTS PASSED',bpy.app.version_string,flush=True)
