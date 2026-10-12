"""Move A (not B) vertically: live distance, pin range, release, restoration."""
import bpy
import importlib
import math
from pathlib import Path
import sys
import types

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
from boolean_free_probe import bypass_adhesion_boolean
package = types.ModuleType('vertical_motion_test')
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
n,rings = 24,8
vertices = [(.3*math.cos(2*math.pi*i/n),.3*math.sin(2*math.pi*i/n),j*.2)
            for j in range(rings+1) for i in range(n)]
faces = [(j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i)
         for j in range(rings) for i in range(n)]
faces += [tuple(reversed(range(n))),tuple(rings*n+i for i in range(n))]
mesh = bpy.data.meshes.new('Motion source')
mesh.from_pydata(vertices,[],faces)
obj = bpy.data.objects.new('Moving A',mesh)
bpy.context.collection.objects.link(obj)
mod = merges.add_merge(obj,base,radius=.2)
for name,value in ((merges.INFLUENCE_RADIUS,.4),(merges.MERGE_RADIUS,.2),
                   (merges.PUSH_STRENGTH,.1),(merges.SURFACE_SPREAD,.1),
                   (merges.SURFACE_CLEARANCE,0),(merges.SURFACE_RELAX,0),
                   (merges.BEAD_SYNC_NORMALS,False)):
    cn.set_input(mod,name,value)
bypass_adhesion_boolean(mod)
original = [tuple(v.co) for v in mesh.vertices]
cases = 0
for level in (0,1):
    cn.set_input(mod,merges.ADHESION_LEVELS,level)
    for order in (merges.BEFORE_SNAP,merges.AFTER_SNAP):
        cn.set_input(mod,merges.BEAD_SUBDIVISION_ORDER,order)
        for core in (0,.05):
            cn.set_input(mod,merges.CONTACT_RADIUS,core)
            saved = {}
            for gap in (.6,.4,.2,.1,.05,.025,0,.025,.05,.1,.2,.4,.6):
                obj.location.z = gap
                # Normal dependency-graph transform update, no manual mesh or
                # node-group retagging: catches stale relative Object Info.
                bpy.context.view_layer.update()
                evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                result = evaluated.to_mesh()
                try:
                    points = [v.co.copy() for v in result.vertices]
                    source = result.attributes[merges.ADHESION_SOURCE_POSITION].data
                    distance = result.attributes[merges.ADHESION_DISTANCE].data
                    error = max(abs(d.value-(s.vector.z+gap)) for s,d in zip(source,distance))
                    assert error < 2e-6, 'A transform did not refresh target distance'
                    outside = [(p-s.vector).length for p,s,d in zip(points,source,distance) if d.value>=.4+1e-6]
                    assert max(outside,default=0)<2e-6
                    if gap >= .4:
                        assert max((p-s.vector).length for p,s in zip(points,source)) < 2e-6, 'Still stuck outside influence'
                    if core and gap<=core:
                        assert min(p.z+gap for p in points)<2e-6
                    if gap in saved:
                        assert max((p-q).length for p,q in zip(points,saved[gap])) < 2e-6, 'History-dependent sticking'
                    saved[gap] = points
                    if level==1 and order==merges.BEFORE_SNAP:
                        print('MOVE A core',core,'gap',gap,'points',len(points),
                              'lowest world z',min(p.z+gap for p in points),
                              'distance refresh error',error,flush=True)
                    cases += 1
                finally:
                    evaluated.to_mesh_clear()
            print('PASS vertical release / distance refresh / return path',level,order,core,flush=True)
assert [tuple(v.co) for v in mesh.vertices] == original
print('ALL VERTICAL MOTION TESTS PASSED',cases,bpy.app.version_string,flush=True)
