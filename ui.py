import bpy

from . import cuts, joins, merges
from . import shapes as cn
from .handles import TILT_EPSILON, tilt_degrees
from .operators import (
    BOX_TOOL_ID, CONE_TOOL_ID, CYLINDER_TOOL_ID, POLYGON_TOOL_ID, PUSHPULL_TOOL_ID, QUADSPHERE_TOOL_ID,
    SPHERE_TOOL_ID, EZHS_OT_box_create, EZHS_OT_cone_create, EZHS_OT_cylinder_create, EZHS_OT_polygon_create,
    EZHS_OT_quadsphere_create, EZHS_OT_sphere_create,
)

_KIND_ICONS = {cn.KIND_BOX: 'MESH_CUBE', cn.KIND_CONE: 'MESH_CONE', cn.KIND_SPHERE: 'MESH_UVSPHERE',
               cn.KIND_QUADSPHERE: 'MESH_ICOSPHERE'}


class VIEW3D_PT_ezhs(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "EZ"
    bl_label = "EZ Hard Surface"

    def draw(self, context):
        layout = self.layout
        grid = layout.grid_flow(row_major=True, columns=2, align=True)
        for text, icon, tool_id in (("Cylinder", 'MESH_CYLINDER', CYLINDER_TOOL_ID),
                                    ("Polygon", 'MESH_CIRCLE', POLYGON_TOOL_ID),
                                    ("Cone", 'MESH_CONE', CONE_TOOL_ID),
                                    ("Sphere", 'MESH_UVSPHERE', SPHERE_TOOL_ID),
                                    ("Quad Sphere", 'MESH_ICOSPHERE', QUADSPHERE_TOOL_ID),
                                    ("Box", 'MESH_CUBE', BOX_TOOL_ID),
                                    ("Push/Pull", 'EMPTY_SINGLE_ARROW', PUSHPULL_TOOL_ID)):
            grid.operator("wm.tool_set_by_id", text=text, icon=icon).name = tool_id
        layout.prop(context.window_manager, "ezhs_snap_cursor", icon='PIVOT_CURSOR')
        layout.label(text="Draw start", icon='MESH_CUBE')
        layout.row(align=True).prop(context.window_manager, "ezhs_box_start", expand=True)
        layout.label(text="Origin", icon='OBJECT_ORIGIN')
        layout.column(align=True).prop(context.window_manager, "ezhs_box_origin", expand=True)  # (long names)

        obj = context.object
        self.draw_cuts(layout, obj)
        if len(context.selected_objects) > 1:
            layout.operator("ezhs.join_shapes", icon='AUTOMERGE_ON')
            layout.operator("ezhs.merge_shapes", icon='SNAP_FACE')
        self.draw_merges(layout, obj)
        mod = cn.find_modifier(obj)
        if mod is None:
            return
        kind = cn.kind_of(mod)
        box = layout.box()
        box.label(text=obj.name, icon=_KIND_ICONS.get(kind, 'MESH_CYLINDER'))
        col = box.column(align=True)
        for name in cn.param_names(kind):
            if name not in ("Centered", "CenteredZ"):  # switching them would move the box: chosen when it is drawn
                cn.draw_input(col, mod, name)
        if kind in (cn.KIND_BOX, cn.KIND_CYLINDER, cn.KIND_CONE):
            params = cn.get_params(mod)
            if kind == cn.KIND_BOX:
                where = "box center" if params["CenteredZ"] else "base center" if params["Centered"] else "a corner"
            else:
                where = "shape center" if params["CenteredZ"] else "base center"
            box.label(text=f"Origin: {where}", icon='OBJECT_ORIGIN')
        box.row().prop(context.window_manager, "ezhs_handles", expand=True)
        tilt = tilt_degrees(obj)
        if tilt > TILT_EPSILON:
            box.label(text=f"Tilt: {tilt:.1f}\N{DEGREE SIGN} from vertical", icon='ORIENTATION_GIMBAL')
            box.operator("ezhs.shape_upright", icon='TRIA_UP_BAR')
        box.prop(context.window_manager, "ezhs_tilt_rings")
        box.operator("ezhs.shape_apply", icon='CHECKMARK')

    @staticmethod
    def draw_merges(layout, obj):
        if obj is None or obj.type != 'MESH':
            return
        for mod in merges.merge_modifiers(obj):
            base = merges.base_of(mod)
            box = layout.box()
            box.label(text=f"Merged onto {base.name}" if base else "Merge (no mesh)", icon='SNAP_FACE')
            method = merges.method_of(mod)
            row = box.row(align=True)
            for text, value in (("Rows", merges.ROWS), ("Deform", merges.DEFORM),
                                ("Bevel", merges.BEVEL), ("Bead", merges.BEAD)):
                op = row.operator("ezhs.merge_method", text=text, depress=method == value)
                op.modifier, op.method = mod.name, value
            if method == merges.BEAD and merges.is_surface_adhesion(mod):
                col = box.column(align=True)
                box.label(text="Surface Adhesion")
                for name in (merges.GLOBAL, merges.INFLUENCE_RADIUS, merges.MERGE_RADIUS, merges.PUSH_STRENGTH):
                    cn.draw_input(col, mod, name)
                    if name == merges.INFLUENCE_RADIUS and mod.node_group.interface.items_tree.get(merges.CONTACT_RADIUS) is not None:
                        cn.draw_input(col, mod, merges.CONTACT_RADIUS)
                if mod.node_group.interface.items_tree.get(merges.SURFACE_SPREAD) is not None:
                    cn.draw_input(col, mod, merges.SURFACE_SPREAD)
                    gap = col.column(align=True)
                    gap.enabled = cn.get_input(mod, merges.SURFACE_SPREAD) > 0
                    cn.draw_input(gap, mod, merges.SURFACE_CLEARANCE)
                    if mod.node_group.interface.items_tree.get(merges.ADHESION_ADAPTIVE_SPREAD) is not None:
                        cn.draw_input(gap, mod, merges.ADHESION_ADAPTIVE_SPREAD)
                    if mod.node_group.interface.items_tree.get(merges.SURFACE_CORNER_REACH) is not None:
                        cn.draw_input(gap, mod, merges.SURFACE_CORNER_REACH)
                    if mod.node_group.interface.items_tree.get(merges.SURFACE_RELAX) is not None:
                        cn.draw_input(gap, mod, merges.SURFACE_RELAX)
                        cn.draw_input(gap, mod, merges.SURFACE_RELAX_PASSES)
                row = box.row(align=True)
                current = cn.get_input(mod, merges.FALLOFF)
                for text, value in (("Smooth", merges.SMOOTH), ("Linear", merges.LINEAR),
                                    ("Sharp", merges.SHARP), ("Sphere", merges.SPHERE)):
                    op = row.operator("ezhs.merge_falloff", text=text, depress=current == value)
                    op.modifier, op.falloff = mod.name, value
                cn.draw_input(col, mod, merges.BEAD_CUSTOM_CURVE)
                if cn.get_input(mod, merges.BEAD_CUSTOM_CURVE):
                    if mod.node_group.users > 1:
                        box.operator("ezhs.bead_curve_own", icon='DUPLICATE').modifier = mod.name
                    else:
                        box.template_curve_mapping(merges.curve_node(mod), "mapping")
                        box.label(text="Outside 0 → Contact 1; y = influence")
                    box.operator("ezhs.bead_curve_reset", icon='LOOP_BACK').modifier = mod.name
                cn.draw_input(col, mod, merges.ADHESION_LEVELS)
                if mod.node_group.interface.items_tree.get(merges.ADHESION_LOCAL_SUBDIVISION) is not None:
                    cn.draw_input(col, mod, merges.ADHESION_LOCAL_SUBDIVISION)
                    if cn.get_input(mod, merges.ADHESION_LOCAL_SUBDIVISION):
                        box.label(text="Preview: transition shading may differ", icon='INFO')
                row = box.row(align=True)
                current = cn.get_input(mod, merges.BEAD_SUBDIVISION_ORDER)
                for text, value in (("After Snap", merges.AFTER_SNAP), ("Before Snap", merges.BEFORE_SNAP)):
                    op = row.operator("ezhs.bead_subdivision_order", text=text, depress=current == value)
                    op.modifier, op.order = mod.name, value
                box.label(text="Before Snap needs dense enough source geometry", icon='INFO')
                normals = box.column(align=True)
                normals.enabled = hasattr(bpy.types, 'GeometryNodeSetMeshNormal')
                cn.draw_input(normals, mod, merges.BEAD_SYNC_NORMALS)
                if not normals.enabled:
                    box.label(text="4.2: smooth shading fallback", icon='INFO')
            elif method == merges.BEAD:
                col = box.column(align=True)
                sizes = (merges.BEAD_WIDTH, merges.BEAD_HEIGHT) if mod.node_group.interface.items_tree.get(merges.BEAD_WIDTH) is not None else (merges.RADIUS,)
                profile = (merges.BEAD_PROFILE_RESOLUTION
                           if mod.node_group.interface.items_tree.get(merges.BEAD_PROFILE_RESOLUTION) is not None
                           else merges.RESOLUTION)
                for name in (merges.GLOBAL, *sizes, profile):
                    cn.draw_input(col, mod, name)
                if mod.node_group.interface.items_tree.get(merges.BEAD_BULGE) is not None:
                    cn.draw_input(col, mod, merges.BEAD_BULGE)
                if mod.node_group.interface.items_tree.get(merges.BEAD_MATERIAL) is not None:
                    cn.draw_input(col, mod, merges.BEAD_MATERIAL)
                if mod.node_group.interface.items_tree.get(merges.BEAD_CONTACT_CUTS) is not None:
                    cn.draw_input(col, mod, merges.BEAD_CONTACT_CUTS)
                if mod.node_group.interface.items_tree.get(merges.BEAD_SEAM_SAMPLES) is not None:
                    cn.draw_input(col, mod, merges.BEAD_SEAM_SAMPLES)
                    subdivision = box.box()
                    cn.draw_input(subdivision, mod, merges.BEAD_SURFACE_SUBDIVISION)
                    settings = subdivision.column(align=True)
                    settings.enabled = cn.get_input(mod, merges.BEAD_SURFACE_SUBDIVISION)
                    cn.draw_input(settings, mod, merges.BEAD_SURFACE_LEVELS)
                    row = settings.row(align=True)
                    current = cn.get_input(mod, merges.BEAD_SUBDIVISION_ORDER)
                    for text, value in (("After Snap", merges.AFTER_SNAP), ("Before Snap", merges.BEFORE_SNAP)):
                        op = row.operator("ezhs.bead_subdivision_order", text=text, depress=current == value)
                        op.modifier, op.order = mod.name, value
                    if current == merges.BEFORE_SNAP:
                        settings.label(text="Fits new samples; denser B helps", icon='INFO')
                if mod.node_group.interface.items_tree.get(merges.BEAD_CUSTOM_CURVE) is not None:
                    cn.draw_input(col, mod, merges.BEAD_CUSTOM_CURVE)
                    if cn.get_input(mod, merges.BEAD_CUSTOM_CURVE):
                        if mod.node_group.users > 1:
                            box.operator("ezhs.bead_curve_own", icon='DUPLICATE').modifier = mod.name
                        else:
                            box.template_curve_mapping(merges.curve_node(mod), "mapping")
                            box.label(text="Border 0 → Center 1; diagonal = unchanged")
                        box.operator("ezhs.bead_curve_reset", icon='LOOP_BACK').modifier = mod.name
                    normals = box.column(align=True)
                    normals.enabled = hasattr(bpy.types, 'GeometryNodeSetMeshNormal')
                    cn.draw_input(normals, mod, merges.BEAD_SYNC_NORMALS)
                    if not normals.enabled:
                        box.label(text="Sync Normals needs Blender 4.5+", icon='INFO')
            elif method == merges.BEVEL:
                bevel = merges.bevel_modifier(obj, mod)
                if bevel is not None:
                    col = box.column(align=True)
                    col.prop(bevel, "width", text="Radius")
                    col.prop(bevel, "segments")
                    col.prop(bevel, "profile")
                    col.prop(bevel, "miter_outer", text="Miter Outer")
                    col.prop(bevel, "miter_inner", text="Miter Inner")
                    col.prop(bevel, "use_clamp_overlap")
            elif method == merges.ROWS:
                rows = merges.bevel_modifier(obj, mod)
                if rows is not None:
                    box.prop(rows, "segments", text="Segments (half on the wall)")
                row = box.row(align=True)
                current = cn.get_input(mod, merges.FALLOFF)
                for text, value in (("Round", merges.SMOOTH), ("Chamfer", merges.LINEAR), ("Custom", merges.CUSTOM)):
                    op = row.operator("ezhs.merge_falloff", text=text, depress=current == value)
                    op.modifier, op.falloff = mod.name, value
                if current == merges.CUSTOM and merges.curve_node(mod) is not None:
                    box.template_curve_mapping(merges.curve_node(mod), "mapping")
                col = box.column(align=True)
                for name in (merges.GLOBAL, merges.HEIGHT, merges.SPREAD, merges.LIFT, merges.PROFILE, merges.SUBDIVISION,
                             merges.SMOOTH_ANGLE):
                    cn.draw_input(col, mod, name)
            else:
                current = cn.get_input(mod, merges.FALLOFF)
                grid = box.grid_flow(row_major=True, columns=3, align=True)  # (six shapes do not fit in one row)
                for text, value in merges.FALLOFFS:
                    op = grid.operator("ezhs.merge_falloff", text=text, depress=current == value)
                    op.modifier, op.falloff = mod.name, value
                if current == merges.CUSTOM and merges.curve_node(mod) is not None:
                    box.template_curve_mapping(merges.curve_node(mod), "mapping")
                col = box.column(align=True)
                for name in (merges.GLOBAL, merges.RADIUS, merges.STRENGTH, merges.SUBDIVISION, merges.CONTACT):
                    try:
                        cn.draw_input(col, mod, name)
                    except KeyError:  # a merge of an older file: its group lacks this input
                        pass
            row = box.row(align=True)
            row.operator("ezhs.merge_apply", icon='CHECKMARK')
            row.operator("ezhs.merge_remove", icon='X').modifier = mod.name

    @staticmethod
    def draw_cuts(layout, obj):
        if obj is None:
            return
        if cuts.is_cutter(obj):
            target = cuts.find_target(obj)
            box = layout.box()
            box.label(text=f"Cutter of {target.name}" if target else "Cutter", icon='MOD_BOOLEAN')
            mod = cuts.find_cut_modifier(obj)
            if mod is not None:
                current = cuts.operation_of(obj)
                row = box.row(align=True)
                for operation, text in (("DIFFERENCE", "Cut"), ("UNION", "Add"), ("INTERSECT", "Intersect"),
                                        ("JOIN", "Join")):
                    row.operator("ezhs.cut_operation", text=text, depress=current == operation).operation = operation
                if current == cuts.JOIN:
                    col = box.column(align=True)
                    for name in (joins.FILLET, joins.DETAIL):
                        cn.draw_input(col, mod, name)
            box.operator("ezhs.cut_release", icon='X')
        elif cuts.cut_modifiers(obj):
            box = layout.box()
            box.label(text=f"{len(cuts.cut_modifiers(obj))} cut(s)", icon='MOD_BOOLEAN')
            box.operator("ezhs.cut_apply", icon='CHECKMARK')



def draw_add_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator_context = 'INVOKE_REGION_WIN'
    layout.operator(EZHS_OT_cylinder_create.bl_idname, icon='MESH_CYLINDER')
    layout.operator(EZHS_OT_polygon_create.bl_idname, icon='MESH_CIRCLE')
    layout.operator(EZHS_OT_cone_create.bl_idname, icon='MESH_CONE')
    layout.operator(EZHS_OT_sphere_create.bl_idname, icon='MESH_UVSPHERE')
    layout.operator(EZHS_OT_quadsphere_create.bl_idname, icon='MESH_ICOSPHERE')
    layout.operator(EZHS_OT_box_create.bl_idname, icon='MESH_CUBE')


def register():
    bpy.utils.register_class(VIEW3D_PT_ezhs)
    bpy.types.VIEW3D_MT_mesh_add.append(draw_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(draw_add_menu)
    bpy.utils.unregister_class(VIEW3D_PT_ezhs)
