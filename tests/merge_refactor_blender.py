"""Compatibility smoke test and geometry fingerprints for all merge paths."""
import hashlib
import importlib
import json
import math
from pathlib import Path
import struct
import sys
import types

import bpy

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('merge_refactor_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('merge_refactor_test.merges')

results = {}
for method, adhesion in ((merges.DEFORM, None), (merges.ROWS, None),
                         (merges.BEVEL, None), (merges.BEAD, False), (merges.BEAD, True)):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(size=4, location=(0, 0, -2))
    base = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.8, depth=3, location=(0, 0, 0.8))
    obj = bpy.context.object
    original = tuple(tuple(v.co) for v in obj.data.vertices)
    mod = merges.add_merge(obj, base, radius=0.16, strength=0.22, method=method, adhesion=adhesion)
    if adhesion:
        merges.cn.set_input(mod, merges.SURFACE_SPREAD, 0.15)
    obj.update_tag()
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        digest = hashlib.sha256()
        for vertex in mesh.vertices:
            assert all(math.isfinite(c) for c in vertex.co)
            digest.update(struct.pack('<3d', *(round(c, 6) for c in vertex.co)))
        for polygon in mesh.polygons:
            digest.update(struct.pack('<I', len(polygon.vertices)))
            for index in polygon.vertices:
                digest.update(struct.pack('<I', index))
        for normal in mesh.corner_normals:
            digest.update(struct.pack('<3d', *(round(c, 6) for c in normal.vector)))
        results[method + ('_adhesion' if adhesion else '')] = {
            'vertices': len(mesh.vertices), 'faces': len(mesh.polygons), 'digest': digest.hexdigest()}
        assert tuple(tuple(v.co) for v in obj.data.vertices) == original
    finally:
        ev.to_mesh_clear()
    assert merges.method_of(mod) == method
    assert merges.base_of(mod) == base
    assert merges.merge_modifiers(obj) == [mod]
    merges.remove_modifier(obj, mod)
    assert not merges.merge_modifiers(obj)

# Cached templates must stay separate from per-modifier editable curve copies.
template = merges.get_adhesion_group()
assert merges.get_adhesion_group() == template
owned = template.copy()
owned[merges.MERGE_OWN_KEY] = 1
assert merges.get_adhesion_group() == template
assert merges.ADHESION_KEY not in merges.get_bead_group()
bpy.data.node_groups.remove(owned)
before = set(bpy.data.node_groups.keys())

def fail_build(group):
    raise RuntimeError('Expected builder failure')

try:
    merges._cached_group('ezhs_refactor_failure', 1, 'Refactor failed fixture', fail_build)
except RuntimeError:
    pass
else:
    raise AssertionError('Builder failure was swallowed')
assert set(bpy.data.node_groups.keys()) == before, 'Failed build left an orphan group'
print('PASS cache isolation / failed-build cleanup')
print('MERGE FINGERPRINTS ' + json.dumps(results, sort_keys=True))
print('ALL MERGE COMPATIBILITY CHECKS PASSED')
