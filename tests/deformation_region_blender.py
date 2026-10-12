"""Source-distance region does not depend on profile or Surface Spread."""
import bpy
import importlib
import math
from pathlib import Path
import sys
import types
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
from boolean_free_probe import bypass_adhesion_boolean
package = types.ModuleType('region_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
merges = importlib.import_module(package.__name__+'.merges')
cn = merges.cn
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,-.15))
base = bpy.context.object
base.scale = (4,4,.3)
bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
# Actual hover gap: A begins .125 above the slab, instead of already touching.
n,rings = 16,16
vertices = [(.4*math.cos(2*math.pi*i/n),
             .4*math.sin(2*math.pi*i/n),j*.125)
            for j in range(rings+1) for i in range(n)]
faces = [(j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i)
         for j in range(rings) for i in range(n)]
faces += [tuple(reversed(range(n))),tuple(rings*n+i for i in range(n))]
mesh = bpy.data.meshes.new('Region source')
mesh.from_pydata(vertices,[],faces)
obj = bpy.data.objects.new('A hovering cylinder',mesh)
bpy.context.collection.objects.link(obj)
obj.location.z = .125
mod = merges.add_merge(obj,base,radius=.2)
for name,value in ((merges.ADHESION_LEVELS,0),(merges.SURFACE_RELAX,0),
                   (merges.BEAD_SYNC_NORMALS,False),(merges.MERGE_RADIUS,.2),
                   (merges.PUSH_STRENGTH,.15)):
    cn.set_input(mod,name,value)
bypass_adhesion_boolean(mod)
original_a = [tuple(v.co) for v in mesh.vertices]
original_b = [tuple(v.co) for v in base.data.vertices]
cases = 0
for influence in (.5,1):
    cn.set_input(mod,merges.INFLUENCE_RADIUS,influence)
    for master in (.5,1):
        cn.set_input(mod,merges.GLOBAL,master)
        radius = influence*master
        expected = [p[2]+.125 < radius for p in vertices]
        for falloff,custom in ((merges.SMOOTH,False),(merges.LINEAR,False),
                               (merges.SHARP,False),(merges.SPHERE,False),(merges.SMOOTH,True)):
            cn.set_input(mod,merges.FALLOFF,falloff)
            cn.set_input(mod,merges.BEAD_CUSTOM_CURVE,custom)
            for spread in (0,.15):
                cn.set_input(mod,merges.SURFACE_SPREAD,spread)
                obj.update_tag()
                bpy.context.view_layer.update()
                evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                result = evaluated.to_mesh()
                try:
                    assert [tuple(p.vertices) for p in result.polygons] == faces
                    region = [item.value for item in result.attributes[merges.ADHESION_REGION].data]
                    distance = [item.value for item in result.attributes[merges.ADHESION_DISTANCE].data]
                    normalized = [item.value for item in result.attributes[merges.ADHESION_REGION_DISTANCE].data]
                    weight = [item.value for item in result.attributes[merges.ADHESION_WEIGHT].data]
                    assert [v>.5 for v in region] == expected
                    for i,v in enumerate(result.vertices):
                        assert abs(distance[i]-(vertices[i][2]+.125)) < 1e-6
                        assert abs(normalized[i]-min(1,distance[i]/radius)) < 1e-6
                        if not expected[i]:
                            assert weight[i] == 0
                            assert (v.co-Vector(vertices[i])).length < 1e-7
                    cases += 1
                finally:
                    evaluated.to_mesh_clear()
        print('PASS boundary radius',radius,'eligible points',sum(expected),
              'all presets / Spread preserve source-distance region',flush=True)
cn.set_input(mod,merges.GLOBAL,1)
cn.set_input(mod,merges.INFLUENCE_RADIUS,.5)
cn.set_input(mod,merges.SURFACE_CLEARANCE,0)
for requested_core in (.125,.25,1):
    cn.set_input(mod,merges.CONTACT_RADIUS,requested_core)
    core_radius = min(requested_core,.5*.95)
    for falloff,custom in ((merges.SMOOTH,False),(merges.LINEAR,False),
                           (merges.SHARP,False),(merges.SPHERE,False),(merges.SMOOTH,True)):
        cn.set_input(mod,merges.FALLOFF,falloff)
        cn.set_input(mod,merges.BEAD_CUSTOM_CURVE,custom)
        for spread in (0,.15):
            cn.set_input(mod,merges.SURFACE_SPREAD,spread)
            obj.update_tag()
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            result = evaluated.to_mesh()
            try:
                masks = [d.value for d in result.attributes[merges.ADHESION_CONTACT_REGION].data]
                for i,v in enumerate(result.vertices):
                    d = vertices[i][2]+.125
                    expected_core = d <= core_radius and d < .5
                    assert (masks[i]>.5) == expected_core
                    if expected_core:
                        assert abs(v.co.z+.125) < 1e-6, 'Exact core lifted off B'
                    if d >= .5:
                        assert (v.co-Vector(vertices[i])).length < 1e-7
                cases += 1
            finally:
                evaluated.to_mesh_clear()
    print('PASS contact radius requested/effective',requested_core,core_radius,
          'exact core / profile-independent pin / untouched outside',flush=True)

# The final pin must survive both internal subdivision orders and live relax.
cn.set_input(mod,merges.CONTACT_RADIUS,.25)
cn.set_input(mod,merges.BEAD_CUSTOM_CURVE,False)
cn.set_input(mod,merges.FALLOFF,merges.SMOOTH)
cn.set_input(mod,merges.SURFACE_SPREAD,.15)
cn.set_input(mod,merges.SURFACE_RELAX,.35)
cn.set_input(mod,merges.SURFACE_RELAX_PASSES,2)
for level in (1,2):
    cn.set_input(mod,merges.ADHESION_LEVELS,level)
    for order in (merges.BEFORE_SNAP,merges.AFTER_SNAP):
        cn.set_input(mod,merges.BEAD_SUBDIVISION_ORDER,order)
        obj.update_tag()
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        result = evaluated.to_mesh()
        try:
            distances = result.attributes[merges.ADHESION_DISTANCE].data
            sources = result.attributes[merges.ADHESION_SOURCE_POSITION].data
            for v,d,source in zip(result.vertices,distances,sources):
                if d.value <= .25+1e-7:
                    assert abs(v.co.z+.125) < 1e-6, 'Refinement/relax detached core'
                if d.value >= .5:
                    assert (v.co-source.vector).length < 1e-7
        finally:
            evaluated.to_mesh_clear()
print('PASS both subdivision orders / two refinement levels / live relax preserve core',flush=True)
cn.set_input(mod,merges.ADHESION_LEVELS,0)
cn.set_input(mod,merges.SURFACE_RELAX,0)

# The core also works alone: no Merge/Push/Spread required to activate it.
for name in (merges.MERGE_RADIUS,merges.PUSH_STRENGTH,merges.SURFACE_SPREAD):
    cn.set_input(mod,name,0)
cn.set_input(mod,merges.CONTACT_RADIUS,.125)
def positions():
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    try:
        return [v.co.copy() for v in result.vertices]
    finally:
        evaluated.to_mesh_clear()
initial = positions()
assert max(abs(p.z+.125) for p in initial[:n]) < 1e-6
base.location.z += .03125
moved = positions()
assert max((p-q).length for p,q in zip(initial,moved)) > .01
base.location.z -= .03125
assert max((p-q).length for p,q in zip(initial,positions())) < 1e-7
cn.set_input(mod,merges.GLOBAL,0)
assert max((p-Vector(q)).length for p,q in zip(positions(),vertices)) < 1e-7
cn.set_input(mod,merges.GLOBAL,1)
cn.set_input(mod,merges.BASE,None)
assert max((p-Vector(q)).length for p,q in zip(positions(),vertices)) < 1e-7
print('PASS contact-only / live motion / restoration / Global zero / missing B',flush=True)
assert [tuple(v.co) for v in mesh.vertices] == original_a
assert [tuple(v.co) for v in base.data.vertices] == original_b
print('ALL DEFORMATION REGION TESTS PASSED',cases,bpy.app.version_string,flush=True)
