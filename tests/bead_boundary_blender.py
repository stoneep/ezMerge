"""Audit bead triangles beyond the vertex/face-center regression checks."""
from pathlib import Path
import runpy

from mathutils import Vector
from mathutils.bvhtree import BVHTree

scope = runpy.run_path(str(Path(__file__).with_name('bead_normals_blender.py')))
bpy, merges = scope['bpy'], scope['merges']
cylinder, base, bead = scope['cylinder'], scope['base'], scope['bead']
evaluated = scope['evaluated']


def audit(mesh):
    mesh.calc_loop_triangles()
    frame = mesh.attributes[merges.BEAD_BISECTOR].data
    parameter = mesh.attributes[merges.BEAD_ARC_T].data
    triangles = [tuple(t.vertices) for t in mesh.loop_triangles
                 if all(frame[i].vector.length > 0.5 for i in t.vertices)]
    positions = [v.co.copy() for v in mesh.vertices]
    print('CONTACT ALIGNMENT:', mesh.attributes[merges.BEAD_CONTACT_ALIGNMENT].data[triangles[0][0]].value)
    tree = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
    self_pairs = [(a, b) for a, b in tree.overlap(tree)
                  if a < b and not set(triangles[a]).intersection(triangles[b])]
    def crosses(a, b):
        # BVH includes touching triangles. Count only proper crossings of a
        # segment through a triangle interior, not an intended shared border.
        def segment_hit(p, q, triangle):
            v0, v1, v2 = (positions[i] for i in triangle)
            edge1, edge2, direction = v1 - v0, v2 - v0, q - p
            h = direction.cross(edge2)
            determinant = edge1.dot(h)
            if abs(determinant) < 1e-12:
                return False
            delta = p - v0
            u = delta.dot(h) / determinant
            cross = delta.cross(edge1)
            v = direction.dot(cross) / determinant
            t = edge2.dot(cross) / determinant
            return 1e-5 < t < 1 - 1e-5 and u > 1e-5 and v > 1e-5 and u + v < 1 - 1e-5
        for first, second in ((triangles[a], triangles[b]), (triangles[b], triangles[a])):
            for i in range(3):
                if segment_hit(positions[first[i]], positions[first[(i + 1) % 3]], second):
                    return True
        return False
    crossings = sum(crosses(a, b) for a, b in self_pairs)
    source_a = BVHTree.FromPolygons([v.co for v in cylinder.data.vertices],
                                    [list(p.vertices) for p in cylinder.data.polygons])
    relative = cylinder.matrix_world.inverted() @ base.matrix_world
    source_b = BVHTree.FromPolygons([relative @ v.co for v in base.data.vertices],
                                    [list(p.vertices) for p in base.data.polygons])
    worst = (0.0, None)
    worst_interior = 0.0
    samples = ((1/3, 1/3, 1/3), (0.5, 0.5, 0), (0.5, 0, 0.5), (0, 0.5, 0.5),
               (0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8))
    degenerate = 0
    for tri in triangles:
        vertices = [positions[i] for i in tri]
        if (vertices[1] - vertices[0]).cross(vertices[2] - vertices[0]).length < 1e-12:
            degenerate += 1
        for weights in samples:
            point = sum((v * w for v, w in zip(vertices, weights)), Vector())
            t = sum(parameter[i].value * w for i, w in zip(tri, weights))
            for source in (source_a, source_b):
                hit, normal, _, _ = source.find_nearest(point)
                depth = -(point - hit).dot(normal)
                if depth > worst[0]:
                    worst = (depth, (tri, t, tuple(point)))
                if 1e-6 < t < 1 - 1e-6:
                    worst_interior = max(worst_interior, depth)
    print('BOUNDARY AUDIT triangles/candidates/crossings/degenerate/interior penetration/worst penetration:',
          len(triangles), len(self_pairs), crossings, degenerate, worst_interior, worst)
    return crossings, degenerate, worst[0]


# v13 must improve the perpendicular cap cases (v12: 10/285/159 proper
# crossings), without worsening the shallow cases. Residual limits remain
# explicit: this is NOT a claim of a generally intersection-free bead.
limits = iter(((3, 0.0013), (12, 0.0015), (60, 0.0013), (0, 0.009), (0, 0.004), (12, 0.003)))
for angle, location in ((scope['math'].pi / 2, (0, 0, 0)), (0.25, (0.65, 0, 0))):
    base.rotation_euler.y = angle
    base.location = location
    for enabled, order in ((False, merges.AFTER_SNAP), (True, merges.AFTER_SNAP), (True, merges.BEFORE_SNAP)):
        merges.cn.set_input(bead, merges.BEAD_SURFACE_SUBDIVISION, enabled)
        merges.cn.set_input(bead, merges.BEAD_SUBDIVISION_ORDER, order)
        cylinder.update_tag()
        print('AUDIT CASE angle/subdivision/order:', angle, enabled, order)
        result = []
        evaluated(cylinder, lambda mesh: result.append(audit(mesh)))
        crossings, degenerate, penetration = result[0]
        max_crossings, max_penetration = next(limits)
        assert crossings <= max_crossings, ('Intersection regression', crossings, max_crossings)
        assert penetration <= max_penetration
        assert degenerate == 0
        if crossings or penetration > 2e-6:
            print('KNOWN LIMIT: sharp-cap/curved-boundary triangle overlap remains')

# A cleanup unit fixture: duplicated A-side and B-side vertices must weld
# within their own border, never across borders. A collapsed face is removed.
data = bpy.data.meshes.new('Cleanup fixture')
triangle = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
data.from_pydata(triangle * 4 + [(2, 0, 0), (2, 0, 0), (2, 0, 0)], [],
                 [(i, i + 1, i + 2) for i in range(0, 15, 3)])
t = data.attributes.new(merges.BEAD_ARC_T, 'FLOAT', 'POINT')
for i, value in enumerate(t.data):
    value.value = 0.0 if i < 6 else 1.0
obj = bpy.data.objects.new('Cleanup fixture', data)
bpy.context.collection.objects.link(obj)
group = bpy.data.node_groups.new('Cleanup fixture', 'GeometryNodeTree')
group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
gin, gout = group.nodes.new('NodeGroupInput'), group.nodes.new('NodeGroupOutput')
attribute = group.nodes.new('GeometryNodeInputNamedAttribute')
attribute.data_type = 'FLOAT'
attribute.inputs['Name'].default_value = merges.BEAD_ARC_T
width = merges._math(group.nodes, group.links, 'ADD', 1.0, 0.0)
cleaned = merges._cleanup_bead_boundary(group.nodes, group.links, gin.outputs['Geometry'],
                                       attribute.outputs['Attribute'], width)
group.links.new(cleaned, gout.inputs['Geometry'])
mod = obj.modifiers.new('Cleanup fixture', 'NODES')
mod.node_group = group

def check_cleanup(mesh):
    assert len(mesh.vertices) == 6 and len(mesh.polygons) == 2, (len(mesh.vertices), len(mesh.polygons))
    values = [round(v.value) for v in mesh.attributes[merges.BEAD_ARC_T].data]
    assert values.count(0) == values.count(1) == 3, values
    assert len(data.vertices) == 15 and len(data.polygons) == 5
    print('PASS cleanup welds duplicates, removes collapsed faces, keeps borders and source separate')

evaluated(obj, check_cleanup)
print('BOUNDARY CLEANUP REGRESSIONS PASSED; KNOWN SHARP-CAP LIMITS REPORTED ABOVE')
