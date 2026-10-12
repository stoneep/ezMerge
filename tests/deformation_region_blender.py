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
assert [tuple(v.co) for v in mesh.vertices] == original_a
assert [tuple(v.co) for v in base.data.vertices] == original_b
print('ALL DEFORMATION REGION TESTS PASSED',cases,bpy.app.version_string,flush=True)
