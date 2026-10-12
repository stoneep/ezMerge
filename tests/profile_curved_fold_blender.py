"""Boolean-free adhesion diagnostics on convex and concave curved targets."""
import importlib
import math
from pathlib import Path
import sys
import types
import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
from boolean_free_probe import bypass_adhesion_boolean
from profile_tangent_probe import build_tangent_profile
from profile_fold_probe import diagnose_mesh,strict_mesh_crossings
package = types.ModuleType('curved_fold_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module(package.__name__+'.merges')
builder = importlib.import_module(package.__name__+'.merge_adhesion')
cn = merges.cn

# A crossing pair and a disjoint pair validate the narrow phase on each runtime.
synthetic = [Vector(p) for p in ((-1,-1,0),(1,-1,0),(0,1,0),
                                (0,-.5,-1),(0,-.5,1),(.5,.5,0))]
assert strict_mesh_crossings(synthetic,[(0,1,2),(3,4,5)])[0]
assert not strict_mesh_crossings(synthetic,[(0,1,2)])[0]

def capture(obj):
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        return ([v.co.copy() for v in mesh.vertices],
                [tuple(t.vertices) for t in mesh.loop_triangles],
                [tuple(p.vertices) for p in mesh.polygons])
    finally:
        evaluated.to_mesh_clear()

for inside in (False,True):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48,ring_count=24,radius=2)
    base = bpy.context.object
    if inside:
        # Inverted sphere is a local inner-wall fixture, not a closed solid
        # volume classifier or a full mug with wall thickness.
        bpy.context.view_layer.objects.active = base
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.mode_set(mode='OBJECT')
    n,rings = 32,12
    lo,hi = (0.65,1.85) if inside else (1.85,3.05)
    vertices = [(.35*math.cos(2*math.pi*i/n),.35*math.sin(2*math.pi*i/n),lo+(hi-lo)*j/rings)
                for j in range(rings+1) for i in range(n)]
    faces = [(j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i)
             for j in range(rings) for i in range(n)]
    faces += [tuple(reversed(range(n))),tuple(rings*n+i for i in range(n))]
    mesh = bpy.data.meshes.new('Curved adhesion source')
    mesh.from_pydata(vertices,[],faces)
    mesh.update()
    obj = bpy.data.objects.new('A',mesh)
    bpy.context.collection.objects.link(obj)
    original_a = [tuple(v.co) for v in mesh.vertices]
    original_b = [tuple(v.co) for v in base.data.vertices]
    group = bpy.data.node_groups.new('Curved profile diagnostic','GeometryNodeTree')
    build_tangent_profile(builder,group,direct_source_normals=True)
    mod = obj.modifiers.new('Adhesion','NODES')
    mod.node_group = group
    for name,value in ((merges.BASE,base),(merges.INFLUENCE_RADIUS,.4),(merges.MERGE_RADIUS,.2),
                       (merges.PUSH_STRENGTH,.15),(merges.SURFACE_RELAX,0),
                       (merges.BEAD_SYNC_NORMALS,False),(merges.ADHESION_LEVELS,0)):
        cn.set_input(mod,name,value)
    bypass_adhesion_boolean(mod)
    subdivision = obj.modifiers.new('After-snap Catmull','SUBSURF')
    subdivision.levels = subdivision.render_levels = 1
    cn.set_input(mod,merges.GLOBAL,0)
    reference,_,topology = capture(obj)
    cn.set_input(mod,merges.GLOBAL,1)
    for spread in (0,.15):
        cn.set_input(mod,merges.SURFACE_SPREAD,spread)
        points,triangles,current_topology = capture(obj)
        assert current_topology == topology
        assert all(math.isfinite(c) for p in points for c in p)
        assert max((p-q).length for p,q in zip(points,reference)) > .01
        print('CASE','inner-wall' if inside else 'outer-surface','spread',spread,flush=True)
        diagnose_mesh(reference,points,triangles)
        base.location.x = .025
        moved = capture(obj)[0]
        assert max((p-q).length for p,q in zip(points,moved)) > 1e-5
        base.location.x = 0
        restored = capture(obj)[0]
        assert max((p-q).length for p,q in zip(points,restored)) < 1e-6
    assert [tuple(v.co) for v in mesh.vertices] == original_a
    assert [tuple(v.co) for v in base.data.vertices] == original_b
    print('PASS finite / connectivity / live motion / restoration / original meshes preserved',flush=True)
print('ALL CURVED FOLD DIAGNOSTICS PASSED',bpy.app.version_string,flush=True)
