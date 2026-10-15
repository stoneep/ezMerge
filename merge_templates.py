"""Per-source target picker and explicit template-pair dispatch.

Sphere/Sphere dispatches to the captured measured-profile experiment.
Other registered pairs still use the existing Bead preview, not Ring repair.
"""
import bpy
from bpy.props import EnumProperty, PointerProperty
from . import merges, merge_sphere_experiment

TYPES = (('SPHERE', 'Sphere', 'MESH_UVSPHERE'),
         ('BOX', 'Box', 'MESH_CUBE'),
         ('CYLINDER', 'Cylinder', 'MESH_CYLINDER'))
TYPE_ITEMS = [('NONE', 'Choose a type', 'No template selected')] + [
    (key, label, 'Manual template choice, not automatic mesh recognition')
    for key, label, icon in TYPES]
LABELS = {key: label for key, label, icon in TYPES}
PAIR_KEY = 'ezhs_template_pair'
BACKEND_KEY = 'ezhs_template_backend'
PRESETS = {
    (source, target): dict(id=f'{source}_TO_{target}',
                          label=f'{source_label} -> {target_label}',
                          backend='BEAD_PREVIEW')
    for source, source_label, _ in TYPES
    for target, target_label, _ in TYPES
}
PRESETS[('SPHERE', 'SPHERE')]['backend'] = 'MEASURED_SPHERE_UNION'


def target_poll(source, candidate):
    return candidate.type == 'MESH' and candidate != source


def target_changed(source, context):
    # A new target must be explicitly classified; don't keep the old target's
    # declared template type when an eyedropper changes the reference.
    source.ezhs_merge_target_type = 'NONE'


def preset_for(source):
    return PRESETS.get((source.ezhs_merge_source_type, source.ezhs_merge_target_type))


def _merge_chain_reaches(start, wanted):
    pending, seen = [start], set()
    while pending:
        obj = pending.pop()
        if obj == wanted:
            return True
        if obj.as_pointer() in seen:
            continue
        seen.add(obj.as_pointer())
        pending.extend(base for mod in merges.merge_modifiers(obj)
                       if (base := merges.base_of(mod)) is not None)
    return False


def validation_error(context, source):
    if context.mode != 'OBJECT':
        return 'Switch to Object Mode'
    if source is None or source.type != 'MESH':
        return 'Select a source Mesh'
    if source.library is not None:
        return 'The source must be a local editable Mesh'
    target = source.ezhs_merge_target
    if target is None:
        return 'Choose the target Mesh with the eyedropper'
    if target == source or target.type != 'MESH':
        return 'Source and target must be different Mesh objects'
    if context.scene.objects.get(target.name) != target:
        return 'The target must be in the current scene'
    if preset_for(source) is None:
        return 'Choose both source and target template types'
    if any(merges.base_of(mod) == target for mod in merges.merge_modifiers(source)):
        return 'Already merged to this target; use the controls below'
    if _merge_chain_reaches(target, source):
        return 'This would create a circular merge dependency'
    return None


class EZHS_OT_merge_template_type(bpy.types.Operator):
    """Declare a template type; does not replace or recognize the mesh"""
    bl_idname = 'ezhs.merge_template_type'
    bl_label = 'Choose Merge Template Type'
    bl_options = {'REGISTER', 'UNDO'}

    role: EnumProperty(items=[('SOURCE', 'Source', ''), ('TARGET', 'Target', '')])
    value: EnumProperty(items=[(key, label, '') for key, label, icon in TYPES])

    @classmethod
    def poll(cls, context):
        obj = context.object
        return context.mode == 'OBJECT' and obj is not None and obj.type == 'MESH' and obj.library is None

    def execute(self, context):
        name = 'ezhs_merge_source_type' if self.role == 'SOURCE' else 'ezhs_merge_target_type'
        setattr(context.object, name, self.value)
        return {'FINISHED'}


class EZHS_OT_merge_template_execute(bpy.types.Operator):
    """Execute the declared pair using its registered experimental backend"""
    bl_idname = 'ezhs.merge_template_execute'
    bl_label = 'Merge Template'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return validation_error(context, context.object) is None

    def execute(self, context):
        source = context.object
        error = validation_error(context, source)
        if error:
            self.report({'WARNING'}, error)
            return {'CANCELLED'}
        if not merges.available():
            self.report({'ERROR'}, 'Merging needs Blender 4.2 or later')
            return {'CANCELLED'}
        target = source.ezhs_merge_target
        preset = preset_for(source)
        if preset['backend'] == 'MEASURED_SPHERE_UNION':
            try:
                merge_sphere_experiment.create(context, source, target)
            except Exception as error:
                self.report({'WARNING'}, str(error))
                return {'CANCELLED'}
            self.report({'INFO'}, 'Sphere experiment created; originals preserved and hidden')
            return {'FINISHED'}
        # Don't destructively evaluate/apply either input. Empty procedural
        # datablocks are allowed when their evaluated meshes contain faces.
        for obj in (source, target):
            evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                has_faces = mesh is not None and bool(mesh.polygons)
            finally:
                evaluated.to_mesh_clear()
            if not has_faces:
                self.report({'ERROR'}, f'{obj.name} has no evaluated mesh faces')
                return {'CANCELLED'}
        existing = {mod.as_pointer() for mod in source.modifiers}
        try:
            # Do not invent unvalidated per-type numeric parameters. Stage 1
            # deliberately keeps established Bead defaults for every pair.
            mod = merges.add_merge(source, target, method=merges.BEAD)
            if mod.node_group is None:
                raise ValueError('The preview backend did not create a node group')
            # add_merge gives each Bead a private group. Group metadata survives
            # save/reload; arbitrary modifier ID properties do not in 5.1.
            mod.node_group[PAIR_KEY] = preset['id']
            mod.node_group[BACKEND_KEY] = preset['backend']
            source.update_tag()
        except Exception as error:
            for created in list(source.modifiers):
                if created.as_pointer() not in existing:
                    source.modifiers.remove(created)
            self.report({'ERROR'}, f'Merge preview failed: {error}')
            return {'CANCELLED'}
        self.report({'WARNING'}, f"{preset['label']}: Bead preview created, not a Ring recapture or welded mesh")
        return {'FINISHED'}


def draw_creation_panel(layout, context, source):
    if source is None or source.type != 'MESH':
        return
    if merge_sphere_experiment.draw_panel(layout, source):
        return
    box = layout.box()
    box.label(text='Merge Template', icon='SNAP_FACE')
    if len(context.selected_objects) == 2:
        box.operator('ezhs.sphere_pair_experiment', text='Merge Two Spheres (Experimental)', icon='MESH_UVSPHERE')
    box.label(text=f'Source: {source.name}', icon='MESH_DATA')
    editable = box.column()
    editable.enabled = context.mode == 'OBJECT' and source.library is None
    for role, prop, title in [('SOURCE', 'ezhs_merge_source_type', 'Source Type'),
                              ('TARGET', 'ezhs_merge_target_type', 'Target Type')]:
        if role == 'TARGET':
            # Object PointerProperty supplies Blender's native eyedropper.
            editable.prop(source, 'ezhs_merge_target', text='Target')
        editable.label(text=title)
        row = editable.row(align=True)
        for value, label, icon in TYPES:
            op = row.operator('ezhs.merge_template_type', text=label, icon=icon,
                              depress=getattr(source, prop) == value)
            op.role, op.value = role, value
    preset = preset_for(source)
    if preset:
        box.label(text=preset['label'])
    if preset and preset['backend'] == 'MEASURED_SPHERE_UNION':
        box.label(text='Sphere experiment: captured Boolean + GN', icon='INFO')
    elif preset and preset['id'] == 'SPHERE_TO_BOX':
        box.label(text='Preview: existing Bead backend', icon='INFO')
        box.label(text='Ring recapture not integrated yet')
    else:
        box.label(text='Preview: existing Bead backend', icon='INFO')
        box.label(text='Template reconstruction not integrated yet')
    error = validation_error(context, source)
    if error:
        box.label(text=error, icon='INFO')
    row = box.row()
    row.enabled = error is None
    row.operator('ezhs.merge_template_execute', text='Merge Spheres (Experimental)' if preset and preset['backend'] == 'MEASURED_SPHERE_UNION' else 'Merge (Preview)', icon='SNAP_FACE')


classes = (EZHS_OT_merge_template_type, EZHS_OT_merge_template_execute)


def register():
    bpy.types.Object.ezhs_merge_target = PointerProperty(
        name='Target Mesh', type=bpy.types.Object, poll=target_poll, update=target_changed,
        description='Pick another Mesh; active object stays the source')
    bpy.types.Object.ezhs_merge_source_type = EnumProperty(
        name='Source Template Type', items=TYPE_ITEMS, default='NONE')
    bpy.types.Object.ezhs_merge_target_type = EnumProperty(
        name='Target Template Type', items=TYPE_ITEMS, default='NONE')
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for name in ('ezhs_merge_target', 'ezhs_merge_source_type', 'ezhs_merge_target_type'):
        if hasattr(bpy.types.Object, name):
            delattr(bpy.types.Object, name)
