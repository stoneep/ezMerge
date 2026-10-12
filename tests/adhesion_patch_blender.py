"""Stage-one patch snapshots; run with Blender --background --factory-startup."""

import importlib
import math
from pathlib import Path
import sys
import types

import bpy

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('patch_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('patch_test.merges')
patches = importlib.import_module('patch_test.merge_patch')
cn = merges.cn


def snapshot(obj):
    obj.update_tag()
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
    try:
        return ([tuple(v.co) for v in mesh.vertices],
                [tuple(p.vertices) for p in mesh.polygons])
    finally:
        evaluated.to_mesh_clear()


def members(patch, name):
    index = patch.vertex_groups[name].index
    return {v.index for v in patch.data.vertices if any(g.group == index and g.weight == 1 for g in v.groups)}


def check_patch(obj, mod):
    original_a = [tuple(v.co) for v in obj.data.vertices]
    base = cn.get_input(mod, merges.BASE)
    original_b = [tuple(v.co) for v in base.data.vertices]
    positions, faces = snapshot(obj)
    patch = merges.create_adhesion_patch(obj, mod)
    mesh = patch.data
    assert patch.hide_render and patch.display_type == 'WIRE' and not patch.modifiers
    assert patch.matrix_world == obj.matrix_world
    indices = [item.value for item in mesh.attributes[patches.PATCH_SOURCE_INDEX].data]
    assert len(mesh.polygons) < len(faces), 'Captured whole object instead of the contact band'
    assert [tuple(v.co) for v in mesh.vertices] == [positions[i] for i in indices]
    assert {tuple(indices[i] for i in p.vertices) for p in mesh.polygons}.issubset(set(faces))
    assert all(math.isfinite(c) for v in mesh.vertices for c in v.co)
    rest = mesh.attributes[patches.PATCH_REST_POSITION]
    assert all((v.co - rest.data[v.index].vector).length < 1e-7 for v in mesh.vertices)
    start = members(patch, patches.PATCH_START)
    contact = members(patch, patches.PATCH_CONTACT)
    interior = members(patch, patches.PATCH_INTERIOR)
    assert start and contact and interior and not start & contact
    assert start | contact == members(patch, patches.PATCH_PINS)
    assert start | contact | interior == set(range(len(mesh.vertices)))
    sources = mesh.attributes[merges.ADHESION_SOURCE_POSITION]
    # Outer support row must not move; the face band includes the crossing row.
    assert all((mesh.vertices[i].co - sources.data[i].vector).length < 2e-6 for i in start)
    distances = mesh.attributes[merges.ADHESION_DISTANCE]
    tolerance = patches._contact_tolerance(cn.get_input(mod, merges.INFLUENCE_RADIUS) * cn.get_input(mod, merges.GLOBAL))
    assert all(distances.data[i].value < tolerance for i in contact)
    adjacency = {}
    for p in mesh.polygons:
        for edge in p.edge_keys:
            key = tuple(sorted(edge))
            adjacency[key] = adjacency.get(key, 0) + 1
    start_edges = mesh.attributes[patches.PATCH_START_EDGE]
    contact_edges = mesh.attributes[patches.PATCH_CONTACT_EDGE]
    for edge in mesh.edges:
        boundary = adjacency[tuple(sorted(edge.vertices))] == 1
        assert boundary == (start_edges.data[edge.index].value or contact_edges.data[edge.index].value)
    assert snapshot(obj) == (positions, faces), 'Capture changed evaluated A'
    assert [tuple(v.co) for v in obj.data.vertices] == original_a
    assert [tuple(v.co) for v in base.data.vertices] == original_b
    print('PASS patch exact shape / separate closed boundaries / source unchanged:', len(mesh.vertices), len(mesh.polygons))
    return patch


bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, -4))
base = bpy.context.object
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=4, location=(0, 0, 1))
obj = bpy.context.object
mod = merges.add_merge(obj, base, radius=0.3)
cn.set_input(mod, merges.ADHESION_LEVELS, 3)
cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
for order in (merges.BEFORE_SNAP, merges.AFTER_SNAP):
    cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, order)
    patch = check_patch(obj, mod)
    frozen = [tuple(v.co) for v in patch.data.vertices]
    base.location.x += 0.1
    cn.set_input(mod, merges.SURFACE_SPREAD, 0.2)
    snapshot(obj)
    assert frozen == [tuple(v.co) for v in patch.data.vertices], 'Snapshot follows live deformation'
    cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
print('PASS both subdivision orders / immutable snapshot across target edits')

# A custom curve may zero influence inside the band; do not create interior
# false boundaries by interpreting its weights as the band definition.
cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, merges.BEFORE_SNAP)
cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, True)
curve = merges.curve_node(mod)
curve.mapping.curves[0].points.new(0.4, 0.0)
curve.mapping.update()
mod.node_group.update_tag()
check_patch(obj, mod)
cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, False)
print('PASS custom curve does not split the geometric band')

# Curved target, nonidentity source transform; preserve the actual post-Spread shape.
bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=1, depth=4, location=(0.3, 0, 0.6), rotation=(0, math.pi / 2, 0))
curved = bpy.context.object
cn.set_input(mod, merges.BASE, curved)
cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, merges.BEFORE_SNAP)
obj.rotation_euler.z = 0.2
obj.scale = (0.75, 0.65, 1)
check_patch(obj, mod)
print('PASS curved target / transformed A')


def rejected(action, message):
    before = (len(bpy.data.objects), len(bpy.data.meshes))
    try:
        action()
    except ValueError:
        pass
    else:
        raise AssertionError(message)
    assert before == (len(bpy.data.objects), len(bpy.data.meshes)), 'Rejected capture leaked data'


cn.set_input(mod, merges.GLOBAL, 0)
rejected(lambda: merges.create_adhesion_patch(obj, mod), 'Zero merge accepted')
cn.set_input(mod, merges.GLOBAL, 1)
later = obj.modifiers.new('Later deformation', 'SUBSURF')
rejected(lambda: merges.create_adhesion_patch(obj, mod), 'Later modifier accepted')
obj.modifiers.remove(later)
curved.location.z = -20
snapshot(obj)
rejected(lambda: merges.create_adhesion_patch(obj, mod), 'Nonintersecting target accepted')
mod.show_viewport = False
rejected(lambda: merges.create_adhesion_patch(obj, mod), 'Disabled merge accepted')
print('PASS zero / missing intersection / later modifiers / disabled / no data leak')

# Operator registration and invocation without altering selection/source objects.
operators = importlib.import_module('patch_test.operators')
cls = operators.EZHS_OT_adhesion_patch_create
bpy.utils.register_class(cls)
try:
    mod.show_viewport = True
    curved.location.z = 0.6
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.view_layer.update()
    assert bpy.ops.ezhs.adhesion_patch_create(modifier=mod.name) == {'FINISHED'}
    assert bpy.context.active_object == obj
finally:
    bpy.utils.unregister_class(cls)
print('PASS patch operator registration / creation')
print('ALL ADHESION PATCH TESTS PASSED', bpy.app.version_string)
