"""Curvature-adaptive spread: flat, curved, sharp-turn and zero-travel fixtures."""
import importlib
import math
from pathlib import Path
import sys
import statistics
import time
import types

import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('spread_curvature_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('spread_curvature_test.merges')
builder = importlib.import_module('spread_curvature_test.merge_adhesion')
from spread_curvature_test.merge_nodes import _math, _vector, _switch, _surface_normal_sampler
from spread_curvature_test.merge_settings import *

def fixed_reference(nodes, links, shaded, target, landed, tangent, spread, weight, spreading):
    """Bounded surface walk with direction transport and sharp-edge stopping."""
    square = lambda value: _math(nodes, links, 'MULTIPLY', value, value)
    # Keep extra coverage distinct from Push: it is concentrated near the
    # contact (weight squared), and walks B in bounded reprojected steps.
    # Transport the direction in B's tangent plane instead of taking one long
    # jump that may cut across a cylinder or skip a cap edge.
    spread_start = landed
    direction = tangent
    step = _math(nodes, links, 'MULTIPLY', spread,
                 _math(nodes, links, 'MULTIPLY', square(weight), 1.0 / ADHESION_SPREAD_STEPS))
    for _ in range(ADHESION_SPREAD_STEPS):
        walk_normal = _surface_normal_sampler(nodes, links, shaded)
        links.new(landed, walk_normal.inputs['Sample Position'])
        walk_n = _vector(nodes, links, 'NORMALIZE', walk_normal.outputs['Value'])
        transported = _vector(nodes, links, 'SUBTRACT', direction,
                              _vector(nodes, links, 'SCALE', walk_n,
                                      scale=_vector(nodes, links, 'DOT_PRODUCT', direction, walk_n)))
        direction = _vector(nodes, links, 'NORMALIZE', transported)
        proposal = _vector(nodes, links, 'ADD', landed, _vector(nodes, links, 'SCALE', direction, scale=step))
        project = nodes.new('GeometryNodeProximity')
        project.target_element = 'FACES'
        links.new(target, project.inputs['Target'])
        links.new(proposal, project.inputs['Source Position'])
        # A sudden normal turn is a cap/sharp edge, not a smooth continuation.
        next_normal = _surface_normal_sampler(nodes, links, shaded)
        links.new(project.outputs['Position'], next_normal.inputs['Sample Position'])
        safe_turn = _math(nodes, links, 'GREATER_THAN',
                          _vector(nodes, links, 'DOT_PRODUCT', walk_n,
                                  _vector(nodes, links, 'NORMALIZE', next_normal.outputs['Value'])), ADHESION_MAX_TURN_COS)
        usable = _math(nodes, links, 'GREATER_THAN', _vector(nodes, links, 'LENGTH', transported), ADHESION_MIN_TANGENT_LENGTH)
        safe_step = _math(nodes, links, 'MULTIPLY', safe_turn, usable)
        landed = _switch(nodes, links, 'VECTOR', safe_step, landed, project.outputs['Position'])
        direction = _vector(nodes, links, 'SCALE', direction, scale=safe_step)
    landed = _switch(nodes, links, 'VECTOR', spreading, spread_start, landed)
    return landed



def run_walk(target, start, tangent, amount, adaptive, fixed_mode=False, corner_reach=None):
    data = bpy.data.meshes.new('Walk point')
    data.from_pydata([start], [], [])
    obj = bpy.data.objects.new('Walk point', data)
    bpy.context.collection.objects.link(obj)
    group = bpy.data.node_groups.new('Walk test', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    info.inputs['Object'].default_value = target
    shaded = merges._smooth_normals(nodes, links, info.outputs['Geometry'], explicit_faces=True)
    vector = nodes.new('ShaderNodeCombineXYZ')
    for socket, value in zip(vector.inputs, tangent):
        socket.default_value = value
    position = nodes.new('GeometryNodeInputPosition').outputs[0]
    spread = _math(nodes, links, 'ADD', amount, 0)
    weight = _math(nodes, links, 'ADD', 1, 0)
    enabled = _math(nodes, links, 'GREATER_THAN', spread, 1e-8)
    geometry = gin.outputs['Geometry']
    if adaptive:
        point, planned, taken = builder._spread_over_surface(
            nodes, links, shaded, info.outputs['Geometry'], position, vector.outputs[0], spread, weight, enabled,
            _math(nodes, links, 'ADD', 0 if fixed_mode else 1, 0),
            None if corner_reach is None else _math(nodes, links, 'ADD', corner_reach, 0))
        for name, value in (('planned', planned), ('taken', taken)):
            store = nodes.new('GeometryNodeStoreNamedAttribute')
            store.data_type, store.domain = 'FLOAT', 'POINT'
            store.inputs['Name'].default_value = name
            links.new(geometry, store.inputs['Geometry'])
            links.new(value, store.inputs['Value'])
            geometry = store.outputs['Geometry']
    else:
        point = fixed_reference(nodes, links, shaded, info.outputs['Geometry'],
                                position, vector.outputs[0], spread, weight, enabled)
    move = nodes.new('GeometryNodeSetPosition')
    links.new(geometry, move.inputs['Geometry'])
    links.new(point, move.inputs['Position'])
    links.new(move.outputs['Geometry'], gout.inputs['Geometry'])
    mod = obj.modifiers.new('Walk', 'NODES')
    mod.node_group = group
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        result = mesh.vertices[0].co.copy()
        assert all(math.isfinite(v) for v in result)
        counts = (mesh.attributes['planned'].data[0].value, mesh.attributes['taken'].data[0].value) if adaptive else (4, 4)
        assert 0 <= counts[1] <= counts[0] <= 8
        return result, counts
    finally:
        ev.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(data)
        bpy.data.node_groups.remove(group)

bpy.ops.mesh.primitive_plane_add(size=10)
plane = bpy.context.object
point, steps = run_walk(plane, (0, 0, 0), (1, 0, 0), 0.8, True)
assert steps == (1, 1), steps
assert (point - Vector((0.8, 0, 0))).length < 1e-6
point, steps = run_walk(plane, (0, 0, 0), (1, 0, 0), 0.0, True)
assert steps == (0, 0) and point.length < 1e-6
print('PASS flat uses one step / zero spread uses zero steps')

counts = []
for radius in (2.0, 1.0, 0.25):
    bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=radius, depth=3)
    target = bpy.context.object
    start = (radius, 0, 0)
    amount = 0.8
    adaptive, steps = run_walk(target, start, (0, 1, 0), amount, True)
    fixed, _ = run_walk(target, start, (0, 1, 0), amount, False)
    toggled_fixed, toggled_counts = run_walk(target, start, (0, 1, 0), amount, True, fixed_mode=True)
    assert (toggled_fixed - fixed).length < 1e-6 and toggled_counts[0] == 4
    counts.append(steps[0])
    adaptive_angle = math.atan2(adaptive.y, adaptive.x)
    fixed_angle = math.atan2(fixed.y, fixed.x)
    if adaptive_angle < 0:
        adaptive_angle += 2 * math.pi
    if fixed_angle < 0:
        fixed_angle += 2 * math.pi
    adaptive_error = abs(adaptive_angle - amount / radius)
    fixed_error = abs(fixed_angle - amount / radius)
    assert abs(adaptive.length - radius) < 0.001
    if radius == 0.25:
        assert adaptive_error < fixed_error * 0.6, (adaptive_error, fixed_error)
    print('CURVATURE radius/planned/taken/adaptive angular error/fixed error:', radius, *steps, adaptive_error, fixed_error)
assert counts[0] < counts[-1] and counts[-1] == 8, counts

bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
point, steps = run_walk(cube, (0.8, 0, 1), (1, 0, 0), 0.8, True)
assert max(abs(c) for c in point) <= 1.00001, ('Escaped sharp target', point)
print('PASS bounded sharp-edge walk:', tuple(point), steps)
bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=1, depth=2)
cap_target = bpy.context.object
for fixed_mode in (False, True):
    blocked, _ = run_walk(cap_target, (1, 0, 0.85), (0, 0, 1), 0.6, True,
                          fixed_mode=fixed_mode, corner_reach=0.0)
    wrapped, steps = run_walk(cap_target, (1, 0, 0.85), (0, 0, 1), 0.6, True,
                              fixed_mode=fixed_mode, corner_reach=0.3)
    print('CAP TRANSITION fixed/blocked/wrapped/steps:', fixed_mode, tuple(blocked), tuple(wrapped), steps)
    assert abs(wrapped.z - 1) < 2e-5 and wrapped.x < 0.9, ('Cap not reached', wrapped)
    assert wrapped.x < blocked.x - 0.05
    x_values = []
    for reach in (0.005, 0.02, 0.1, 0.3):
        gradual, _ = run_walk(cap_target, (1, 0, 0.85), (0, 0, 1), 0.6, True,
                              fixed_mode=fixed_mode, corner_reach=reach)
        assert abs(gradual.z - 1) < 2e-5
        x_values.append(gradual.x)
    assert all(a >= b - 1e-5 for a, b in zip(x_values, x_values[1:])), x_values
    assert x_values[0] > x_values[-1] + 0.2, x_values
    print('PASS gradual Corner Reach:', fixed_mode, x_values)
    reverse, _ = run_walk(cap_target, (0.85, 0, 1), (1, 0, 0), 0.6, True,
                          fixed_mode=fixed_mode, corner_reach=0.3)
    assert abs(math.hypot(reverse.x, reverse.y) - 1) < 0.001 and reverse.z < 0.9, reverse
    print('PASS cap to side transition:', fixed_mode, tuple(reverse))
uncapped_data = bpy.data.meshes.new('No adjacent cap')
uncapped_data.from_pydata([tuple(v.co) for v in cap_target.data.vertices], [],
                         [list(p.vertices) for p in cap_target.data.polygons if abs(p.normal.z) < 0.9])
uncapped = bpy.data.objects.new('No adjacent cap', uncapped_data)
bpy.context.collection.objects.link(uncapped)
point, _ = run_walk(uncapped, (1, 0, 0.85), (0, 0, 1), 0.6, True, corner_reach=0.3)
assert abs(point.x - 1) < 1e-5 and abs(point.z - 1) < 1e-5, point
print('PASS missing adjacent face does not invent a cap')
print('ALL CURVATURE SPREAD REGRESSIONS PASSED')
if '--skip-benchmark' in sys.argv:
    raise SystemExit(0)

# End-to-end plane benchmark, not an inference from the chosen step count.
bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, -4))
slab = bpy.context.object
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=4, location=(0, 0, 1))
source = bpy.context.object
adaptive_function = builder._spread_over_surface

def fixed_adapter(nodes, links, shaded, target, landed, tangent, spread, weight, spreading, adaptive=None, corner_reach=None):
    point = fixed_reference(nodes, links, shaded, target, landed, tangent, spread, weight, spreading)
    count = _switch(nodes, links, 'FLOAT', spreading, 0, 4)
    return point, count, count

try:
    for name, function in (('fixed4', fixed_adapter), ('adaptive', adaptive_function)):
        builder._spread_over_surface = function
        group = bpy.data.node_groups.new('Spread benchmark', 'GeometryNodeTree')
        builder._build_adhesion(group)
        mod = source.modifiers.new('Spread benchmark', 'NODES')
        mod.node_group = group
        merges.cn.set_input(mod, BASE, slab)
        merges.cn.set_input(mod, INFLUENCE_RADIUS, 0.6)
        merges.cn.set_input(mod, MERGE_RADIUS, 0.3)
        merges.cn.set_input(mod, PUSH_STRENGTH, 0.3)
        merges.cn.set_input(mod, SURFACE_SPREAD, 0.3)
        timings = []
        for index in range(9):
            slab.location.x = index * 0.02
            source.update_tag()
            started = time.perf_counter()
            bpy.context.view_layer.update()
            ev = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = ev.to_mesh()
            assert len(mesh.vertices) > 1000
            ev.to_mesh_clear()
            if index > 1:
                timings.append(time.perf_counter() - started)
        print('PLANE SPREAD BENCHMARK mode/median seconds:', name, statistics.median(timings))
        source.modifiers.remove(mod)
        bpy.data.node_groups.remove(group)
finally:
    builder._spread_over_surface = adaptive_function
