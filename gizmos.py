"""Always-visible arrow handles on the active EZ shape.

The arrows are Blender's own ``arrow_3d`` gizmos; dragging one starts the
``ezhs.shape_handle`` operator, so typed values, Ctrl snapping, Esc and
undo behave exactly like the Push/Pull tool. All possible arrows exist; the
ones that do not apply to the active shape's kind are hidden.
"""

import bpy
from bpy.app.handlers import persistent

from . import shapes as cn
from .controllers import modal_running, reset_running
from .settings import handle_mode, redraw_viewports
from .handles import (
    ALL_PARTS, DIALS, ROTATE, ROTATE_X, ROTATE_Y, TILT_PARTS, all_parts_for, handle_matrix, handle_pose,
)

# Arrows follow the axis colors of Blender; the radius handle is orange.
X_COLOR = (0.95, 0.30, 0.30)
Y_COLOR = (0.35, 0.80, 0.25)
Z_COLOR = (0.30, 0.50, 1.00)
RADIUS_COLOR = (1.00, 0.55, 0.10)
ROTATE_COLOR = (0.95, 0.85, 0.25)
_COLORS = {'TOP': Z_COLOR, 'BOTTOM': Z_COLOR, 'X0': X_COLOR, 'X1': X_COLOR,
           'Y0': Y_COLOR, 'Y1': Y_COLOR, 'SIDE': RADIUS_COLOR, 'TOPR': RADIUS_COLOR, ROTATE: ROTATE_COLOR,
           ROTATE_X: X_COLOR, ROTATE_Y: Y_COLOR}
_SCALE = {'TOP': 0.9, 'BOTTOM': 0.9, 'X0': 0.8, 'X1': 0.8, 'Y0': 0.8, 'Y1': 0.8, 'SIDE': 0.7, 'TOPR': 0.7, ROTATE: 0.55,
          ROTATE_X: 0.32, ROTATE_Y: 0.22}


class EZHS_GGT_shape(bpy.types.GizmoGroup):
    bl_idname = "EZHS_GGT_shape"
    bl_label = "EZ Shape Handles"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    # PERSISTENT: always on (no gizmo_group_type_ensure needed).
    # EXCLUDE_MODAL: hide the arrows while a gizmo/operator drag is running.
    bl_options = {'3D', 'PERSISTENT', 'EXCLUDE_MODAL'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (context.mode == 'OBJECT' and obj is not None and obj.select_get()
                and cn.find_modifier(obj) is not None)

    def setup(self, context):
        self.handles = {}
        for part in ALL_PARTS:
            gz = self.gizmos.new("GIZMO_GT_dial_3d" if part in DIALS else "GIZMO_GT_arrow_3d")
            if part not in DIALS:
                gz.draw_style = 'NORMAL'
            gz.use_draw_modal = False
            gz.scale_basis = _SCALE[part]
            gz.line_width = 2.0
            gz.color = _COLORS[part]
            gz.alpha = 0.6
            gz.color_highlight = _COLORS[part]
            gz.alpha_highlight = 1.0
            props = gz.target_set_operator("ezhs.shape_handle")
            props.part = part
            self.handles[part] = gz

    def refresh(self, context):
        self.draw_prepare(context)

    def draw_prepare(self, context):
        obj = context.active_object
        mod = cn.find_modifier(obj)
        if mod is None:
            return
        # EXCLUDE_MODAL only covers gizmo drags: also hide while a tool runs.
        hidden = modal_running()
        rings = context.window_manager.ezhs_tilt_rings
        mode = handle_mode(context)
        parts = all_parts_for(mod)
        for part, gz in self.handles.items():
            # Resize and Rotate apart: the dial and rings no longer sit on the arrow at the origin.
            wrong_mode = (mode == 'RESIZE' and part in DIALS) or (mode == 'ROTATE' and part not in DIALS)
            gz.hide = hidden or part not in parts or wrong_mode or (part in TILT_PARTS and not rings)
            if part in parts:
                location, direction = handle_pose(obj, mod, part)
                gz.matrix_basis = handle_matrix(location, direction)


@persistent
def _on_load(_dummy):
    # A modal operator cannot survive loading a file; do not stay hidden.
    reset_running()


def register():
    bpy.types.WindowManager.ezhs_tilt_rings = bpy.props.BoolProperty(
        name="Tilt rings", default=True, update=redraw_viewports,
        description="Show the red and green rings that tilt the selected shape about its own X and Y axes")
    bpy.utils.register_class(EZHS_GGT_shape)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.utils.unregister_class(EZHS_GGT_shape)
    del bpy.types.WindowManager.ezhs_tilt_rings
