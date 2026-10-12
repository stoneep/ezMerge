"""Options of the add-on. They live on the window manager: they belong to the session, not to a .blend file."""

import bpy


def redraw_viewports(self, context):
    """Property update: the 3D views (the sidebar is another region) show the change on their next redraw."""
    for window in self.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def snap_to_cursor(context):
    """True if the 3D cursor is an inference point of the tools."""
    return bool(getattr(context.window_manager, "ezhs_snap_cursor", False))


def toggle_snap_to_cursor(context):
    """Flip the option; returns the new value."""
    value = not snap_to_cursor(context)
    context.window_manager.ezhs_snap_cursor = value
    return value


START_MODES = (
    ('CORNER', "Corner", "The first click is a corner of the base; the second click is the opposite corner"),
    ('CENTER', "Center", "The first click is the center of the base (like a cylinder): the base starts as a "
                         "square, X or Y then grows only that side"),
)


def box_from_center(context):
    """True if the box tool starts at the center of the base (like the cylinder) instead of at a corner."""
    return getattr(context.window_manager, "ezhs_box_start", 'CORNER') == 'CENTER'


ORIGIN_MODES = (
    ('CORNER', "Corner", "The origin of a new box is a corner of its base (round shapes have no corner: the "
                         "center of their base, as before)"),
    ('PLANE', "Center (Plane)", "The origin is the center of the base (like a cylinder): Rotate and Scale work "
                                "around the middle of the base"),
    ('CENTER3D', "Center (3D)", "The origin is the center of the whole shape, halfway up as well (boxes, "
                                "cylinders, polygons and cones)"),
)


def box_origin_mode(context):
    """Where new boxes get their origin: 'CORNER', 'PLANE' (center of the base) or 'CENTER3D'."""
    return getattr(context.window_manager, "ezhs_box_origin", 'CORNER')


HANDLE_MODES = (
    ('ALL', "All", "Show every handle: the resize arrows and the rotation dial and tilt rings"),
    ('RESIZE', "Resize", "Show only the arrows that resize the shape"),
    ('ROTATE', "Rotate", "Show only the rotation dial and the tilt rings"),
)


def handle_mode(context):
    return getattr(context.window_manager, "ezhs_handles", 'ALL')


def register():
    bpy.types.WindowManager.ezhs_box_origin = bpy.props.EnumProperty(
        name="Origin", items=ORIGIN_MODES, default='CORNER',
        description="Where the origin of a new shape is: for a box a corner, the center of the base (Plane) or "
                    "the center of the whole box (3D); for a cylinder, polygon or cone the center of the base or "
                    "(3D) halfway up. So Rotate and Scale from Blender work around the middle. Press O before "
                    "the first click to cycle")
    bpy.types.WindowManager.ezhs_handles = bpy.props.EnumProperty(
        name="Handles", items=HANDLE_MODES, default='ALL', update=redraw_viewports,
        description="Which handles the selected shape shows. Resize and Rotate apart keep the dial and rings "
                    "off the arrow at the origin. Press E to switch")
    bpy.types.WindowManager.ezhs_box_start = bpy.props.EnumProperty(
        name="Draw start", items=START_MODES, default='CORNER',
        description="Where a box starts when you draw it: at a corner of the base (default) or at the center of "
                    "the base, like a cylinder. Separate from where the origin of the box is. Press M before "
                    "the first click to switch")
    bpy.types.WindowManager.ezhs_snap_cursor = bpy.props.BoolProperty(
        name="Snap to 3D cursor", default=False,
        description="Treat the 3D cursor as a snap point (and take its orientation) when drawing shapes. "
                    "Press K while drawing to switch it")


def unregister():
    del bpy.types.WindowManager.ezhs_box_origin
    del bpy.types.WindowManager.ezhs_handles
    del bpy.types.WindowManager.ezhs_box_start
    del bpy.types.WindowManager.ezhs_snap_cursor
