"""Hover preview of the create tools: shows where the anchor will land.

The tools' first click places the anchor (center or corner) immediately, so
without this the snap (corner, midpoint, face center, cap center) would only
be visible after the fact. Blender calls ``draw_cursor`` while a tool is active.
"""

from mathutils import Vector

from .controllers import BoxCreate, ConeCreate, CylinderCreate, QuadSphereCreate, SphereCreate, modal_running


def make_preview(make_controller):
    """A ``draw_cursor`` function that previews the anchor of ``make_controller(context, mouse)``."""

    def draw_preview(context, _tool, xy):
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None or region.type != 'WINDOW' or modal_running():
            return
        # Cursor positions arrive in window pixels; the controllers work in region pixels.
        mouse = Vector(xy) - Vector((region.x, region.y))
        if not (0 <= mouse.x <= region.width and 0 <= mouse.y <= region.height):
            return
        preview = make_controller(context, mouse)
        preview.preview = True
        preview.update(context)
        preview.overlay.draw_in_window(region, rv3d, context.preferences.system.ui_scale or 1.0)

    return draw_preview


draw_cylinder_preview = make_preview(lambda context, mouse: CylinderCreate(context, mouse, 32))
draw_polygon_preview = make_preview(lambda context, mouse: CylinderCreate(context, mouse, 6, polygon=True))
draw_cone_preview = make_preview(lambda context, mouse: ConeCreate(context, mouse, 32))
draw_sphere_preview = make_preview(lambda context, mouse: SphereCreate(context, mouse, 32))
draw_quadsphere_preview = make_preview(lambda context, mouse: QuadSphereCreate(context, mouse, 32))
draw_box_preview = make_preview(lambda context, mouse: BoxCreate(context, mouse))
