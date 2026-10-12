"""Headless visual check: Blender --python this_file.py -- before|after."""
import importlib
from pathlib import Path
import sys
import types

import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('ezhs_preview')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('ezhs_preview.merges')
label = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else 'after'
no_boolean = '--no-boolean' in sys.argv
output = root / 'artifacts' / 'bead-preview'
output.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

mat = bpy.data.materials.new('Satin graphite')
mat.diffuse_color = (0.36, 0.4, 0.44, 1)
mat.use_nodes = True
shader = mat.node_tree.nodes.get('Principled BSDF')
shader.inputs['Base Color'].default_value = mat.diffuse_color
shader.inputs['Roughness'].default_value = 0.42
shader.inputs['Metallic'].default_value = 0.12

def cylinder(radius, depth, rotation, location):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=radius, depth=depth,
                                      rotation=rotation, location=location)
    obj = bpy.context.object
    for face in obj.data.polygons:
        face.use_smooth = abs(face.normal.z) < 0.9
    obj.data.materials.append(mat)
    return obj

target_view = label.startswith(('adhesion-corner', 'adhesion-spread')) or label in ('target', 'wide-flat', 'raised', 'curve-soft', 'curve-neutral', 'curve-unsynced',
                        'surface-after', 'surface-before', 'adhesion', 'adhesion-unsynced', 'adhesion-spread')
if label.startswith('adhesion-corner'):
    base = cylinder(1, 2, (0, 1.57079632679, 0), (0, 0, 0))
    obj = cylinder(0.78, 3.6, (0, 0, 0), (0.75, 0, 0))
elif target_view:
    base = cylinder(1, 3.8, (0, 1.57079632679, 0), (0, 0, 0))
    obj = cylinder(0.78, 3.6, (0, 0, 0), (0, 0, 0))
else:
    base = cylinder(0.85, 6, (0, 0.62, 0), (0.25, 0, 0))
    obj = cylinder(1, 5, (0, 0, 0), (0, 0, 0))
bead = merges.add_merge(obj, base, method=merges.BEAD, adhesion=label.startswith('adhesion'))
if label.startswith('adhesion'):
    merges.cn.set_input(bead, merges.INFLUENCE_RADIUS, 0.55)
    merges.cn.set_input(bead, merges.MERGE_RADIUS, 0.3)
    merges.cn.set_input(bead, merges.PUSH_STRENGTH, 0.22)
    if label == 'adhesion-unsynced':
        merges.cn.set_input(bead, merges.BEAD_SYNC_NORMALS, False)
    if label.startswith('adhesion-spread'):
        merges.cn.set_input(bead, merges.SURFACE_SPREAD, 0.35)
        merges.cn.set_input(bead, merges.SURFACE_CLEARANCE, 0.0002)
    if label.startswith('adhesion-corner'):
        merges.cn.set_input(bead, merges.SURFACE_SPREAD, 0.4)
        merges.cn.set_input(bead, merges.SURFACE_CLEARANCE, 0.0002)
        merges.cn.set_input(bead, merges.SURFACE_CORNER_REACH, 0.0 if label.endswith('blocked') else 0.3)
    if label.endswith('relax-off'):
        merges.cn.set_input(bead, merges.SURFACE_RELAX, 0.0)
    if no_boolean:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from boolean_free_probe import bypass_adhesion_boolean
        bypass_adhesion_boolean(bead)
if label == 'silicone':
    merges.cn.set_input(bead, merges.BEAD_HEIGHT, merges.cn.get_input(bead, merges.BEAD_WIDTH) * 0.7)
if label in ('wide-flat', 'raised'):
    merges.cn.set_input(bead, merges.BEAD_WIDTH, 0.25)
    merges.cn.set_input(bead, merges.BEAD_HEIGHT, 0.045 if label == 'wide-flat' else 0.12)
if label.startswith('curve-'):
    merges.cn.set_input(bead, merges.BEAD_WIDTH, 0.25)
    merges.cn.set_input(bead, merges.BEAD_HEIGHT, 0.08)
    if label == 'curve-soft':
        merges.cn.set_input(bead, merges.BEAD_CUSTOM_CURVE, True)
        curve = merges.curve_node(bead)
        curve.mapping.curves[0].points.new(0.5, 0.8).handle_type = 'AUTO'
        curve.mapping.update()
        bead.node_group.update_tag()
    if label == 'curve-unsynced':
        merges.cn.set_input(bead, merges.BEAD_SYNC_NORMALS, False)
if label.startswith('surface-'):
    merges.cn.set_input(bead, merges.BEAD_WIDTH, 0.25)
    merges.cn.set_input(bead, merges.BEAD_HEIGHT, 0.08)
    merges.cn.set_input(bead, merges.BEAD_PROFILE_RESOLUTION, 7)
    merges.cn.set_input(bead, merges.BEAD_SEAM_SAMPLES, 0)
    merges.cn.set_input(bead, merges.BEAD_SURFACE_SUBDIVISION, True)
    merges.cn.set_input(bead, merges.BEAD_SURFACE_LEVELS, 2)
    merges.cn.set_input(bead, merges.BEAD_SUBDIVISION_ORDER,
                        merges.BEFORE_SNAP if label == 'surface-before' else merges.AFTER_SNAP)
obj.update_tag()

scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.render.resolution_x = 640
scene.render.resolution_y = 720
scene.render.resolution_percentage = 100
scene.world.use_nodes = True
scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.12, 0.14, 0.17, 1)
scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.5

def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()

bpy.ops.object.camera_add(location=(6, -9, 3.8))
camera = bpy.context.object
aim(camera, (0, 0, 0))
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 5.2 if target_view else 6.8
scene.camera = camera
for pos, power, size in (((-4, -5, 7), 1600, 5), ((4, -2, 2), 800, 4), ((1, 4, 5), 1800, 3)):
    bpy.ops.object.light_add(type='AREA', location=pos)
    light = bpy.context.object
    light.data.energy = power
    light.data.shape = 'DISK'
    light.data.size = size
    aim(light, (0, 0, 0))
artifact_label = label + ('-no-boolean' if no_boolean else '')
scene.render.filepath = str(output / (artifact_label + '.png'))
bpy.ops.wm.save_as_mainfile(filepath=str(output / (artifact_label + '.blend')))
bpy.ops.render.render(write_still=True)
print('PREVIEW', scene.render.filepath)
