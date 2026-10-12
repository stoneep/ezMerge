"""Isolate adhesion profile on a slab: no Boolean/Spread/Relax/custom normals.

Regular source rings let us measure the actual r(z) profile without changing
subdivision or confusing a shading fix with a geometric tangent match.
"""
import importlib
import math
from pathlib import Path
import sys
import types

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boolean_free_probe import bypass_adhesion_boolean
from profile_tangent_probe import build_tangent_profile
root = Path(__file__).resolve().parents[1]
package = types.ModuleType('plane_profile')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('plane_profile.merges')
cn = merges.cn
output = root / 'artifacts' / 'profile-plane'
output.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.15))
base = bpy.context.object
base.scale = (4, 4, 0.3)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
base.name = 'B Flat Slab'
n = 32
rings = int(sys.argv[sys.argv.index('--rings')+1]) if '--rings' in sys.argv else 160
if not 80 <= rings <= 1280 or rings % 80:
    raise ValueError('Rings must be a multiple of 80 in 80..1280')
vertices = [(0.6*math.cos(2*math.pi*i/n), 0.6*math.sin(2*math.pi*i/n), 2*j/rings)
            for j in range(rings+1) for i in range(n)]
faces = [(j*n+i, j*n+(i+1)%n, (j+1)*n+(i+1)%n, (j+1)*n+i)
         for j in range(rings) for i in range(n)]
faces += [tuple(reversed(range(n))), tuple(rings*n+i for i in range(n))]
mesh = bpy.data.meshes.new('A Uniform Profile Rings')
mesh.from_pydata(vertices, [], faces)
mesh.update()
obj = bpy.data.objects.new('A Profile Cylinder', mesh)
bpy.context.collection.objects.link(obj)
for face in mesh.polygons:
    face.use_smooth = len(face.vertices) == 4
mod = merges.add_merge(obj, base, radius=0.3)
profile_probe = '--tangent-profile' in sys.argv
if profile_probe:
    group = bpy.data.node_groups.new('Tangent Smooth diagnostic', 'GeometryNodeTree')
    build_tangent_profile(importlib.import_module('plane_profile.merge_adhesion'), group)
    mod.node_group = group
for name, value in ((merges.INFLUENCE_RADIUS, 1), (merges.MERGE_RADIUS, 0.6),
                    (merges.PUSH_STRENGTH, 0.3), (merges.SURFACE_SPREAD, 0),
                    (merges.SURFACE_RELAX, 0), (merges.BEAD_SYNC_NORMALS, False),
                    (merges.ADHESION_LEVELS, 0)):
    cn.set_input(mod, name, value)
bypass_adhesion_boolean(mod)
original_a = [tuple(v.co) for v in mesh.vertices]
original_b = [tuple(v.co) for v in base.data.vertices]


def capture():
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    try:
        assert len(result.vertices) == len(vertices)
        assert [tuple(p.vertices) for p in result.polygons] == faces
        points = [v.co.copy() for v in result.vertices]
        assert all(math.isfinite(c) for p in points for c in p)
        assert min(p.z for p in points) > -1e-6
        assert all((points[i]-Vector(vertices[i])).length < 2e-6 for i in range(len(vertices)) if vertices[i][2] >= 1)
        assert max(abs(points[i].z) for i in range(n)) < 1e-6
        profile = [(math.hypot(points[j*n].x, points[j*n].y), points[j*n].z) for j in range(rings+1)]
        assert all(b[1] >= a[1]-1e-6 for a, b in zip(profile, profile[1:])), 'Height reversed'
        assert all(b[0] <= a[0]+2e-6 for a, b in zip(profile, profile[1:])), 'Radius reversed'
        assert abs(profile[0][0]-(0.6+cn.get_input(mod, merges.PUSH_STRENGTH))) < 2e-6
        assert abs(profile[-1][0]-0.6) < 2e-6
        assert abs(profile[-1][1]-2) < 2e-6
        dr = profile[1][0]-profile[0][0]
        dz = profile[1][1]-profile[0][1]
        shoulder = rings//2
        outer_angle = math.degrees(math.atan2(abs(profile[shoulder-1][0]-profile[shoulder][0]),
                                            profile[shoulder][1]-profile[shoulder-1][1]))
        return points, profile, math.degrees(math.atan2(dz, abs(dr))), outer_angle
    finally:
        evaluated.to_mesh_clear()


results = {}
for label, falloff in (('smooth', merges.SMOOTH), ('linear', merges.LINEAR),
                       ('sharp', merges.SHARP), ('sphere', merges.SPHERE), ('custom-linear', merges.SMOOTH)):
    cn.set_input(mod, merges.FALLOFF, falloff)
    cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, label == 'custom-linear')
    points, profile, angle, outer_angle = capture()
    results[label] = points
    print('PROFILE', label, 'tangent probe', profile_probe, 'rings', rings,
          'contact secant angle deg', angle, 'shoulder deviation from vertical deg', outer_angle,
          'samples (r,z)', [tuple(round(c, 6) for c in profile[j]) for j in (0, rings//40, rings//10, rings//5, 3*rings//10, rings//2)], flush=True)
    if profile_probe and label == 'smooth':
        assert angle < 9 and outer_angle < 1, 'Tangent profile endpoints not improved'
    assert [tuple(v.co) for v in obj.data.vertices] == original_a
    assert [tuple(v.co) for v in base.data.vertices] == original_b
assert all(max((p-q).length for p,q in zip(results['smooth'], result)) > 0.001
           for label, result in results.items() if label != 'smooth')
cn.set_input(mod, merges.FALLOFF, merges.SMOOTH)
cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, False)
reference = capture()[0]
cn.set_input(mod, merges.PUSH_STRENGTH, 0)
without_push = capture()[0]
assert max(abs(math.hypot(p.x,p.y)-0.6) for p in without_push) < 2e-6
cn.set_input(mod, merges.PUSH_STRENGTH, 0.3)
assert max((p-q).length for p,q in zip(reference, capture()[0])) < 1e-7
base.location.z -= 0.05
obj.update_tag()
bpy.context.view_layer.update()
evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
temporary = evaluated.to_mesh()
try:
    assert max((p-v.co).length for p,v in zip(reference, temporary.vertices)) > 0.001
finally:
    evaluated.to_mesh_clear()
base.location.z += 0.05
assert max((p-q).length for p,q in zip(reference, capture()[0])) < 1e-7
print('PASS original connectivity / outside unchanged / contact fixed / monotonic profiles / live movement restoration', flush=True)

if '--render' in sys.argv:
    label = sys.argv[sys.argv.index('--render')+1]
    falloff = {'smooth': merges.SMOOTH, 'linear': merges.LINEAR, 'sharp': merges.SHARP, 'sphere': merges.SPHERE, 'custom-linear': merges.SMOOTH}[label]
    cn.set_input(mod, merges.FALLOFF, falloff)
    cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, label == 'custom-linear')
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 640, 640
    scene.render.resolution_percentage = 100
    scene.world.color = (0.15,0.15,0.15)
    material = bpy.data.materials.new('Neutral Profile')
    material.diffuse_color = (0.4,0.44,0.48,1)
    obj.data.materials.append(material)
    base.data.materials.append(material)
    def aim(item):
        item.rotation_euler = (Vector((0,0,0.75))-item.location).to_track_quat('-Z','Y').to_euler()
    bpy.ops.object.camera_add(location=(4,-6,3))
    camera = bpy.context.object
    aim(camera)
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 3.2
    scene.camera = camera
    for position, power in (((-3,-4,5),900), ((3,1,4),700)):
        bpy.ops.object.light_add(type='AREA', location=position)
        light = bpy.context.object
        light.data.energy, light.data.size = power, 4
        aim(light)
    artifact_label = label + ('-tangent' if profile_probe else '')
    scene.render.filepath = str(output / (artifact_label+'.png'))
    bpy.ops.wm.save_as_mainfile(filepath=str(output / (artifact_label+'.blend')))
    bpy.ops.render.render(write_still=True)
    print('PREVIEW', scene.render.filepath, flush=True)
print('ALL PLANE PROFILE TESTS PASSED', bpy.app.version_string, flush=True)
