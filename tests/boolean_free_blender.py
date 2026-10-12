"""Compare existing adhesion with a diagnostic Boolean-free branch."""
import importlib
import math
from pathlib import Path
import statistics
import sys
import time
import types

import bpy
from mathutils.bvhtree import BVHTree
sys.path.insert(0, str(Path(__file__).resolve().parent))
from boolean_free_probe import bypass_adhesion_boolean, build_signed_probe

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('boolean_free_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('boolean_free_test.merges')
cn = merges.cn


def snapshot(obj):
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        positions = [v.co.copy() for v in mesh.vertices]
        topology = (len(mesh.vertices), [tuple(e.vertices) for e in mesh.edges], [tuple(p.vertices) for p in mesh.polygons])
        attributes = {}
        for name in (merges.ADHESION_WEIGHT, merges.ADHESION_SOURCE_POSITION):
            attr = mesh.attributes.get(name)
            if attr:
                attributes[name] = [d.vector.copy() if attr.data_type == 'FLOAT_VECTOR' else d.value for d in attr.data]
        return positions, topology, attributes
    finally:
        evaluated.to_mesh_clear()


def subdivision_reference(obj, level):
    reference = bpy.data.objects.new('Reference simple subdivision', obj.data)
    bpy.context.collection.objects.link(reference)
    reference.matrix_world = obj.matrix_world.copy()
    ng = bpy.data.node_groups.new('Reference subdivision', 'GeometryNodeTree')
    ng.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    gin, gout = ng.nodes.new('NodeGroupInput'), ng.nodes.new('NodeGroupOutput')
    subdivide = ng.nodes.new('GeometryNodeSubdivideMesh')
    subdivide.inputs['Level'].default_value = level
    ng.links.new(gin.outputs['Geometry'], subdivide.inputs['Mesh'])
    ng.links.new(subdivide.outputs['Mesh'], gout.inputs['Geometry'])
    reference.modifiers.new('Reference', 'NODES').node_group = ng
    try:
        return snapshot(reference)[1]
    finally:
        bpy.data.objects.remove(reference, do_unlink=True)
        bpy.data.node_groups.remove(ng)


for corner in (False, True):
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=1, depth=2 if corner else 3.8,
                                      rotation=(0, math.pi / 2, 0))
    base = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.78, depth=3.6,
                                      location=(0.75 if corner else 0, 0, 0))
    obj = bpy.context.object
    mod = merges.add_merge(obj, base, radius=0.3)
    cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
    cn.set_input(mod, merges.SURFACE_CORNER_REACH, 0.3)
    cn.set_input(mod, merges.ADHESION_LEVELS, 3)
    with_cut = snapshot(obj)
    original_a = [tuple(v.co) for v in obj.data.vertices]
    original_b = [tuple(v.co) for v in base.data.vertices]
    bypass_adhesion_boolean(mod)
    cn.set_input(mod, merges.SURFACE_RELAX, 0)
    unsigned = snapshot(obj)
    if '--signed-probe' in sys.argv:
        settings = {item.name: cn.get_input(mod, item.name) for item in mod.node_group.interface.items_tree
                    if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name != 'Geometry'}
        group = bpy.data.node_groups.new('Signed distance diagnostic', 'GeometryNodeTree')
        build_signed_probe(importlib.import_module('boolean_free_test.merge_adhesion'), group)
        mod.node_group = group
        for name, value in settings.items():
            cn.set_input(mod, name, value)
        bypass_adhesion_boolean(mod)
    assert not any(n.bl_idname == 'GeometryNodeMeshBoolean' for n in mod.node_group.nodes)
    for level in (0, 2, 3):
        cn.set_input(mod, merges.ADHESION_LEVELS, level)
        expected = subdivision_reference(obj, level)
        for order in (merges.BEFORE_SNAP, merges.AFTER_SNAP):
            cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, order)
            positions, topology, attrs = snapshot(obj)
            assert topology == expected, 'Boolean-free adhesion changed connectivity'
            assert all(math.isfinite(c) for p in positions for c in p)
            assert max(p.length for p in positions) < 10
            for p, source, weight in zip(positions, attrs[merges.ADHESION_SOURCE_POSITION], attrs[merges.ADHESION_WEIGHT]):
                if weight < 1e-8:
                    assert (p-source).length < 2e-6
        print('PASS exact subdivision topology / finite / outside unchanged:', 'cap' if corner else 'side', level)
    cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, merges.BEFORE_SNAP)
    reference = snapshot(obj)
    base.location.y = 0.2
    moved = snapshot(obj)
    assert moved[1] == reference[1], 'Topology changed during movement without Boolean'
    assert max((p-q).length for p, q in zip(reference[0], moved[0])) > 0.001
    base.location.y = 0
    restored = snapshot(obj)
    assert max((p-q).length for p, q in zip(reference[0], restored[0])) < 1e-6
    cn.set_input(mod, merges.SURFACE_SPREAD, 0)
    no_spread = snapshot(obj)[0]
    cn.set_input(mod, merges.SURFACE_SPREAD, 0.4)
    assert max((p-q).length for p, q in zip(no_spread, snapshot(obj)[0])) > 0.01
    assert [tuple(v.co) for v in obj.data.vertices] == original_a
    assert [tuple(v.co) for v in base.data.vertices] == original_b
    relative = obj.matrix_world.inverted() @ base.matrix_world
    bvh = BVHTree.FromPolygons([relative @ v.co for v in base.data.vertices], [list(p.vertices) for p in base.data.polygons])
    def leakage(data):
        leaked = hidden = 0
        for p, source in zip(data[0], data[2][merges.ADHESION_SOURCE_POSITION]):
            q, n, _, _ = bvh.find_nearest(source)
            if (source-q).dot(n) < -0.02:
                hidden += 1
                q, n, _, _ = bvh.find_nearest(p)
                leaked += (p-q).dot(n) > 0.001
        return leaked, hidden
    print('PROBE originally hidden points emerging:', 'unsigned', leakage(unsigned), 'current', leakage(reference))
    inside = 0
    for p in reference[0]:
        nearest, normal, _, _ = bvh.find_nearest(p)
        inside += (p-nearest).dot(normal) < -1e-5
    times = []
    for _ in range(5):
        start = time.perf_counter()
        snapshot(obj)
        times.append((time.perf_counter()-start)*1000)
    print('PROBE', 'cap' if corner else 'side', 'cut points', with_cut[1][0], 'uncut points', reference[1][0],
          'interior points retained', inside, 'uncut median ms', statistics.median(times))
    print('PASS live motion / repeatability / Surface Spread works / source data unchanged')
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(base, do_unlink=True)
print('ALL BOOLEAN-FREE PROBES PASSED', bpy.app.version_string)
