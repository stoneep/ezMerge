"""Experimental capture -> Bead adjustment -> save-base lifecycle.

Captures the evaluated stack, NOT an automatic mesh unfolding/repair solver.
Original object and modifier settings are retained. Push/Spread and topology
controls are deliberately absent from the adjustment interface.
"""
import bpy
from bpy.props import IntProperty, StringProperty
from . import merge_adhesion, merges
from . import shapes as cn

TAG = 'ezhs_recapture'
SOURCE = 'ezhs_recapture_source'
TARGET = 'ezhs_recapture_target'
SOURCE_HIDE = 'ezhs_recapture_source_hidden'
SOURCE_RENDER = 'ezhs_recapture_source_render_hidden'
MASK = 'ezhs_recapture_editable'
REST = 'ezhs_recapture_rest'
LIMIT = 400000
SAFE = (merges.BASE, merges.GLOBAL, merges.INFLUENCE_RADIUS, merges.MERGE_RADIUS,
        merges.FALLOFF, merges.BEAD_CUSTOM_CURVE, merges.BEAD_SYNC_NORMALS)


def recapture_modifier(obj):
    if obj is None or obj.type != 'MESH':
        return None
    return next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                 and TAG in m.node_group), None)


def primary(obj, name=''):
    if obj is None or obj.type != 'MESH':
        return None
    return next((m for m in merges.merge_modifiers(obj)
                 if merges.is_surface_adhesion(m) and (not name or m.name == name)), None)


def _mask(mesh):
    existing = mesh.attributes.get(MASK)
    if existing:
        mesh.attributes.remove(existing)
    source = mesh.attributes.get('collar_selection') or mesh.attributes.get(merges.ADHESION_WEIGHT)
    if source is None or source.domain != 'POINT' or source.data_type not in {'FLOAT', 'BOOLEAN'}:
        raise ValueError('No captured contact mask. Evaluate a surface-adhesion Bead first.')
    values = [bool(item.value > 1e-8) for item in source.data]
    if not any(values):
        raise ValueError('No active contact region to capture.')
    target = mesh.attributes.new(MASK, 'BOOLEAN', 'POINT')
    for item, value in zip(target.data, values):
        item.value = value


def _evaluated_mesh(context, obj):
    context.view_layer.update()
    deps = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(deps)
    if len(evaluated.data.vertices) > LIMIT:
        raise ValueError('Experimental Recapture limit: 400,000 vertices. Lower adhesion levels first.')
    mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=deps)
    if not mesh.polygons or not mesh.vertices:
        bpy.data.meshes.remove(mesh)
        raise ValueError('Evaluated mesh is empty.')
    return mesh


def _new_group():
    inner = bpy.data.node_groups.new('EZ Recapture Bead calculations', 'GeometryNodeTree')
    inner.is_modifier = True
    try:
        merge_adhesion._build_adhesion(inner, recaptured=True)
        reachable = set()
        stack = [n for n in inner.nodes if n.bl_idname == 'NodeGroupOutput']
        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            stack.extend(link.from_node for socket in node.inputs for link in socket.links)
        for node in list(inner.nodes):
            if node not in reachable:
                inner.nodes.remove(node)
        group = bpy.data.node_groups.new('EZ Recapture adjustments', 'GeometryNodeTree')
        group.is_modifier = True
        group[TAG] = 1
        group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        for name in SAFE:
            source = inner.interface.items_tree[name]
            socket = group.interface.new_socket(name=name, in_out='INPUT', socket_type=source.socket_type)
            for prop in ('default_value', 'min_value', 'max_value', 'description'):
                if hasattr(source, prop) and hasattr(socket, prop):
                    setattr(socket, prop, getattr(source, prop))
            if name == merges.MERGE_RADIUS:
                socket.default_value = 0.0
                socket.description = 'Additional normal Pull from the captured baseline; not a capture radius'
            elif name == merges.BEAD_SYNC_NORMALS:
                socket.default_value = False
        nodes, links = group.nodes, group.links
        gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
        store = nodes.new('GeometryNodeStoreNamedAttribute')
        store.domain, store.data_type = 'POINT', 'FLOAT_VECTOR'
        store.inputs['Name'].default_value = REST
        links.new(gin.outputs['Geometry'], store.inputs['Geometry'])
        links.new(nodes.new('GeometryNodeInputPosition').outputs[0], store.inputs['Value'])
        bead = nodes.new('GeometryNodeGroup')
        bead.name = 'Captured Bead'
        bead.node_tree = inner
        links.new(store.outputs['Geometry'], bead.inputs['Geometry'])
        for name in SAFE:
            links.new(gin.outputs[name], bead.inputs[name])
        # Unsafe controls are constants, not merely disabled UI widgets.
        for name in (merges.CONTACT_RADIUS, merges.PUSH_STRENGTH, merges.SURFACE_SPREAD,
                     merges.SURFACE_CLEARANCE, merges.SURFACE_RELAX, merges.SURFACE_RELAX_PASSES,
                     merges.ADHESION_LEVELS):
            bead.inputs[name].default_value = 0
        bead.inputs[merges.ADHESION_LOCAL_SUBDIVISION].default_value = False
        bead.inputs[merges.BEAD_SUBDIVISION_ORDER].default_value = merges.BEFORE_SNAP
        mask = nodes.new('GeometryNodeInputNamedAttribute')
        mask.data_type = 'BOOLEAN'
        mask.inputs['Name'].default_value = MASK
        inverse = nodes.new('FunctionNodeBooleanMath')
        inverse.operation = 'NOT'
        links.new(mask.outputs['Attribute'], inverse.inputs[0])
        rest = nodes.new('GeometryNodeInputNamedAttribute')
        rest.data_type = 'FLOAT_VECTOR'
        rest.inputs['Name'].default_value = REST
        protect = nodes.new('GeometryNodeSetPosition')
        links.new(bead.outputs['Geometry'], protect.inputs['Geometry'])
        links.new(inverse.outputs[0], protect.inputs['Selection'])
        links.new(rest.outputs['Attribute'], protect.inputs['Position'])
        links.new(protect.outputs['Geometry'], gout.inputs['Geometry'])
        return group
    except Exception:
        if 'group' in locals():
            bpy.data.node_groups.remove(group)
        if inner.users == 0:
            bpy.data.node_groups.remove(inner)
        raise


def _neutral(mod):
    cn.set_input(mod, merges.MERGE_RADIUS, 0.0)
    cn.set_input(mod, merges.BEAD_SYNC_NORMALS, False)


def _activate(context, obj):
    for other in context.selected_objects:
        other.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _replace_mesh(obj, mesh):
    old = obj.data
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def capture(context, obj, modifier_name=''):
    current = recapture_modifier(obj)
    source = obj.get(SOURCE) if current else obj
    if source is None or source.type != 'MESH':
        raise ValueError('Original source is missing.')
    if not current and any(other.get(SOURCE) == source and recapture_modifier(other)
                           for other in bpy.data.objects if other != source):
        raise ValueError('This original already has a Recapture result. Select that result to refresh or restore it.')
    if current and len(obj.modifiers) != 1:
        raise ValueError('Remove additional modifiers before recapturing this experimental result.')
    mod = primary(source, modifier_name)
    if mod is None:
        raise ValueError('Original surface-adhesion Bead modifier is missing.')
    target = merges.base_of(mod)
    if target is None or target.type != 'MESH' or target == source:
        raise ValueError('A separate mesh target B is required.')
    # Temporary evaluated source copy uses the current result pose on refresh.
    # No source transform, data, node inputs, or modifier stack are overwritten.
    temporary = source.copy()
    context.scene.collection.objects.link(temporary)
    temporary.hide_viewport = False
    temporary.hide_set(False)
    temporary.matrix_world = obj.matrix_world.copy()
    mesh = None
    try:
        mesh = _evaluated_mesh(context, temporary)
        _mask(mesh)
    except Exception:
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        raise
    finally:
        bpy.data.objects.remove(temporary, do_unlink=True)
    if current:
        _replace_mesh(obj, mesh)
        cn.set_input(current, merges.BASE, target)
        _neutral(current)
        obj.update_tag()
        return obj
    try:
        group = _new_group()
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    result = bpy.data.objects.new(source.name + ' Recapture', mesh)
    context.collection.objects.link(result)
    result.matrix_world = source.matrix_world.copy()
    result.color = source.color
    result[SOURCE], result[TARGET] = source, target
    result[SOURCE_HIDE], result[SOURCE_RENDER] = source.hide_get(), source.hide_render
    adjust = result.modifiers.new('EZ Recapture adjustments', 'NODES')
    adjust.node_group = group
    for name, value in ((merges.BASE, target), (merges.GLOBAL, 1.0),
                        (merges.INFLUENCE_RADIUS, cn.get_input(mod, merges.INFLUENCE_RADIUS)),
                        (merges.MERGE_RADIUS, 0.0), (merges.FALLOFF, merges.SMOOTH),
                        (merges.BEAD_CUSTOM_CURVE, False), (merges.BEAD_SYNC_NORMALS, False)):
        cn.set_input(adjust, name, value)
    source.hide_set(True)
    source.hide_render = True
    _activate(context, result)
    return result


def save_base(context, obj):
    mod = recapture_modifier(obj)
    if mod is None or len(obj.modifiers) != 1:
        raise ValueError('Save Base requires only the experimental Recapture modifier.')
    mesh = _evaluated_mesh(context, obj)
    if mesh.attributes.get(MASK) is None:
        bpy.data.meshes.remove(mesh)
        raise ValueError('Captured contact mask is missing.')
    _replace_mesh(obj, mesh)
    _neutral(mod)
    obj.update_tag()


def curve_node(mod):
    bead = mod.node_group.nodes.get('Captured Bead')
    return bead.node_tree.nodes.get(merges.CURVE_NODE) if bead and bead.node_tree else None


class EZHS_OT_recapture(bpy.types.Operator):
    """Capture the current evaluated Bead stack; this does not automatically repair folds"""
    bl_idname = 'ezhs.recapture'
    bl_label = 'Recapture (Experimental)'
    bl_options = {'REGISTER', 'UNDO'}
    modifier: StringProperty(name='Modifier')

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(recapture_modifier(context.active_object) or primary(context.active_object))

    def execute(self, context):
        try:
            capture(context, context.active_object, self.modifier)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Captured current evaluated shape. Automatic fold repair is not included.')
        return {'FINISHED'}


class EZHS_OT_recapture_save(bpy.types.Operator):
    """Save the adjusted result as the next baseline and reset applied adjustments"""
    bl_idname = 'ezhs.recapture_save'
    bl_label = 'Save New Base'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and recapture_modifier(context.active_object) is not None

    def execute(self, context):
        try:
            save_base(context, context.active_object)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Adjusted result saved; additional Pull and normal transfer reset.')
        return {'FINISHED'}


class EZHS_OT_recapture_restore(bpy.types.Operator):
    """Remove this generated experimental object and reveal the untouched original"""
    bl_idname = 'ezhs.recapture_restore'
    bl_label = 'Restore Original'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and recapture_modifier(context.active_object) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = context.active_object
        source = obj.get(SOURCE)
        if source is None:
            self.report({'ERROR'}, 'Original is missing; generated result was retained.')
            return {'CANCELLED'}
        hidden, render = bool(obj.get(SOURCE_HIDE)), bool(obj.get(SOURCE_RENDER))
        mesh, group = obj.data, recapture_modifier(obj).node_group
        inner = group.nodes['Captured Bead'].node_tree
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        if group.users == 0:
            bpy.data.node_groups.remove(group)
        if inner.users == 0:
            bpy.data.node_groups.remove(inner)
        _activate(context, source)
        source.hide_set(hidden)
        source.hide_render = render
        return {'FINISHED'}


class EZHS_OT_recapture_falloff(bpy.types.Operator):
    bl_idname = 'ezhs.recapture_falloff'
    bl_label = 'Recapture Falloff'
    bl_options = {'REGISTER', 'UNDO'}
    preset: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and recapture_modifier(context.active_object) is not None

    def execute(self, context):
        if self.preset not in (merges.SMOOTH, merges.LINEAR, merges.SHARP, merges.SPHERE):
            return {'CANCELLED'}
        cn.set_input(recapture_modifier(context.active_object), merges.FALLOFF, self.preset)
        context.active_object.update_tag()
        return {'FINISHED'}


def draw_panel(layout, obj):
    mod = recapture_modifier(obj)
    if mod is None:
        return False
    box = layout.box()
    box.label(text='Recapture / Experimental', icon='INFO')
    box.label(text='Captured result; not automatic mesh repair')
    col = box.column(align=True)
    for name in (merges.GLOBAL, merges.INFLUENCE_RADIUS, merges.MERGE_RADIUS):
        cn.draw_input(col, mod, name)
    row = box.row(align=True)
    for text, value in (('Smooth', merges.SMOOTH), ('Linear', merges.LINEAR),
                        ('Sharp', merges.SHARP), ('Sphere', merges.SPHERE)):
        row.operator('ezhs.recapture_falloff', text=text, depress=cn.get_input(mod, merges.FALLOFF) == value).preset = value
    cn.draw_input(col, mod, merges.BEAD_CUSTOM_CURVE)
    if cn.get_input(mod, merges.BEAD_CUSTOM_CURVE):
        box.template_curve_mapping(curve_node(mod), 'mapping')
    cn.draw_input(col, mod, merges.BEAD_SYNC_NORMALS)
    box.label(text='Push / Spread / Core / Subdivision disabled', icon='ERROR')
    box.label(text='Pull is experimental; inspect before saving')
    box.operator('ezhs.recapture', text='Recapture from Original', icon='FILE_REFRESH')
    box.operator('ezhs.recapture_save', icon='FILE_TICK')
    box.operator('ezhs.recapture_restore', icon='LOOP_BACK')
    return True


classes = (EZHS_OT_recapture, EZHS_OT_recapture_save, EZHS_OT_recapture_restore, EZHS_OT_recapture_falloff)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
