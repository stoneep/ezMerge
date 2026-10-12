"""Live spread relaxation: pins, stateless movement, cost and displacement noise."""
import importlib
import math
from pathlib import Path
import statistics
import sys
import time
import types

import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('relax_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('relax_test.merges')
builder = importlib.import_module('relax_test.merge_adhesion')
cn = merges.cn


def evaluate(obj):
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return ([v.co.copy() for v in mesh.vertices],
                [tuple(p.vertices) for p in mesh.polygons],
                [v.value for v in mesh.attributes[merges.ADHESION_WEIGHT].data],
                [v.value for v in mesh.attributes[merges.ADHESION_DISTANCE].data])
    finally:
        evaluated.to_mesh_clear()


bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, -4))
base = bpy.context.object
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=4, location=(0, 0, 1))
obj = bpy.context.object
mod = merges.add_merge(obj, base, radius=0.3)
cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
cn.set_input(mod, merges.ADHESION_LEVELS, 3)
original_a = [tuple(v.co) for v in obj.data.vertices]
original_b = [tuple(v.co) for v in base.data.vertices]
for order in (merges.BEFORE_SNAP, merges.AFTER_SNAP):
    cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, order)
    cn.set_input(mod, merges.SURFACE_RELAX, 0)
    off, faces, weights, distances = evaluate(obj)
    cn.set_input(mod, merges.SURFACE_RELAX, 0.35)
    on, on_faces, _, _ = evaluate(obj)
    assert faces == on_faces and len(off) == len(on)
    assert max((p-q).length for p, q in zip(on, off)) > 1e-6
    for i, (p, q) in enumerate(zip(on, off)):
        if weights[i] < 1e-8 or distances[i] < 1e-6:
            assert (p-q).length < 2e-6, 'Pins/outside moved'
        assert p.z + obj.location.z >= -1e-6, 'Relax penetrated B'
    cn.set_input(mod, merges.SURFACE_RELAX_PASSES, 0)
    assert all((p-q).length < 1e-7 for p, q in zip(evaluate(obj)[0], off))
    cn.set_input(mod, merges.SURFACE_RELAX_PASSES, 2)
    print('PASS both boundaries / outside / no plane penetration / zero passes:', order)

    # Test every supported budget independently from the same live input.
    influence = cn.get_input(mod, merges.INFLUENCE_RADIUS) * cn.get_input(mod, merges.GLOBAL)
    previous = off
    for passes in range(5):
        cn.set_input(mod, merges.SURFACE_RELAX_PASSES, passes)
        positions, topology, _, _ = evaluate(obj)
        assert topology == faces
        assert max((p-q).length for p, q in zip(positions, previous)) <= influence * 0.02 + 3e-6
        for i, p in enumerate(positions):
            if weights[i] < 1e-8 or distances[i] < 1e-6:
                assert (p-off[i]).length < 2e-6
        previous = positions
    cn.set_input(mod, merges.SURFACE_RELAX_PASSES, 2)
    print('PASS independent budgets 0..4 / each pass movement bounded:', order)

# No spread must not activate relaxation, regardless of the pass budget.
cn.set_input(mod, merges.SURFACE_SPREAD, 0)
cn.set_input(mod, merges.SURFACE_RELAX, 0)
no_spread = evaluate(obj)[0]
cn.set_input(mod, merges.SURFACE_RELAX, 1)
cn.set_input(mod, merges.SURFACE_RELAX_PASSES, 4)
assert max((p-q).length for p, q in zip(no_spread, evaluate(obj)[0])) < 1e-7
cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
cn.set_input(mod, merges.SURFACE_RELAX, 0.35)
cn.set_input(mod, merges.SURFACE_RELAX_PASSES, 2)
print('PASS no Surface Spread means no relaxation')

reference = evaluate(obj)[0]
counts = len(bpy.data.objects), len(bpy.data.meshes)
base.location.z += 0.2
moved = evaluate(obj)[0]
assert len(moved) != len(reference) or any((p-q).length > 1e-4 for p, q in zip(moved, reference))
base.location.z -= 0.2
restored = evaluate(obj)[0]
assert len(reference) == len(restored)
assert max((p-q).length for p, q in zip(reference, restored)) < 1e-6
assert counts == (len(bpy.data.objects), len(bpy.data.meshes))
assert [tuple(v.co) for v in obj.data.vertices] == original_a
assert [tuple(v.co) for v in base.data.vertices] == original_b
print('PASS live target motion / deterministic restoration / no snapshots or source edits')

for strength in (0, 0.35):
    cn.set_input(mod, merges.SURFACE_RELAX, strength)
    evaluate(obj)
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        evaluate(obj)
        timings.append((time.perf_counter() - start) * 1000)
    print('BENCH full adhesion evaluation, relax', strength, 'median ms', statistics.median(timings))

# Isolated helper test: alternating displacement noise on an otherwise smooth
# grid. Position smoothing would shrink this base shape; displacement relax
# must instead reduce noise while keeping both ends unchanged.
vertices, sources, faces, weights, distances = [], [], [], [], []
for row in range(9):
    for column in range(17):
        x = column / 16
        source = (x, row / 8, x * x)
        amplitude = 0.04 * math.sin(math.pi * x) ** 2
        vertices.append((x, row / 8 + amplitude * (-1 if row % 2 else 1), source[2]))
        sources.append(source)
        weights.append(1 - x)
        distances.append(x)
for row in range(8):
    for column in range(16):
        a = row * 17 + column
        faces.append((a, a + 1, a + 18, a + 17))
mesh = bpy.data.meshes.new('Relax noise fixture')
mesh.from_pydata(vertices, [], faces)
mesh.attributes.new(merges.ADHESION_SOURCE_POSITION, 'FLOAT_VECTOR', 'POINT').data.foreach_set('vector', [c for p in sources for c in p])
for name, values in ((merges.ADHESION_WEIGHT, weights), (merges.ADHESION_DISTANCE, distances)):
    mesh.attributes.new(name, 'FLOAT', 'POINT').data.foreach_set('value', values)
fixture = bpy.data.objects.new(mesh.name, mesh)
bpy.context.collection.objects.link(fixture)
group = bpy.data.node_groups.new('Relax noise fixture', 'GeometryNodeTree')
group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
nodes, links = group.nodes, group.links
gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
info = nodes.new('GeometryNodeObjectInfo')
info.inputs['Object'].default_value = base
info.transform_space = 'RELATIVE'
from relax_test.merge_nodes import _smooth_normals, _math
result = builder._relax_spread(nodes, links, gin.outputs['Geometry'], info.outputs['Geometry'],
                               _smooth_normals(nodes, links, info.outputs['Geometry'], explicit_faces=True),
                               _math(nodes, links, 'ADD', 0.6, 0), _math(nodes, links, 'ADD', 3, 0),
                               _math(nodes, links, 'ADD', 1, 0), _math(nodes, links, 'ADD', 1, 0),
                               _math(nodes, links, 'ADD', 0, 0))
links.new(result, gout.inputs['Geometry'])
fixture.modifiers.new('Live relax', 'NODES').node_group = group
relaxed = evaluate(fixture)[0]


def noise_energy(positions):
    delta = [p - Vector(source) for p, source in zip(positions, sources)]
    return sum((delta[e.vertices[0]] - delta[e.vertices[1]]).length_squared for e in mesh.edges)


initial = [Vector(v) for v in vertices]
assert noise_energy(relaxed) < noise_energy(initial) * 0.7, 'Noise not sufficiently reduced'
for i, p in enumerate(relaxed):
    if i % 17 in (0, 16):
        assert (p-initial[i]).length < 1e-7
    assert abs(p.z - sources[i][2]) < 1e-7, 'Undeformed base curvature shrank'
print('PASS displacement noise energy:', noise_energy(initial), '->', noise_energy(relaxed))
print('ALL LIVE SPREAD RELAX TESTS PASSED', bpy.app.version_string)
