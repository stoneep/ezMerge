"""Viewport feedback: guide lines, points and a value label near the cursor.

Operators fill an Overlay with plain data; the draw callbacks only read it,
so they never touch the operator after it has finished.
"""

import math

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader

from .geometry import any_perpendicular

LABEL_BG = (0.08, 0.08, 0.08, 0.80)
LABEL_FG = (1.0, 1.0, 1.0, 1.0)
LABEL_TYPED = (1.0, 0.80, 0.30, 1.0)


def circle_points(center, axis, radius, segments=64):
    u = any_perpendicular(axis)
    v = axis.cross(u)
    return [center + (u * math.cos(a) + v * math.sin(a)) * radius
            for a in (2.0 * math.pi * i / segments for i in range(segments))]


class Overlay:
    def __init__(self, region=None):
        self.region = region  # only draw in the viewport the tool runs in
        self.lines = []    # (coords, color, width) with coords as LINES pairs
        self.points = []   # (coord, color)
        self.label = None  # (text, typed)
        self.mouse = (0, 0)
        self._handles = []

    def clear(self):
        self.lines.clear()
        self.points.clear()
        self.label = None

    def line(self, a, b, color, width=2.0):
        self.lines.append(([a.copy(), b.copy()], color, width))

    def circle(self, center, axis, radius, color, width=2.0):
        pts = circle_points(center, axis, radius)
        coords = []
        for i, p in enumerate(pts):
            coords += [p, pts[(i + 1) % len(pts)]]
        self.lines.append((coords, color, width))

    def point(self, coord, color):
        self.points.append((coord.copy(), color))

    def add(self):
        space = bpy.types.SpaceView3D
        self._handles = [
            space.draw_handler_add(self._draw_view, (), 'WINDOW', 'POST_VIEW'),
            space.draw_handler_add(self._draw_pixel, (), 'WINDOW', 'POST_PIXEL'),
        ]

    def remove(self):
        for handle in self._handles:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        self._handles = []

    def _other_region(self):
        return self.region is not None and bpy.context.region != self.region

    def _draw_view(self):
        if self._other_region():
            return
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        if self.lines:
            shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            shader.bind()
            shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
            for coords, color, width in self.lines:
                batch = batch_for_shader(shader, 'LINES', {"pos": coords})
                shader.uniform_float("lineWidth", width)
                shader.uniform_float("color", color)
                batch.draw(shader)
        gpu.state.blend_set('NONE')

    def _draw_pixel(self):
        if self._other_region():
            return
        ui_scale = bpy.context.preferences.system.ui_scale or 1.0
        if self.points:
            self._draw_points(bpy.context.region, bpy.context.region_data, 5.0 * ui_scale)
        if self.label:
            self._draw_label(ui_scale)

    def draw_in_window(self, region, rv3d, ui_scale=1.0):
        """Draw everything in 2D, in window pixels (for tool cursors).

        Tool cursors are drawn in window space with no 3D view matrices, so the
        3D lines are projected by hand and everything is shifted by the region
        origin. The cursor position in ``self.mouse`` is region-relative.
        """
        origin = (region.x, region.y)
        gpu.state.blend_set('ALPHA')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        for coords, color, width in self.lines:
            flat = []
            for a, b in zip(coords[0::2], coords[1::2]):
                pa = location_3d_to_region_2d(region, rv3d, a)
                pb = location_3d_to_region_2d(region, rv3d, b)
                if pa is not None and pb is not None:
                    flat += [(pa.x + origin[0], pa.y + origin[1]), (pb.x + origin[0], pb.y + origin[1])]
            if flat:
                gpu.state.line_width_set(width)
                batch = batch_for_shader(shader, 'LINES', {"pos": flat})
                shader.bind()
                shader.uniform_float("color", color)
                batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')
        self._draw_points(region, rv3d, 5.0 * ui_scale, origin)
        if self.label:
            self._draw_label(ui_scale, origin)

    def _draw_points(self, region, rv3d, radius, origin=(0.0, 0.0)):
        # Drawn as 2D discs: there is no point shader common to 4.2 and later.
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        for coord, color in self.points:
            p = location_3d_to_region_2d(region, rv3d, coord)
            if p is None:
                continue
            p = (p.x + origin[0], p.y + origin[1])
            ring = [(p[0] + radius * math.cos(a), p[1] + radius * math.sin(a))
                    for a in (2.0 * math.pi * i / 16 for i in range(16))]
            tris = [v for i in range(16) for v in (p, ring[i], ring[(i + 1) % 16])]
            batch = batch_for_shader(shader, 'TRIS', {"pos": tris})
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.state.blend_set('NONE')

    def _draw_label(self, ui_scale, origin=(0.0, 0.0)):
        text, typed = self.label
        font = 0
        size = round(13 * ui_scale)
        blf.size(font, size)
        w, h = blf.dimensions(font, text)
        x, y = self.mouse[0] + origin[0] + 18, self.mouse[1] + origin[1] - 28
        pad = 5

        gpu.state.blend_set('ALPHA')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        x0, y0, x1, y1 = x - pad, y - pad, x + w + pad, y + h + pad
        batch = batch_for_shader(shader, 'TRIS', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]},
                                 indices=((0, 1, 2), (0, 2, 3)))
        shader.uniform_float("color", LABEL_BG)
        batch.draw(shader)
        gpu.state.blend_set('NONE')

        blf.position(font, x, y, 0)
        blf.color(font, *(LABEL_TYPED if typed else LABEL_FG))
        blf.draw(font, text)
