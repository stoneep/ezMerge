"""Explicit, non-destructive two-sphere Boolean + measured-profile prototype.

Not arbitrary mesh recognition or live adhesion. Original evaluated surfaces
are captured once; controls only reshape a radial band on the captured union.
"""
import bpy
import bmesh
from mathutils import Vector
from . import merge_sphere_profile, shapes as cn

TAG = 'ezhs_sphere_experiment'


def modifier(obj):
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == 'NODES' and
                 m.node_group and m.node_group.get(TAG)), None)


def sphere_data(context, obj):
    if obj.type != 'MESH' or obj.library:
        raise ValueError('Choose two local Mesh spheres')
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=context.evaluated_depsgraph_get())
    try:
        points = [obj.matrix_world @ v.co for v in mesh.vertices]
        if len(points) < 16 or not mesh.polygons:
            raise ValueError('Sphere needs at least 16 vertices and closed faces')
        # World bounds identify primitive sphere centers, not names or UI types.
        center = Vector(tuple((min(p[i] for p in points)+max(p[i] for p in points))/2 for i in range(3)))
        distances = [(p-center).length for p in points]
        radius = sum(distances)/len(distances)
        if radius <= 1e-8 or max(abs(r-radius) for r in distances) > radius*.035:
            raise ValueError('Only near-spherical meshes supported (3.5% radial tolerance)')
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            if any(not e.is_manifold for e in bm.edges):
                raise ValueError('Input spheres must be closed manifold meshes')
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(mesh)
        finally:
            bm.free()
        mesh.transform(obj.matrix_world)
        return mesh, center, radius
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise


def create(context, first, second):
    if first == second:
        raise ValueError('Choose two different spheres')
    old_selection = list(context.selected_objects)
    old_active = context.view_layer.objects.active
    # A failed operation must leave neither helper objects nor orphaned data.
    inventories = {kind: set(getattr(bpy.data, kind).keys()) for kind in
                   ('objects', 'meshes', 'node_groups', 'materials')}
    try:
        inputs = [(obj, *sphere_data(context, obj)) for obj in (first, second)]
        inputs.sort(key=lambda entry: entry[3], reverse=True)
        (_, mesh_a, ca, ra), (_, mesh_b, cb, rb) = inputs
        distance = (cb-ca).length
        margin = max(ra, rb)*1e-4
        if not abs(ra-rb)+margin < distance < ra+rb-margin:
            raise ValueError('Spheres must overlap; tangent, separated or contained pairs are unsupported')
        axis = (cb-ca).normalized()
        frame = axis.to_track_quat('Z', 'Y').to_matrix().to_4x4()
        frame.translation = ca
        inverse = frame.inverted()
        for mesh in (mesh_a, mesh_b):
            mesh.transform(inverse)
        result = bpy.data.objects.new('Sphere Pair - Experimental', mesh_a)
        cutter = bpy.data.objects.new('Sphere Pair temporary operand', mesh_b)
        for obj in (result, cutter):
            context.collection.objects.link(obj)
            obj.matrix_world = frame
        for obj in context.selected_objects:
            obj.select_set(False)
        result.select_set(True)
        context.view_layer.objects.active = result
        boolean = result.modifiers.new('Captured actual sphere union', 'BOOLEAN')
        boolean.operation = 'UNION'
        boolean.solver = 'EXACT'
        boolean.object = cutter
        bpy.ops.object.modifier_apply(modifier=boolean.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
        if mesh_b.users == 0:
            bpy.data.meshes.remove(mesh_b)
        bm = bmesh.new()
        try:
            bm.from_mesh(result.data)
            if not bm.faces or any(not e.is_manifold for e in bm.edges):
                raise ValueError('Boolean did not produce a closed manifold result')
        finally:
            bm.free()
        center = (ra*ra-rb*rb+distance*distance)/(2*distance)
        # Support spans only the junction, never either exterior pole.
        lo = max(-ra+ra*.02, center-rb*.85)
        hi = min(distance+rb-rb*.02, center+rb*.85)
        lower = min(rb*.6, (center-lo)*.8)
        upper = min(rb*.5, (hi-center)*.8)
        mod, sockets, metadata, lookup = merge_sphere_profile.install(result, center, lo, hi, lower, upper)
        mod.node_group[TAG] = True
        result[TAG] = True
        result['sphere_source_a'] = first
        result['sphere_source_b'] = second
        result['sphere_profile_lookup'] = lookup
        for key, obj in (('a', first), ('b', second)):
            result['sphere_hide_'+key] = obj.hide_get()
            result['sphere_render_'+key] = obj.hide_render
        for face in result.data.polygons:
            face.use_smooth = True
        # Commit visibility only after every fallible geometry step succeeds.
        for obj in (first, second):
            obj.hide_set(True)
            obj.hide_render = True
        return result
    except Exception:
        for kind in ('objects', 'node_groups', 'meshes', 'materials'):
            collection = getattr(bpy.data, kind)
            for data in list(collection):
                if data.name not in inventories[kind] and (kind == 'objects' or data.users == 0):
                    collection.remove(data, do_unlink=True)
        for obj in old_selection:
            obj.select_set(True)
        context.view_layer.objects.active = old_active
        raise


class EZHS_OT_sphere_pair_experiment(bpy.types.Operator):
    bl_idname = 'ezhs.sphere_pair_experiment'
    bl_label = 'Merge Two Spheres (Experimental)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and len(context.selected_objects) == 2 and all(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        try:
            create(context, *context.selected_objects)
        except Exception as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Experimental sphere union created; originals preserved and hidden')
        return {'FINISHED'}


class EZHS_OT_sphere_pair_restore(bpy.types.Operator):
    bl_idname = 'ezhs.sphere_pair_restore'
    bl_label = 'Restore Two Original Spheres'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and modifier(context.object) is not None

    def execute(self, context):
        result = context.object
        sources = [result.get('sphere_source_'+key) for key in ('a', 'b')]
        if any(obj is None for obj in sources):
            self.report({'WARNING'}, 'Original missing; keeping result')
            return {'CANCELLED'}
        for key, obj in zip(('a', 'b'), sources):
            obj.hide_set(bool(result.get('sphere_hide_'+key)))
            obj.hide_render = bool(result.get('sphere_render_'+key))
            obj.select_set(True)
        lookup = result.get('sphere_profile_lookup')
        group, mesh = modifier(result).node_group, result.data
        bpy.data.objects.remove(result, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        if group.users == 0:
            bpy.data.node_groups.remove(group)
        if lookup and lookup.users <= 1:
            mesh = lookup.data
            bpy.data.objects.remove(lookup, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        context.view_layer.objects.active = sources[0]
        return {'FINISHED'}


def draw_panel(layout, obj):
    mod = modifier(obj)
    if mod is None:
        return False
    box = layout.box()
    box.label(text='Sphere Pair / Experimental', icon='INFO')
    box.label(text='Captured union; not live adhesion')
    for name in ('Strength', 'Region Center Z', 'Lower Reach', 'Upper Reach', 'Edge Fade', 'Show Region'):
        cn.draw_input(box, mod, name)
    box.operator('ezhs.sphere_pair_restore', icon='LOOP_BACK')
    return True


def register():
    for cls in (EZHS_OT_sphere_pair_experiment, EZHS_OT_sphere_pair_restore):
        bpy.utils.register_class(cls)


def unregister():
    for cls in (EZHS_OT_sphere_pair_restore, EZHS_OT_sphere_pair_experiment):
        bpy.utils.unregister_class(cls)
