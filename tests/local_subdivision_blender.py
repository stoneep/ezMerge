"""Local refinement integrity, attachment and moving-target performance."""
import importlib
import math
from pathlib import Path
import statistics
import sys
import time
import types

import bpy
import mathutils
from mathutils.bvhtree import BVHTree

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('local_merge_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('local_merge_test.merges')
builder = importlib.import_module('local_merge_test.merge_adhesion')
cn = merges.cn
bpy.ops.mesh.primitive_cube_add(size=16, location=(0, 0, -8))
base = bpy.context.object
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=12, location=(0, 0, 1))
obj = bpy.context.object
original = [tuple(v.co) for v in obj.data.vertices]

def evaluate(check):
    obj.update_tag()
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return check(mesh)
    finally:
        ev.to_mesh_clear()

def check_closed(mesh):
    counts = {tuple(sorted(e.vertices)): 0 for e in mesh.edges}
    for face in mesh.polygons:
        assert face.area > 1e-12, 'Collapsed transition face'
        if abs(face.normal.z) < 0.9:
            center = sum((mesh.vertices[i].co for i in face.vertices), mathutils.Vector()) / len(face.vertices)
            assert face.normal.x * center.x + face.normal.y * center.y > 0, 'Inverted transition face'
        for key in face.edge_keys:
            counts[tuple(sorted(key))] += 1
    bad = [value for value in counts.values() if value != 2]
    assert not bad, ('Open/non-manifold refinement transition', len(bad), bad[:10])
    print('PASS closed conforming refinement:', len(mesh.vertices), len(mesh.polygons))

# Inspect refinement before Boolean/deformation: no T-junction/open seams.
group = bpy.data.node_groups.new('Local topology test', 'GeometryNodeTree')
group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
gin, gout = group.nodes.new('NodeGroupInput'), group.nodes.new('NodeGroupOutput')
info = group.nodes.new('GeometryNodeObjectInfo')
info.transform_space = 'RELATIVE'
info.inputs['Object'].default_value = base
for level in (0, 1, 2, 3, 4, 5):
    geometry = builder._local_subdivide(group.nodes, group.links, gin.outputs['Geometry'], info.outputs['Geometry'],
                                       merges._math(group.nodes, group.links, 'ADD', 0.4, 0),
                                       merges._math(group.nodes, group.links, 'ADD', level, 0), 'Local test')
    for link in list(gout.inputs['Geometry'].links):
        group.links.remove(link)
    group.links.new(geometry, gout.inputs['Geometry'])
    mod = obj.modifiers.new('Local topology', 'NODES')
    mod.node_group = group
    evaluate(check_closed)
    obj.modifiers.remove(mod)

bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.25, depth=4, location=(0.3, 0, 0.5))
curved = bpy.context.object
for angle in (0.35, 0.75, math.pi / 2):
    curved.rotation_euler.y = angle
    info.inputs['Object'].default_value = curved
    geometry = builder._local_subdivide(group.nodes, group.links, gin.outputs['Geometry'], info.outputs['Geometry'],
                                       merges._math(group.nodes, group.links, 'ADD', 0.4, 0),
                                       merges._math(group.nodes, group.links, 'ADD', 4, 0), 'Curved local test')
    for link in list(gout.inputs['Geometry'].links):
        group.links.remove(link)
    group.links.new(geometry, gout.inputs['Geometry'])
    probe = obj.modifiers.new('Curved topology', 'NODES')
    probe.node_group = group
    evaluate(check_closed)
    obj.modifiers.remove(probe)

mod = merges.add_merge(obj, base, radius=0.2)
cn.set_input(mod, merges.ADHESION_LEVELS, 4)
cn.set_input(mod, merges.SURFACE_SPREAD, 0.15)

def check_attachment(mesh):
    distance = mesh.attributes[merges.ADHESION_DISTANCE].data
    source = mesh.attributes[merges.ADHESION_SOURCE_POSITION].data
    weight = mesh.attributes[merges.ADHESION_WEIGHT].data
    contacts = 0
    for i, v in enumerate(mesh.vertices):
        assert all(math.isfinite(c) for c in v.co)
        if weight[i].value < 1e-8:
            assert (v.co - source[i].vector).length < 2e-6
        if distance[i].value < 1e-6:
            gap = cn.get_input(mod, merges.SURFACE_CLEARANCE)
            assert abs((obj.matrix_world @ v.co).z - gap) < 2e-5
            contacts += 1
    assert contacts
    return len(mesh.vertices), len(mesh.polygons), contacts

measurements = {}
for local in (False, True):
    cn.set_input(mod, merges.ADHESION_LOCAL_SUBDIVISION, local)
    counts = evaluate(check_attachment)
    times = []
    for x in (0.0, 0.03, 0.06, 0.09, 0.12, 0.15):
        base.location.x = x
        started = time.perf_counter()
        evaluate(lambda mesh: len(mesh.vertices))
        times.append(time.perf_counter() - started)
    measurements[local] = counts, statistics.median(times)
    print('MOVING TARGET local/vertices/faces/contact points/median seconds:', local, counts, measurements[local][1])
assert measurements[True][0][1] < measurements[False][0][1] * 0.6
assert measurements[True][0][2] >= measurements[False][0][2] * 0.7, 'Contact density severely reduced'
assert [tuple(v.co) for v in obj.data.vertices] == original
print('ALL LOCAL SUBDIVISION REGRESSIONS PASSED')
