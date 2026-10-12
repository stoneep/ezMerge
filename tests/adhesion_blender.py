"""Blender --background --factory-startup --python tests/adhesion_blender.py."""
import importlib
import math
from pathlib import Path
import sys
import types
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('adhesion_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('adhesion_test.merges')
bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, -4))
base = bpy.context.object
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=4, location=(0, 0, 1))
obj = bpy.context.object
original_a = [tuple(v.co) for v in obj.data.vertices]
original_b = [tuple(v.co) for v in base.data.vertices]
mod = merges.add_merge(obj, base, radius=0.3)
assert merges.is_surface_adhesion(mod)
cn = merges.cn
cn.set_input(mod, merges.ADHESION_LOCAL_SUBDIVISION, False)
cn.set_input(mod, merges.ADHESION_LEVELS, 4)

def mesh_snapshot(check=True):
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        positions = [v.co.copy() for v in mesh.vertices]
        assert all(math.isfinite(c) for p in positions for c in p)
        if check and mesh.attributes.get(merges.ADHESION_DISTANCE):
            distances = mesh.attributes[merges.ADHESION_DISTANCE].data
            source = mesh.attributes[merges.ADHESION_SOURCE_POSITION].data
            weights = mesh.attributes[merges.ADHESION_WEIGHT].data
            outside = 0
            contact = 0
            for i, p in enumerate(positions):
                assert p.length < 10, 'Exploding adhesion'
                if weights[i].value < 1e-8:
                    assert (p - source[i].vector).length < 2e-6, 'Outside influence changed'
                    outside += 1
                if distances[i].value < 1e-6:
                    clearance = (cn.get_input(mod, merges.SURFACE_CLEARANCE) * cn.get_input(mod, merges.GLOBAL)
                                 if cn.get_input(mod, merges.SURFACE_SPREAD) > 0 else 0.0)
                    assert abs((obj.matrix_world @ p).z - clearance) < 2e-5, 'Contact detached from slab'
                    contact += 1
            assert outside and contact
            print('PASS outside unchanged / contact attached:', outside, contact)
        return positions
    finally:
        evaluated.to_mesh_clear()

def different(a, b):
    assert len(a) == len(b)
    return max((p-q).length for p, q in zip(a, b))

reference = mesh_snapshot()
cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
covered = mesh_snapshot()
assert different(reference, covered) > 0.25, 'Surface Spread has no coverage effect'
cn.set_input(mod, merges.SURFACE_SPREAD, 0.6)
limited = mesh_snapshot()
cn.set_input(mod, merges.SURFACE_SPREAD, 100.0)
assert different(limited, mesh_snapshot()) < 2e-6, 'Spread does not obey influence safety cap'
cn.set_input(mod, merges.SURFACE_SPREAD, 0.0)
assert different(reference, mesh_snapshot()) < 2e-6
print('PASS independent coverage / clearance / spread clamp / zero restoration')
cn.set_input(mod, merges.PUSH_STRENGTH, 0.0)
no_push = mesh_snapshot()
assert different(reference, no_push) > 0.1
cn.set_input(mod, merges.PUSH_STRENGTH, 0.3)
cn.set_input(mod, merges.MERGE_RADIUS, 0.0)
no_pull = mesh_snapshot()
assert different(reference, no_pull) > 0.001
cn.set_input(mod, merges.MERGE_RADIUS, 0.3)
cn.set_input(mod, merges.INFLUENCE_RADIUS, 1.1)
wider = mesh_snapshot()
assert different(reference, wider) > 0.01
print('PASS independent influence / normal pull / tangential push')
cn.set_input(mod, merges.INFLUENCE_RADIUS, 0.6)
cn.set_input(mod, merges.GLOBAL, 2.0)
cn.set_input(mod, merges.SURFACE_SPREAD, 0.2)
scaled = mesh_snapshot()
cn.set_input(mod, merges.GLOBAL, 1.0)
for name, value in ((merges.INFLUENCE_RADIUS, 1.2), (merges.MERGE_RADIUS, 0.6),
                    (merges.PUSH_STRENGTH, 0.6), (merges.SURFACE_SPREAD, 0.4),
                    (merges.SURFACE_CLEARANCE, 0.0002), (merges.SURFACE_CORNER_REACH, 0.3)):
    cn.set_input(mod, name, value)
explicit = mesh_snapshot()
assert different(scaled, explicit) < 2e-6
print('PASS Global scales influence / merge / push / spread / clearance preserving ratios')
cn.set_input(mod, merges.SURFACE_SPREAD, 0.0)
presets = []
for value in (merges.SMOOTH, merges.LINEAR, merges.SHARP, merges.SPHERE):
    merges.set_falloff(obj, mod, value)
    presets.append(mesh_snapshot())
assert all(different(presets[0], p) > 0.001 for p in presets[1:])
cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, True)
curve = merges.curve_node(mod)
curve.mapping.curves[0].points.new(0.5, 0.8).handle_type = 'AUTO'
curve.mapping.update()
mod.node_group.update_tag()
custom = mesh_snapshot()
merges.reset_bead_curve(obj, mod)
neutral = mesh_snapshot()
assert different(custom, neutral) > 0.001
print('PASS falloff presets / custom curve / reset')
cn.set_input(mod, merges.BEAD_CUSTOM_CURVE, False)
for order in (merges.AFTER_SNAP, merges.BEFORE_SNAP):
    cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, order)
    mesh_snapshot()
print('PASS both subdivision orders')
cn.set_input(mod, merges.GLOBAL, 0.0)
off = mesh_snapshot(False)
assert [tuple(v) for v in off] == original_a
cn.set_input(mod, merges.GLOBAL, 1.0)
cn.set_input(mod, merges.PUSH_STRENGTH, 0.0)
cn.set_input(mod, merges.MERGE_RADIUS, 0.0)
assert [tuple(v) for v in mesh_snapshot(False)] == original_a
assert [tuple(v.co) for v in obj.data.vertices] == original_a
assert [tuple(v.co) for v in base.data.vertices] == original_b
print('PASS zero / no displacement bypass and source data untouched')
cn.set_input(mod, merges.SURFACE_SPREAD, 0.3)
mesh_snapshot()
cn.set_input(mod, merges.SURFACE_SPREAD, 0.0)
print('PASS spread-only deformation with push and merge zero')
cn.set_input(mod, merges.PUSH_STRENGTH, 0.3)
cn.set_input(mod, merges.MERGE_RADIUS, 0.6)
cn.set_input(mod, merges.BEAD_SYNC_NORMALS, True)
synced = mesh_snapshot()
cn.set_input(mod, merges.BEAD_SYNC_NORMALS, False)
assert different(synced, mesh_snapshot()) < 2e-6
cn.set_input(mod, merges.BASE, None)
assert [tuple(v) for v in mesh_snapshot(False)] == original_a
cn.set_input(mod, merges.BASE, base)
print('PASS normals toggle leaves geometry unchanged / missing B bypass')

# Curved contacts: test actual attachment, not just whether nodes evaluate.
obj.modifiers.remove(mod)
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.25, depth=4)
curved = bpy.context.object
mod = merges.add_merge(obj, curved, radius=0.3)
cn.set_input(mod, merges.ADHESION_LOCAL_SUBDIVISION, False)
cn.set_input(mod, merges.ADHESION_LEVELS, 3)
for angle in (0.35, 0.75, math.pi / 2):
    curved.rotation_euler.y = angle
    curved.location = (0.3, 0, 0.6)
    bpy.context.view_layer.update()
    relative = obj.matrix_world.inverted() @ curved.matrix_world
    surface = BVHTree.FromPolygons([relative @ v.co for v in curved.data.vertices],
                                   [list(p.vertices) for p in curved.data.polygons])
    for order in (merges.BEFORE_SNAP, merges.AFTER_SNAP):
        cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, order)
        obj.update_tag()
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            distance = mesh.attributes[merges.ADHESION_DISTANCE].data
            source = mesh.attributes[merges.ADHESION_SOURCE_POSITION].data
            weight = mesh.attributes[merges.ADHESION_WEIGHT].data
            contact_gaps = []
            for i, vertex in enumerate(mesh.vertices):
                assert all(math.isfinite(c) for c in vertex.co) and vertex.co.length < 10
                if weight[i].value < 1e-8:
                    assert (vertex.co - source[i].vector).length < 2e-6
                if distance[i].value < 1e-6:
                    contact_gaps.append(surface.find_nearest(vertex.co)[3])
            assert contact_gaps
            gap = max(contact_gaps)
            assert gap < 2e-5, ('Curved contact detached', angle, order, gap)
            print('PASS curved attachment / finite positions / untouched outside:', angle, order, gap)
        finally:
            evaluated.to_mesh_clear()
    # No push means no frame rotation/reconstruction. Outside the pull band,
    # even vertices still INSIDE Influence Radius must stay exactly on A.
    # This fails for the old sampled-normal position reconstruction on B's
    # faceted curved surface, which introduced the vertical rendering creases.
    cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, merges.BEFORE_SNAP)
    cn.set_input(mod, merges.PUSH_STRENGTH, 0.0)
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        distance = mesh.attributes[merges.ADHESION_DISTANCE].data
        source = mesh.attributes[merges.ADHESION_SOURCE_POSITION].data
        weights = mesh.attributes[merges.ADHESION_WEIGHT].data
        errors = [(vertex.co - source[i].vector).length
                  for i, vertex in enumerate(mesh.vertices)
                  if distance[i].value >= 0.3 and weights[i].value > 1e-8]
        assert errors and max(errors) < 2e-6, ('Spurious normal-frame displacement', angle, max(errors))
        print('PASS zero push retains A within influence but outside pull:', angle, max(errors))
    finally:
        evaluated.to_mesh_clear()
    cn.set_input(mod, merges.PUSH_STRENGTH, 0.3)
    cn.set_input(mod, merges.SURFACE_SPREAD, 0.3)
    for order in (merges.BEFORE_SNAP, merges.AFTER_SNAP):
        cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, order)
        obj.update_tag()
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            distance = mesh.attributes[merges.ADHESION_DISTANCE].data
            source = mesh.attributes[merges.ADHESION_SOURCE_POSITION].data
            weights = mesh.attributes[merges.ADHESION_WEIGHT].data
            gaps = []
            for i, vertex in enumerate(mesh.vertices):
                assert all(math.isfinite(c) for c in vertex.co) and vertex.co.length < 10
                if weights[i].value < 1e-8:
                    assert (vertex.co - source[i].vector).length < 2e-6
                if distance[i].value < 1e-6:
                    gaps.append(surface.find_nearest(vertex.co)[3])
            expected = cn.get_input(mod, merges.SURFACE_CLEARANCE)
            assert gaps and max(gaps) < expected + 2e-5, ('Spread contact gap', angle, order, max(gaps))
            assert min(gaps) > expected * 0.5, ('Spread contact clearance lost', angle, order, min(gaps))
            print('PASS curved spread / clearance / untouched outside:', angle, order, min(gaps), max(gaps))
        finally:
            evaluated.to_mesh_clear()
    cn.set_input(mod, merges.SURFACE_SPREAD, 0.0)
print('ALL SURFACE ADHESION REGRESSIONS PASSED')
