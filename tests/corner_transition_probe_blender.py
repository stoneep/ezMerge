"""Isolate Spread, Push, frame rotation and ray landing without Boolean/Relax."""
import importlib
import math
from pathlib import Path
import sys
import statistics
import time
import types

import bpy
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boolean_free_probe import bypass_adhesion_boolean, build_signed_probe

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('corner_probe')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('corner_probe.merges')
builder = importlib.import_module('corner_probe.merge_adhesion')
cn = merges.cn
levels = int(sys.argv[sys.argv.index('--levels') + 1]) if '--levels' in sys.argv else 2
if not 0 <= levels <= 5:
    raise ValueError('Probe levels must be 0..5')
bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=1, depth=2, rotation=(0, math.pi / 2, 0))
base = bpy.context.object
bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.78, depth=3.6, location=(0.75, 0, 0))
obj = bpy.context.object
original_a = [tuple(v.co) for v in obj.data.vertices]
original_b = [tuple(v.co) for v in base.data.vertices]
relative = obj.matrix_world.inverted() @ base.matrix_world
bvh = BVHTree.FromPolygons([relative @ v.co for v in base.data.vertices], [list(p.vertices) for p in base.data.polygons])


def capture():
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        points = [v.co.copy() for v in mesh.vertices]
        source = [d.vector.copy() for d in mesh.attributes[merges.ADHESION_SOURCE_POSITION].data]
        weights = [d.value for d in mesh.attributes[merges.ADHESION_WEIGHT].data]
        topology = (len(points), [tuple(e.vertices) for e in mesh.edges], [tuple(p.vertices) for p in mesh.polygons])
        exposed_stretch, stretch, collapsed, hidden_leak = 0, 0, 0, 0
        order_failures = 0
        worst = None
        tangent_lengths = [d.value for d in mesh.attributes['probe_raw_tangent_length'].data]
        maximum = 0.0
        for edge in mesh.edges:
            a, b = edge.vertices
            rest = (source[a]-source[b]).length
            if rest < 1e-6:
                continue
            ratio = (points[a]-points[b]).length / rest
            order_failures += (points[b]-points[a]).dot(source[b]-source[a]) < 0.05 * rest * rest
            if ratio > maximum:
                maximum = ratio
                worst = dict(source_a=tuple(source[a]), source_b=tuple(source[b]),
                             tangent_lengths=(tangent_lengths[a], tangent_lengths[b]))
            if ratio > 4:
                stretch += 1
                midpoint = (points[a]+points[b])*0.5
                q, n, _, _ = bvh.find_nearest(midpoint)
                exposed_stretch += (midpoint-q).dot(n) >= -1e-4
        for face in mesh.polygons:
            a, b, c = face.vertices[:3]
            rest = (source[b]-source[a]).cross(source[c]-source[a]).length
            now = (points[b]-points[a]).cross(points[c]-points[a]).length
            collapsed += rest > 1e-8 and now < rest * 0.01
        for p, s in zip(points, source):
            assert all(math.isfinite(c) for c in p)
            q, n, _, _ = bvh.find_nearest(s)
            if (s-q).dot(n) < -0.02:
                q, n, _, _ = bvh.find_nearest(p)
                hidden_leak += (p-q).dot(n) > 0.001
        for p, s, w in zip(points, source, weights):
            if w < 1e-8:
                assert (p-s).length < 2e-6, 'Outside changed'
        band_movement = [(p-s).length for p, s, w in zip(points, source, weights) if w > 1e-8]
        return points, topology, dict(stretched_edges=stretch, exposed_stretched_edges=exposed_stretch,
                                     max_edge_ratio=maximum, collapsed_first_triangles=collapsed, hidden_leak=hidden_leak,
                                     source_chart_order_failures=order_failures,
                                     mean_band_displacement=sum(band_movement)/len(band_movement), worst_edge=worst)
    finally:
        evaluated.to_mesh_clear()


reference_topology = None
for case, mode, spread, push in (
    ('signed', 'signed', 0.4, 0.22),
    ('spread-zero', 'signed', 0, 0.22),
    ('push-zero', 'signed', 0.4, 0),
    ('no-rotation', 'no-rotation', 0.4, 0.22),
    ('nearest-landing', 'nearest-landing', 0.4, 0.22),
    ('smooth-target', 'smooth-target', 0.4, 0.22),
    ('coherent4', 'coherent4', 0.4, 0.22),
    ('coherent8', 'coherent8', 0.4, 0.22),
    ('limit-stretch', 'limit-stretch', 0.4, 0.22),
    ('limit-order', 'limit-order', 0.4, 0.22),
    ('transport-order', 'transport-order', 0.4, 0.22),
    ('transport-soft', 'transport-soft', 0.4, 0.22),
    ('order-soft', 'order-soft', 0.4, 0.22),
    ('tangent-damped', 'tangent-damped', 0.4, 0.22),
    ('tangent-soft', 'tangent-soft', 0.4, 0.22),
):
    if '--case' in sys.argv and case != sys.argv[sys.argv.index('--case') + 1]:
        continue
    group = bpy.data.node_groups.new('Corner diagnostic ' + case, 'GeometryNodeTree')
    build_signed_probe(builder, group, mode=mode)
    mod = obj.modifiers.new('Diagnostic', 'NODES')
    mod.node_group = group
    for name, value in ((merges.BASE, base), (merges.INFLUENCE_RADIUS, 0.55), (merges.MERGE_RADIUS, 0.3),
                        (merges.PUSH_STRENGTH, push), (merges.SURFACE_SPREAD, spread),
                        (merges.SURFACE_CORNER_REACH, 0.3), (merges.SURFACE_CLEARANCE, 0.0002),
                        (merges.SURFACE_RELAX, 0), (merges.ADHESION_LEVELS, levels)):
        cn.set_input(mod, name, value)
    bypass_adhesion_boolean(mod)
    points, topology, metrics = capture()
    if reference_topology is None:
        reference_topology = topology
    assert topology == reference_topology
    # Repeat same input, no time advance: no solver state/cached previous shape.
    again, _, _ = capture()
    assert max((p-q).length for p, q in zip(points, again)) < 1e-7
    print('METRICS', case, 'levels', levels, 'points', len(points), metrics, flush=True)
    if case.startswith(('coherent', 'limit-', 'transport-', 'order-', 'tangent-')):
        base.location.y = 0.05
        moved, moved_topology, _ = capture()
        assert moved_topology == topology
        assert max((p-q).length for p, q in zip(points, moved)) > 1e-4
        base.location.y = 0
        restored, _, _ = capture()
        assert max((p-q).length for p, q in zip(points, restored)) < 1e-6
        samples = []
        for _ in range(5):
            obj.update_tag()
            start = time.perf_counter()
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            evaluated.to_mesh()
            evaluated.to_mesh_clear()
            samples.append((time.perf_counter()-start)*1000)
        print('PASS live target movement / restoration; median evaluation ms', statistics.median(samples), flush=True)
    obj.modifiers.remove(mod)
    bpy.data.node_groups.remove(group)
assert [tuple(v.co) for v in obj.data.vertices] == original_a
assert [tuple(v.co) for v in base.data.vertices] == original_b
print('ALL CORNER ISOLATION PROBES PASSED', bpy.app.version_string, flush=True)
