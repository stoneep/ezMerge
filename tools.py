import bpy
from bpy.types import WorkSpaceTool

from .hover import (
    draw_box_preview, draw_cone_preview, draw_cylinder_preview, draw_polygon_preview, draw_quadsphere_preview,
    draw_sphere_preview,
)
from .operators import (
    BOX_TOOL_ID, CONE_TOOL_ID, CYLINDER_TOOL_ID, POLYGON_TOOL_ID, PUSHPULL_TOOL_ID, QUADSPHERE_TOOL_ID,
    SPHERE_TOOL_ID,
)


def _create_keymap(operator):
    # The second item is Ctrl+click: placing the anchor on a mesh starts a cut.
    return (
        (operator, {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        (operator, {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True}, None),
    )


class EZHS_TL_cylinder(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = CYLINDER_TOOL_ID
    bl_label = "EZ Cylinder"
    bl_description = (
        "Click the center, click the radius, click the height.\n"
        "Ctrl+click on a mesh (or press C) to cut a hole (U: union, I: intersect), X/Y/Z lock the axis.\n"
        "Type a value + Enter for exact sizes, mouse wheel changes segments"
    )
    bl_icon = "ops.mesh.primitive_cylinder_add_gizmo"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = _create_keymap("ezhs.cylinder_create")

    def draw_settings(context, layout, tool):
        props = tool.operator_properties("ezhs.cylinder_create")
        layout.prop(props, "segments")
        layout.prop(context.window_manager, "ezhs_box_origin")
        layout.prop(context.window_manager, "ezhs_snap_cursor")

    draw_cursor = staticmethod(draw_cylinder_preview)


class EZHS_TL_polygon(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = POLYGON_TOOL_ID
    bl_label = "EZ Polygon"
    bl_description = (
        "Click the center, click a vertex (sets the size and the rotation), click the height.\n"
        "Mouse wheel changes the number of sides. Ctrl+click on a mesh (or press C) cuts a hole (U: union, I: intersect),\n"
        "X/Y/Z lock the axis. Type a value + Enter for exact sizes"
    )
    bl_icon = "ops.gpencil.primitive_polyline"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = _create_keymap("ezhs.polygon_create")

    def draw_settings(context, layout, tool):
        props = tool.operator_properties("ezhs.polygon_create")
        layout.prop(props, "segments")
        layout.prop(context.window_manager, "ezhs_box_origin")
        layout.prop(context.window_manager, "ezhs_snap_cursor")

    draw_cursor = staticmethod(draw_polygon_preview)


class EZHS_TL_cone(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = CONE_TOOL_ID
    bl_label = "EZ Cone"
    bl_description = (
        "Click the center, the base radius, the height, then the top radius.\n"
        "Click near the axis for a pointed cone, move out for a frustum; type a number for exact sizes.\n"
        "Ctrl+click on a mesh (or press C) to cut a hole (U: union, I: intersect), X/Y/Z lock the axis, the wheel sets the segments"
    )
    bl_icon = "ops.mesh.primitive_cone_add_gizmo"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = _create_keymap("ezhs.cone_create")

    def draw_settings(context, layout, tool):
        props = tool.operator_properties("ezhs.cone_create")
        layout.prop(props, "segments")
        layout.prop(context.window_manager, "ezhs_box_origin")
        layout.prop(context.window_manager, "ezhs_snap_cursor")

    draw_cursor = staticmethod(draw_cone_preview)


class EZHS_TL_sphere(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = SPHERE_TOOL_ID
    bl_label = "EZ Sphere"
    bl_description = (
        "Click the center, then click the radius.\n"
        "Ctrl+click on a mesh (or press C) to cut a dome-shaped hole (U: union, I: intersect), X/Y/Z lock the axis.\n"
        "Type a value + Enter for an exact radius, the mouse wheel changes the segments"
    )
    bl_icon = "ops.mesh.primitive_uv_sphere_add_gizmo"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = _create_keymap("ezhs.sphere_create")

    def draw_settings(context, layout, tool):
        props = tool.operator_properties("ezhs.sphere_create")
        layout.prop(props, "segments")
        layout.prop(context.window_manager, "ezhs_snap_cursor")

    draw_cursor = staticmethod(draw_sphere_preview)


class EZHS_TL_quadsphere(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = QUADSPHERE_TOOL_ID
    bl_label = "EZ Quad Sphere"
    bl_description = (
        "A sphere of quads without poles (a cube pushed onto a sphere). Click the center, then the radius.\n"
        "Ctrl+click on a mesh (or press C) to cut a dome-shaped hole (U: union, I: intersect), X/Y/Z lock the axis.\n"
        "Type a value + Enter for an exact radius, the mouse wheel changes the segments"
    )
    bl_icon = "ops.mesh.primitive_ico_sphere_add_gizmo"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = _create_keymap("ezhs.quadsphere_create")

    def draw_settings(context, layout, tool):
        props = tool.operator_properties("ezhs.quadsphere_create")
        layout.prop(props, "segments")
        layout.prop(context.window_manager, "ezhs_snap_cursor")

    draw_cursor = staticmethod(draw_quadsphere_preview)


class EZHS_TL_box(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = BOX_TOOL_ID
    bl_label = "EZ Box"
    bl_description = (
        "Click a corner, click the opposite corner, click the height.\n"
        "Draw start Center (M before the first click): click the center, the base starts as a square,\n"
        "X or Y then grows only that side. On a face the box lines up with its nearest edge (A toggles);\n"
        "Box origin (O): a corner, the center of the base (Plane) or the center of the whole box (3D).\n"
        "R draws a rotated rectangle.\n"
        "Type width;depth (e.g. 50;30) or one value for a square, then Enter.\n"
        "Ctrl+click on a mesh (or press C) to cut a hole (U: union, I: intersect), X/Y/Z lock the axis"
    )
    bl_icon = "ops.mesh.primitive_cube_add_gizmo"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = _create_keymap("ezhs.box_create")

    def draw_settings(context, layout, tool):
        layout.prop(context.window_manager, "ezhs_box_start")
        layout.prop(context.window_manager, "ezhs_box_origin")
        layout.prop(context.window_manager, "ezhs_snap_cursor")

    draw_cursor = staticmethod(draw_box_preview)


class EZHS_TL_pushpull(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = PUSHPULL_TOOL_ID
    bl_label = "EZ Push/Pull"
    bl_description = (
        "Drag a face of an EZ shape to change its size, or a round shape's side to change a radius.\n"
        "Type a value + Enter for exact sizes"
    )
    bl_icon = "ops.mesh.extrude_region_move"
    bl_cursor = 'CROSSHAIR'
    bl_keymap = (
        ("ezhs.pushpull", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("ezhs.pushpull", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, None),
    )

    def draw_settings(context, layout, tool):
        layout.prop(context.window_manager, "ezhs_snap_cursor")


_TOOLS = (EZHS_TL_cylinder, EZHS_TL_polygon, EZHS_TL_cone, EZHS_TL_sphere, EZHS_TL_quadsphere,
          EZHS_TL_box, EZHS_TL_pushpull)


def register():
    bpy.utils.register_tool(_TOOLS[0], separator=True)
    for before, tool in zip(_TOOLS, _TOOLS[1:]):
        bpy.utils.register_tool(tool, after={before.bl_idname})


def unregister():
    for tool in reversed(_TOOLS):
        bpy.utils.unregister_tool(tool)
