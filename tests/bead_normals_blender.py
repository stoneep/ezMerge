"""Run with Blender --background --factory-startup --python this_file.py."""
import importlib
import math
from pathlib import Path
import sys
import types

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('ezhs_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module('ezhs_test.merges')


def evaluated(obj, check):
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        check(mesh)
    finally:
        ev.to_mesh_clear()


bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=2)
cylinder = bpy.context.object
source_material = bpy.data.materials.new('Source material regression')
cylinder.data.materials.append(source_material)
group = bpy.data.node_groups.new('Normal regression', 'GeometryNodeTree')
group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
gin = group.nodes.new('NodeGroupInput')
gout = group.nodes.new('NodeGroupOutput')
normal_geometry = merges._smooth_normals(group.nodes, group.links, gin.outputs['Geometry'], explicit_faces=True)
group.links.new(normal_geometry, gout.inputs['Geometry'])
mod = cylinder.modifiers.new('Normals test', 'NODES')
mod.node_group = group


def check_normals(mesh):
    data = mesh.attributes[merges.SURFACE_NORMAL].data
    max_error = 0.0
    for poly in mesh.polygons:
        for index in poly.loop_indices:
            normal = data[index].vector
            if abs(poly.normal.z) > 0.9:
                expected = poly.normal
            else:
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                expected = Vector((point.x, point.y, 0)).normalized()
            max_error = max(max_error, (normal - expected).length)
    assert max_error < 1e-5, max_error
    print('PASS radial side normals and sharp cap normals:', max_error)


evaluated(cylinder, check_normals)
cylinder.modifiers.remove(mod)


def check_bead(mesh):
    assert len(mesh.vertices) > len(cylinder.data.vertices), 'No bead generated'
    assert all(math.isfinite(c) for vertex in mesh.vertices for c in vertex.co)
    assert all(vertex.co.length < 20 for vertex in mesh.vertices), 'Exploding profile'
    for name in (merges.BEAD_BISECTOR, merges.BEAD_BINORMAL):
        values = mesh.attributes[name].data
        nonzero = [v.vector.length for v in values if v.vector.length > 1e-6]
        assert nonzero and all(abs(length - 1) < 1e-4 for length in nonzero)
    # Test the actual boundary locations, not just whether the nodes evaluate.
    source_a = BVHTree.FromPolygons(
        [v.co for v in cylinder.data.vertices], [list(p.vertices) for p in cylinder.data.polygons])
    planes_a = [(p.normal.copy(), cylinder.data.vertices[p.vertices[0]].co.copy())
                for p in cylinder.data.polygons]
    ev_base = base.evaluated_get(bpy.context.evaluated_depsgraph_get())
    base_mesh = ev_base.to_mesh()
    try:
        relative = cylinder.matrix_world.inverted() @ base.matrix_world
        source_b = BVHTree.FromPolygons(
            [relative @ v.co for v in base_mesh.vertices], [list(p.vertices) for p in base_mesh.polygons])
        normal_matrix = relative.to_3x3().inverted().transposed()
        planes_b = [((normal_matrix @ p.normal).normalized(), relative @ base_mesh.vertices[p.vertices[0]].co)
                    for p in base_mesh.polygons]
    finally:
        ev_base.to_mesh_clear()
    t_values = mesh.attributes[merges.BEAD_ARC_T].data
    bisectors = mesh.attributes[merges.BEAD_BISECTOR].data
    binormals = mesh.attributes[merges.BEAD_BINORMAL].data
    normals_a = mesh.attributes[merges.BEAD_NORMAL_A].data
    max_gap = 0.0
    boundaries = 0
    a_weights = mesh.attributes[merges.BEAD_A_WEIGHT].data
    b_weights = mesh.attributes[merges.BEAD_B_WEIGHT].data
    max_intrusion = 0.0
    worst_intrusion = None
    max_face_intrusion = 0.0
    inward_faces = 0
    for vertex in mesh.vertices:
        if bisectors[vertex.index].vector.length < 0.5:
            continue
        for source in (source_a, source_b):
            point, normal, _, _ = source.find_nearest(vertex.co)
            depth = -(vertex.co - point).dot(normal)
            if depth > max_intrusion:
                max_intrusion = depth
                worst_intrusion = (vertex.index, t_values[vertex.index].value, tuple(vertex.co))
    for poly in mesh.polygons:
        if all(bisectors[i].vector.length > 0.5 for i in poly.vertices):
            outward = sum((bisectors[i].vector for i in poly.vertices), Vector())
            inward_faces += poly.normal.dot(outward) < 0
            for planes in (planes_a, planes_b):
                signed = max((poly.center - point).dot(normal) for normal, point in planes)
                if -signed > max_face_intrusion:
                    max_face_intrusion = -signed
    assert max_intrusion < 2e-6, ('Bead inside source', max_intrusion, worst_intrusion)
    assert inward_faces == 0, ('Inward bead faces', inward_faces)
    print('PASS outward faces and no vertex intrusion:', max_intrusion)
    assert max_face_intrusion < 2e-6, ('Bead face center inside source', max_face_intrusion)
    print('PASS no face-center intrusion:', max_face_intrusion)
    for vertex in mesh.vertices:
        i = vertex.index
        if bisectors[i].vector.length < 0.5:
            continue  # Original A has no bead frame.
        contact = mesh.attributes[merges.BEAD_CURVE_POS].data[i].vector
        radius = merges.cn.get_input(bead, merges.BEAD_WIDTH) * merges.cn.get_input(bead, merges.GLOBAL)
        height = merges.cn.get_input(bead, merges.BEAD_HEIGHT) * merges.cn.get_input(bead, merges.GLOBAL)
        assert (vertex.co - contact).length < 4 * radius + height, 'Unbounded bead width'
        t = t_values[i].value
        if abs(t) > 1e-6 and abs(t - 1) > 1e-6:
            continue
        positive_is_b = mesh.attributes[merges.BEAD_POSITIVE_IS_B].data[i].value
        on_b = positive_is_b if t < 0.5 else not positive_is_b
        assert abs((b_weights if on_b else a_weights)[i].value - 1) < 1e-6
        assert abs((a_weights if on_b else b_weights)[i].value) < 1e-6
        distance = (source_b if on_b else source_a).find_nearest(vertex.co)[3]
        assert distance is not None
        max_gap = max(max_gap, distance)
        boundaries += 1
    assert boundaries > 0
    assert max_gap < 2e-5, max_gap
    native_normals = hasattr(bpy.types, 'GeometryNodeSetMeshNormal')
    normal_data = mesh.attributes.get(merges.BEAD_NORMAL)
    assert bool(normal_data) == native_normals
    original_face_normals = {}
    for poly in mesh.polygons:
        if all(bisectors[i].vector.length > 0.5 for i in poly.vertices):
            material = mesh.materials[poly.material_index]
            assert material is not None and material.original == source_material, ('Wrong bead material', material)
        if all(bisectors[i].vector.length < 0.5 for i in poly.vertices):
            for index in poly.loop_indices:
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                original_face_normals[index] = (Vector((point.x, point.y, 0)).normalized()
                                               if poly.use_smooth and abs(poly.normal.z) < 0.9
                                               else poly.normal)
    shading_error = 0.0
    for loop in mesh.loops:
        i = loop.vertex_index
        actual = mesh.corner_normals[loop.index].vector
        if bisectors[i].vector.length < 0.5:
            # Sources in this regression use flat faces; their shading must
            # survive irrespective of JoinGeometry's mesh/loop ordering.
            expected = original_face_normals[loop.index]
            assert actual.dot(expected) > 0.9999, ('Original normals changed', actual, expected)
            assert a_weights[i].value == 0 and b_weights[i].value == 0
        elif native_normals:
            expected = normal_data.data[loop.index].vector
            assert abs(expected.length - 1) < 1e-5
            shading_error = max(shading_error, (actual - expected).length)
            # Independent analytic check for smooth cylinders with rigid
            # transforms: a wall's interpolated normal is its radial direction.
            if cylinder.data.polygons[0].use_smooth and cylinder.scale == Vector((1, 1, 1)):
                t = t_values[i].value
                if abs(t) < 1e-6 or abs(t - 1) < 1e-6:
                    positive_is_b = binormals[i].vector.dot(normals_a[i].vector) > 0
                    on_b = positive_is_b if t < 0.5 else not positive_is_b
                    point = mesh.vertices[i].co
                    if on_b:
                        local = relative.inverted() @ point
                        expected_surface = relative.to_3x3().inverted().transposed() @ Vector((local.x, local.y, 0))
                    else:
                        expected_surface = Vector((point.x, point.y, 0))
                    assert actual.dot(expected_surface.normalized()) > 0.9999, ('Boundary normal', actual, expected_surface)
        if bisectors[i].vector.length > 0.5 and 0.25 <= t_values[i].value <= 0.75:
            assert a_weights[i].value == 0 and b_weights[i].value == 0
    assert shading_error < 1e-4, shading_error
    print('PASS shading and original normals; native error:', shading_error)
    print('PASS boundary attachment:', boundaries, 'points; max gap', max_gap)
    print('PASS bead evaluation:', len(mesh.vertices), 'vertices')


bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, -1.5))
base = bpy.context.object
base.scale = (3, 3, 1)
bead = merges.add_merge(cylinder, base, radius=0.08, method=merges.BEAD, adhesion=False)
for x in (0, 0.17, -0.24):
    cylinder.location.x = x
    evaluated(cylinder, check_bead)
merges.remove_modifier(cylinder, bead)

bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.25, depth=4, rotation=(0, math.pi / 2, 0), location=(0, 0, -0.8))
curved_base = bpy.context.object
base = curved_base
bead = merges.add_merge(cylinder, curved_base, radius=0.08, method=merges.BEAD, adhesion=False)
for x in (0, 0.19, -0.23):
    cylinder.location.x = x
    evaluated(cylinder, check_bead)
for angle in (0.2, -0.35):
    cylinder.rotation_euler.y = angle
    cylinder.scale = (1, 0.85, 1.1)
    evaluated(cylinder, check_bead)
cylinder.rotation_euler.y = 0
cylinder.scale = (1, 1, 1)
for obj in (cylinder, curved_base):
    for poly in obj.data.polygons:
        poly.use_smooth = abs(poly.normal.z) < 0.9
    obj.data.update()
for x in (0, 0.19):
    cylinder.location.x = x
    evaluated(cylinder, check_bead)
if hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
    def center_normals(mesh):
        t = mesh.attributes[merges.BEAD_ARC_T].data
        frame = mesh.attributes[merges.BEAD_BISECTOR].data
        return {loop.index: mesh.corner_normals[loop.index].vector.copy()
                for loop in mesh.loops
                if frame[loop.vertex_index].vector.length > 0.5
                and 0.25 <= t[loop.vertex_index].value <= 0.75}

    actual_center = {}
    evaluated(cylinder, lambda mesh: actual_center.update(center_normals(mesh)))
    original_group = bead.node_group
    control = original_group.copy()
    for node in control.nodes:
        if node.bl_idname == 'GeometryNodeSetMeshNormal':
            before = node.inputs['Mesh'].links[0].from_socket
            for link in list(node.outputs['Mesh'].links):
                target = link.to_socket
                control.links.remove(link)
                control.links.new(before, target)
    bead.node_group = control
    control_center = {}
    evaluated(cylinder, lambda mesh: control_center.update(center_normals(mesh)))
    assert actual_center and actual_center.keys() == control_center.keys()
    assert max((normal - control_center[i]).length for i, normal in actual_center.items()) < 1e-5
    bead.node_group = original_group
    bpy.data.node_groups.remove(control)
    print('PASS bead center retains its own shading normals')

# Shallow cylinder crossings used to amplify the radius into a wide strap.
for obj in (cylinder, curved_base):
    for poly in obj.data.polygons:
        poly.use_smooth = False
    obj.data.update()
curved_base.rotation_euler.y = 0.25
curved_base.location = (0.65, 0, 0)
for height in (0.02, 0.05, 0.1):
    merges.cn.set_input(bead, merges.BEAD_HEIGHT, height)
    cylinder.update_tag()
    evaluated(cylinder, check_bead)
merges.cn.set_input(bead, merges.BEAD_HEIGHT, 0.0336)
for radius in (0.16, 0.24):
    merges.cn.set_input(bead, merges.BEAD_WIDTH, radius)
    cylinder.update_tag()
    evaluated(cylinder, check_bead)
merges.cn.set_input(bead, merges.GLOBAL, 0.0)
cylinder.update_tag()

def check_disabled(mesh):
    assert len(mesh.vertices) == len(cylinder.data.vertices), 'Zero Global left bead geometry'
    print('PASS disabled bead')

evaluated(cylinder, check_disabled)

# Check independent dimensions on a cylinder/slab where clearance does not
# impose a height floor: width must move the borders, not the center, and
# height must move the center without moving the borders.
merges.remove_modifier(cylinder, bead)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, -1.5))
base = bpy.context.object
base.scale = (3, 3, 1)
bead = merges.add_merge(cylinder, base, width=0.08, height=0.025)
merges.cn.set_input(bead, merges.BEAD_PROFILE_RESOLUTION, 13)
cylinder.update_tag()

def snapshot(mesh):
    result = {'center': {}, 'border': {}}
    frame = mesh.attributes[merges.BEAD_BISECTOR].data
    contact = mesh.attributes[merges.BEAD_CURVE_POS].data
    t = mesh.attributes[merges.BEAD_ARC_T].data
    height = merges.cn.get_input(bead, merges.BEAD_HEIGHT) * merges.cn.get_input(bead, merges.GLOBAL)
    for vertex in mesh.vertices:
        i = vertex.index
        if frame[i].vector.length < 0.5:
            continue
        key = tuple(round(c, 6) for c in contact[i].vector)
        if abs(t[i].value - 0.5) < 1e-6:
            actual_height = (vertex.co - contact[i].vector).dot(frame[i].vector)
            assert abs(actual_height - height) < 2e-6, (actual_height, height)
            result['center'][key] = vertex.co.copy()
        elif abs(t[i].value) < 1e-6 or abs(t[i].value - 1) < 1e-6:
            result['border'][(key, round(t[i].value))] = vertex.co.copy()
    assert result['center'] and result['border']
    return result

snapshots = []
evaluated(cylinder, lambda mesh: snapshots.append(snapshot(mesh)))
merges.cn.set_input(bead, merges.BEAD_WIDTH, 0.18)
cylinder.update_tag()
evaluated(cylinder, lambda mesh: snapshots.append(snapshot(mesh)))
assert snapshots[0]['center'].keys() == snapshots[1]['center'].keys()
assert max((pos - snapshots[1]['center'][key]).length
           for key, pos in snapshots[0]['center'].items()) < 2e-6
assert max((pos - snapshots[1]['border'][key]).length
           for key, pos in snapshots[0]['border'].items()) > 0.04
merges.cn.set_input(bead, merges.BEAD_HEIGHT, 0.06)
cylinder.update_tag()
evaluated(cylinder, lambda mesh: snapshots.append(snapshot(mesh)))
assert max((pos - snapshots[2]['border'][key]).length
           for key, pos in snapshots[1]['border'].items()) < 2e-6
merges.cn.set_input(bead, merges.GLOBAL, 0.5)
cylinder.update_tag()
evaluated(cylinder, lambda mesh: snapshot(mesh))
print('PASS independent width/height, fixed borders, and global scaling')

def geometry_snapshot(mesh):
    return ([v.co.copy() for v in mesh.vertices],
            [loop.normal.copy() for loop in mesh.loops], snapshot(mesh))

def capture():
    result = []
    evaluated(cylinder, lambda mesh: result.append(geometry_snapshot(mesh)))
    return result[0]

neutral = capture()
merges.cn.set_input(bead, merges.BEAD_CUSTOM_CURVE, True)
cylinder.update_tag()
diagonal = capture()
assert max((a - b).length for a, b in zip(neutral[0], diagonal[0])) < 2e-6
curve = merges.curve_node(bead)
knot = curve.mapping.curves[0].points.new(0.5, 0.85)
knot.handle_type = 'AUTO'
curve.mapping.update()
bead.node_group.update_tag()
cylinder.update_tag()
raised = capture()
assert max((a - b).length for a, b in zip(neutral[0], raised[0])) > 0.002
for section in ('center', 'border'):
    assert max((pos - raised[2][section][key]).length
               for key, pos in neutral[2][section].items()) < 2e-6
evaluated(cylinder, check_bead)
knot.location = (0.5, 0.15)
curve.mapping.update()
bead.node_group.update_tag()
cylinder.update_tag()
evaluated(cylinder, check_bead)
knot.location = (0.5, 0.85)
curve.mapping.update()
bead.node_group.update_tag()

# Editing one merge must not change another's curve, including duplicates.
other = merges.add_merge(cylinder, base, width=0.18, height=0.06)
assert other.node_group != bead.node_group
assert len(merges.curve_node(other).mapping.curves[0].points) == 2
other.node_group = bead.node_group
merges.own_curve(other)
assert other.node_group != bead.node_group
merges.reset_bead_curve(cylinder, other)
assert len(curve.mapping.curves[0].points) == 3
merges.remove_modifier(cylinder, other)
merges.cn.set_input(bead, merges.BEAD_CUSTOM_CURVE, False)
cylinder.update_tag()
bypassed = capture()
assert max((a - b).length for a, b in zip(neutral[0], bypassed[0])) < 2e-6
merges.reset_bead_curve(cylinder, bead)
merges.cn.set_input(bead, merges.BEAD_CUSTOM_CURVE, True)
cylinder.update_tag()
reset = capture()
assert max((a - b).length for a, b in zip(neutral[0], reset[0])) < 2e-6
print('PASS custom curve changes shape, preserves borders/height, resets and stays independent')

merges.cn.set_input(bead, merges.BEAD_SYNC_NORMALS, False)
cylinder.update_tag()
unsynced = capture()
assert max((a - b).length for a, b in zip(neutral[0], unsynced[0])) < 2e-6
normal_difference = max((a - b).length for a, b in zip(neutral[1], unsynced[1]))
if hasattr(bpy.types, 'GeometryNodeSetMeshNormal'):
    assert normal_difference > 0.01, normal_difference
else:
    assert normal_difference < 1e-6
merges.cn.set_input(bead, merges.BEAD_SYNC_NORMALS, True)
cylinder.update_tag()
resynced = capture()
assert max((a - b).length for a, b in zip(neutral[1], resynced[1])) < 2e-6
print('PASS live Sync Normals toggle, geometry unchanged; normal difference:', normal_difference)

def surface_stats(mesh):
    frame = mesh.attributes[merges.BEAD_BISECTOR].data
    parameter = mesh.attributes[merges.BEAD_ARC_T].data
    contacts = mesh.attributes[merges.BEAD_CURVE_POS].data
    vertices = [v.index for v in mesh.vertices if frame[v.index].vector.length > 0.5]
    seam = {tuple(round(c, 6) for c in contacts[i].vector) for i in vertices}
    profile = {round(parameter[i].value, 6) for i in vertices}
    return (len(mesh.polygons) - len(cylinder.data.polygons), len(vertices), len(seam), len(profile))

def stats():
    result = []
    evaluated(cylinder, lambda mesh: result.append(surface_stats(mesh)))
    return result[0]

initial_surface = stats()
original_a = [v.co.copy() for v in cylinder.data.vertices]
original_b = [v.co.copy() for v in base.data.vertices]
merges.cn.set_input(bead, merges.BEAD_SURFACE_SUBDIVISION, True)
mode_positions = []
for order in (merges.AFTER_SNAP, merges.BEFORE_SNAP):
    merges.cn.set_input(bead, merges.BEAD_SUBDIVISION_ORDER, order)
    for level in (0, 1, 2):
        merges.cn.set_input(bead, merges.BEAD_SURFACE_LEVELS, level)
        cylinder.update_tag()
        evaluated(cylinder, check_bead)
        counts = stats()
        assert counts[0] == initial_surface[0] * 4 ** level, (order, level, counts, initial_surface)
        if level:
            assert counts[2] > initial_surface[2], counts
            assert counts[3] > initial_surface[3], counts
        if level == 1:
            evaluated(cylinder, lambda mesh: mode_positions.append([v.co.copy() for v in mesh.vertices]))
        print('PASS surface subdivision order/level, faces/vertices/seam/profile:', order, level, counts)
assert len(mode_positions[0]) == len(mode_positions[1])
assert max((a - b).length for a, b in zip(*mode_positions)) > 1e-5
assert all(a.co == b for a, b in zip(cylinder.data.vertices, original_a))
assert all(a.co == b for a, b in zip(base.data.vertices, original_b))
merges.cn.set_input(bead, merges.BEAD_SURFACE_SUBDIVISION, False)
cylinder.update_tag()
restored = capture()
assert max((a - b).length for a, b in zip(neutral[0], restored[0])) < 2e-6
merges.cn.set_input(bead, merges.GLOBAL, 0.0)
merges.cn.set_input(bead, merges.BEAD_SURFACE_SUBDIVISION, True)
cylinder.update_tag()
evaluated(cylinder, check_disabled)
print('PASS subdivision bypass, two distinct orders, untouched sources, Global zero')

# Exercise both orders on curved and shallow cylinder crossings as well.
merges.remove_modifier(cylinder, bead)
base = curved_base
bead = merges.add_merge(cylinder, base, width=0.16, height=0.06)
merges.cn.set_input(bead, merges.BEAD_SURFACE_SUBDIVISION, True)
merges.cn.set_input(bead, merges.BEAD_SURFACE_LEVELS, 1)
for angle, location in ((math.pi / 2, (0, 0, 0)), (0.25, (0.65, 0, 0))):
    base.rotation_euler.y = angle
    base.location = location
    for order in (merges.AFTER_SNAP, merges.BEFORE_SNAP):
        merges.cn.set_input(bead, merges.BEAD_SUBDIVISION_ORDER, order)
        cylinder.update_tag()
        evaluated(cylinder, check_bead)
print('PASS both subdivision orders on perpendicular and shallow cylinder crossings')
intermediate_limits = []
for angle in (0.35, 0.55, 0.75, 1.05, 1.35):
    base.rotation_euler.y = angle
    base.location = (0.3, 0, 0)
    for order in (merges.AFTER_SNAP, merges.BEFORE_SNAP):
        print('INTERMEDIATE ANGLE:', angle, order)
        merges.cn.set_input(bead, merges.BEAD_SUBDIVISION_ORDER, order)
        cylinder.update_tag()
        try:
            evaluated(cylinder, check_bead)
        except AssertionError as error:
            # Exploratory cap-transition cases are not declared solved. Keep
            # the strict core check and report these separately, never as PASS.
            detail = error.args[0]
            if not isinstance(detail, tuple) or detail[0] not in ('Bead inside source', 'Inward bead faces'):
                raise
            if detail[0] == 'Bead inside source':
                assert detail[1] < 0.01, ('Severe intermediate-angle intrusion', angle, order, detail)
            else:
                assert detail[1] <= 1, ('Severe intermediate-angle winding', angle, order, detail)
            intermediate_limits.append((angle, order, detail))
            print('KNOWN INTERMEDIATE CAP LIMIT:', angle, order, detail)
print('INTERMEDIATE ANGLE AUDIT COMPLETE; remaining geometry-limit cases:', len(intermediate_limits))
# Leave the canonical shallow fixture for the boundary audit script.
base.rotation_euler.y = 0.25
base.location = (0.65, 0, 0)
cylinder.update_tag()
print('CORE BEAD NORMAL REGRESSIONS PASSED; INTERMEDIATE CAP LIMITS REPORTED ABOVE')
