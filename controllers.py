"""Interactive logic of the tools, independent of bpy.types.Operator.

Each controller receives (context, event) pairs and returns one of
'RUNNING', 'FINISHED', 'CANCELLED' or 'PASS' (pass the event through).
"""

import math

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Matrix, Quaternion, Vector

from . import cuts
from . import shapes as cn
from .geometry import (
    Drag1D, axis_color, closest_on_axis, ground_hit, mouse_ray, orientation_for_normal,
    orientation_from_axes, pixel_size, radial_on_plane, ray_plane_point, scene_raycast_ex, signed_angle,
    snap, snap_step, wrap_angle,
)
from .handles import FACES, SIDE, TOPR, corner_base, dimension, face_center, frame_of, tilt_degrees
from .inference import KIND_CURSOR, SNAP_PIXELS, cursor_inference, find_edge, find_inference
from .numinput import NumInput, format_length, parse_angle
from .overlay import Overlay
from .settings import ORIGIN_MODES, box_from_center, box_origin_mode, snap_to_cursor, toggle_snap_to_cursor

X = Vector((1.0, 0.0, 0.0))
Y = Vector((0.0, 1.0, 0.0))
Z = Vector((0.0, 0.0, 1.0))
AXES = {'X': X, 'Y': Y, 'Z': Z}

POINT_COLOR = (1.0, 0.55, 0.10, 1.0)
SNAP_COLOR = (0.20, 0.90, 0.90, 1.0)
GUIDE_COLOR = (1.0, 1.0, 1.0, 0.85)
HILITE_COLOR = (1.0, 0.85, 0.20, 1.0)
ALIGN_COLOR = (0.60, 1.00, 0.30, 1.0)
GHOST_COLOR = (0.55, 0.55, 0.65, 0.8)
# the shape's own X, Y and Z axes (as the handles and the tilt rings are colored)
LOCAL_COLORS = ((0.95, 0.30, 0.30, 1.0), (0.35, 0.80, 0.25, 1.0), (0.30, 0.50, 1.00, 1.0))

# Faces with more corners than this (round caps) are not worth aligning a box to.
ALIGN_MAX_CORNERS = 8
ANGLE_STEP = math.radians(15.0)

_NAVIGATION = {
    'MIDDLEMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM',
}
_MOVE = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}

_KEYS = {'X', 'Y', 'Z', 'C', 'A', 'R', 'P', 'O', 'N', 'U', 'I', 'M'}  # (K is handled before)
_CUT_KEYS = {'C': cuts.DIFFERENCE, 'U': cuts.UNION, 'I': cuts.INTERSECT}
_MODIFIER_KEYS = {'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_ALT', 'RIGHT_ALT'}

STATUS_TEXT = ("LMB: confirm   Type value + Enter: exact   Wheel/PgUp/PgDn: segments   "
               "Ctrl: snap   Alt: no inference   K: 3D cursor snap   MMB: navigate   Esc/RMB: cancel")
CREATE_STATUS_TEXT = ("LMB: confirm   Type value + Enter: exact   X/Y/Z: lock axis   C: cut   U: union   "
                      "I: intersect   Ctrl+click on a mesh: cut   Wheel: segments   Ctrl: snap   "
                      "Alt: no inference   K: 3D cursor snap   Esc/RMB: cancel")
POLYGON_STATUS_TEXT = CREATE_STATUS_TEXT.replace("Wheel: segments", "Wheel: sides")
BOX_STATUS_TEXT = ("LMB: confirm   Type width;depth (or one value) + Enter: exact   X/Y/Z: lock axis   "
                   "M: draw start   O: box origin   A: align to edges   R: rotated rectangle   C: cut   U: union   I: intersect   "
                   "Ctrl+click on a mesh: cut   Ctrl: snap   Alt: no inference   K: 3D cursor snap   "
                   "Esc/RMB: cancel")
ROTATE_STATUS_TEXT = ("LMB: confirm   Type degrees + Enter: exact   X/Y/Z: axis (world, shape, back)   "
                      "P: pick pivot   C: pivot at center   O: pivot at origin   U: vertical   "
                      "Ctrl: 15\N{DEGREE SIGN} steps   Alt: no inference   K: 3D cursor snap   Esc/RMB: cancel")
VERTICAL_SNAP = math.radians(4.0)  # how close the turn must come to the vertical one to snap to it
VERTICAL_TOLERANCE = 2e-3          # how well an axis must be square to the lean to be able to stand the shape up


_running = set()


def modal_running():
    """True while any EZ modal tool is active (the handle gizmos hide then)."""
    return bool(_running)


def reset_running():
    _running.clear()


class ModalController:
    title = ""
    status_text = STATUS_TEXT
    segments_label = "Segments"  # None: the shape has no segment count

    def __init__(self, context, mouse, segments):
        self.region = context.region
        self.rv3d = context.region_data
        self.units = context.scene.unit_settings
        self.mouse = Vector(mouse)
        self.press_mouse = None
        self.cursor_x = None    # X axis of the 3D cursor while the tools snap to it, else None
        self.ctrl = False
        self.alt = False
        self.preview = False  # drawing a hover preview only: no header, no scene changes
        self.message = ""
        self.infer_kind = ""    # what the value/point is currently snapped to
        self.infer_point = None
        # ui_scale reads 0.0 without a real UI (bpy module); never let that disable snapping.
        self.snap_px = SNAP_PIXELS * (context.preferences.system.ui_scale or 1.0)
        self.segments = cn.clamp_segments(segments)
        self.num = NumInput()
        self.overlay = Overlay(context.region)
        self.drag_threshold = context.preferences.inputs.drag_threshold_mouse

    # -- lifecycle ---------------------------------------------------------

    def start(self, context):
        _running.add(self)
        self.overlay.add()
        context.workspace.status_text_set(self.status_text)
        self.update(context)

    def end(self, context):
        _running.discard(self)
        self.overlay.remove()
        context.area.header_text_set(None)
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    # -- events --------------------------------------------------------------

    def handle(self, context, event):
        etype, value = event.type, event.value
        if etype in _NAVIGATION or etype.startswith('NDOF'):
            return 'PASS'
        self.ctrl, self.alt = event.ctrl, event.alt

        if etype in _MOVE:
            self.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            self.update(context)
            return 'RUNNING'
        if etype == 'ESC' or (etype == 'RIGHTMOUSE' and value == 'PRESS'):
            return 'CANCELLED'
        if etype in _MODIFIER_KEYS:
            self.update(context)
            return 'RUNNING'
        if etype == 'LEFTMOUSE':
            self.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            if value == 'PRESS':
                self.update(context)
                self.press_mouse = self.mouse.copy()
                return self._confirm(context)
            if (value == 'RELEASE' and self.press_mouse is not None
                    and (self.mouse - self.press_mouse).length > self.drag_threshold):
                # Press-drag-release confirms like a second click.
                self.update(context)
                self.press_mouse = None
                return self._confirm(context)
            return 'RUNNING'
        if etype in {'RET', 'NUMPAD_ENTER'} and value == 'PRESS':
            self.update(context)
            self.press_mouse = None
            return self._confirm(context)
        if value == 'PRESS' and etype in {'WHEELUPMOUSE', 'PAGE_UP', 'WHEELDOWNMOUSE', 'PAGE_DOWN'}:
            step = 1 if etype in {'WHEELUPMOUSE', 'PAGE_UP'} else -1
            self.set_segments(self.segments + step * (4 if event.shift else 1))
            self.update(context)
            return 'RUNNING'
        if value == 'PRESS' and etype == 'K' and not self.num.active and not event.ctrl and not event.alt:
            self.message = f"Snap to 3D cursor: {'on' if toggle_snap_to_cursor(context) else 'off'}"
            self.update(context)
            return 'RUNNING'
        if (value == 'PRESS' and etype in _KEYS and not self.num.active
                and not event.ctrl and not event.alt):
            self.message = ""
            if self.key(context, etype):
                self.update(context)
                return 'RUNNING'
        if self.num.handle(event):
            self.update(context)
            return 'RUNNING'
        return 'RUNNING'

    def _confirm(self, context):
        self.message = ""
        result = self.confirm(context)
        self.num.clear()
        if result == 'RUNNING':
            self.update(context)
        return result

    # -- helpers for subclasses ---------------------------------------------

    def ray(self):
        return mouse_ray(self.region, self.rv3d, self.mouse)

    def pick(self, context, exclude=(), reject=None):
        """Raycast under the cursor -> (RayHit or None, Inference or None).

        Alt turns the inference off; ``exclude`` objects are looked through.
        """
        origin, direction = self.ray()
        hit = scene_raycast_ex(context, origin, direction, exclude)
        inference = None
        self.cursor_x = None
        if not self.alt and snap_to_cursor(context):  # the 3D cursor beats the mesh under it
            inference = cursor_inference(context, self.region, self.rv3d, self.mouse, self.snap_px, reject)
            if inference is not None:
                self.cursor_x = self._cursor_x(context)
        if inference is None and hit is not None and not self.alt:
            inference = find_inference(context, self.region, self.rv3d, self.mouse, hit,
                                       self.snap_px, reject)
        self.infer_kind = inference.kind if inference else ""
        self.infer_point = inference.point if inference else None
        return hit, inference

    @staticmethod
    def _cursor_x(context):
        """The 3D cursor's own X axis (what a box drawn there lines up with)."""
        return (context.scene.cursor.matrix.to_3x3() @ X).normalized()

    def apply_input(self, value, snapped_ok=True):
        """Apply Ctrl snapping and the typed value to a dragged length."""
        if self.ctrl and snapped_ok:
            value = snap(value, snap_step(self.rv3d, self.units))
        if self.num.active:
            typed = self.num.value(self.units)
            if typed is not None:
                value = typed
        return value

    def label(self, name, value):
        if self.num.active:
            return f"{name} = {self.num.text}_", True
        text = f"{name} {format_length(value, self.units)}"
        return (f"{text}   ({self.infer_kind})" if self.infer_kind else text), False

    def header(self, context, text):
        self.overlay.mouse = tuple(self.mouse)
        if self.preview:
            return
        extra = f"   {self.message}" if self.message else ""
        sides = f"   {self.segments_label}: {self.segments}" if self.segments_label else ""
        context.area.header_text_set(f"{self.title}   {text}{sides}{extra}")
        context.area.tag_redraw()

    # -- to implement --------------------------------------------------------

    def set_segments(self, value):
        self.segments = cn.clamp_segments(value)

    def key(self, context, key):
        """Handle X/Y/Z/C. Return True if the key was used."""
        return False

    def update(self, context):
        raise NotImplementedError

    def confirm(self, context):
        raise NotImplementedError

    def cancel(self, context):
        pass


def cap_center_snap(obj, loc, nor):
    """Inference: snap to the center of an EZ cylinder cap near the cursor."""
    mod = cn.find_modifier(obj)
    if mod is None or cn.kind_of(mod) != cn.KIND_CYLINDER:
        return None
    params = cn.get_params(mod)
    mw = obj.matrix_world
    m3 = mw.to_3x3()
    axis = (m3 @ Z).normalized()
    if abs(nor.dot(axis)) < 0.95:
        return None
    reach = max(params["Radius"] * (m3 @ X).length * 0.3, 1e-6)
    lift = cn.origin_offset(cn.KIND_CYLINDER, params).z  # the origin may be halfway up
    for z in (params["Height"] - lift, -lift):
        center = mw @ Vector((0.0, 0.0, z))
        if (loc - center).length <= reach:
            return center, (axis if axis.dot(nor) > 0.0 else -axis)
    return None


class ShapeCreate(ModalController):
    """Anchor -> size -> height. Shared by the cylinder, polygon and box tools.

    X/Y/Z lock the shape's axis to a world axis (anchor and size steps).
    Ctrl+click on a mesh when placing the anchor, or C at any step, makes the
    shape cut into that mesh with a live, non-destructive Boolean; U adds the
    shape to the mesh (union) and I keeps only where they overlap (intersect).
    Subclasses provide the object and the size step.
    """

    status_text = CREATE_STATUS_TEXT
    SIZE_STATE = 'RADIUS'
    FINAL_STATE = None  # a step after the height (a cone's top radius), or None
    ROUND = False       # cylinders, polygons and cones: the origin option puts theirs halfway up (Center (3D))

    def __init__(self, context, mouse, segments=32):
        super().__init__(context, mouse, segments)
        self.state = 'CENTER'
        self.origin_mode = box_origin_mode(context)  # the Origin option (re-read while hovering)
        self.origin_3d = False  # this shape's origin is halfway up (decided with the first click)
        self.center = Vector()
        self.normal = Z.copy()          # shape axis (surface normal or locked axis)
        self.surface_normal = Z.copy()  # what the surface under the cursor says
        self.axis_lock = None
        self.snapped = False
        self.inference = ""
        self.base_rotation = Quaternion()
        self.x_hint = None              # direction local X should follow (an edge), or None
        self.hover_hit = None
        self.spin = 0.0                 # extra turn about the shape axis (polygons)
        self.rotation = None
        self.height = 0.0
        self.drag = None
        self.obj = None
        self.mod = None
        self.hover_target = None  # mesh under the cursor that could be cut
        self.target = None        # mesh the anchor was placed on
        self.cut_wanted = False   # C/U/I pressed before the anchor click
        self.cut = False
        self.cut_op = cuts.DIFFERENCE  # what the shape does to the target: cut, add or intersect
        self.cut_mod = None

    # -- to implement ------------------------------------------------------------

    def _create_object(self, context, matrix):
        raise NotImplementedError

    def _size_states(self):
        """The steps between placing the anchor and the height, in order."""
        return (self.SIZE_STATE,)

    def _start_size(self):
        """Called when the anchor is placed or reoriented, and when a size step starts."""

    def extra_key(self, context, key):
        """Handle the keys of a specific tool (A, R). Return True if the key was used."""
        return False

    def _flags(self):
        """Short status words shown next to the cursor and in the header."""
        return []

    def _update_size(self, context):
        raise NotImplementedError

    def _size_ready(self):
        raise NotImplementedError

    def _cut_live(self):
        """True once the shape has volume (a flat one confuses the boolean solver)."""
        raise NotImplementedError

    def _draw_anchor(self, ov, size, color):
        ov.circle(self.center, self.normal, size, color)

    def _draw_size(self, ov):
        """Fill the overlay for the size step; return the header text."""
        raise NotImplementedError

    def _draw_height(self, ov):
        raise NotImplementedError

    def _update_final(self, context):
        raise NotImplementedError

    def _draw_final(self, ov):
        raise NotImplementedError

    # -- input ---------------------------------------------------------------

    def key(self, context, key):
        if key in _CUT_KEYS:
            op = _CUT_KEYS[key]
            if self.state == 'CENTER':
                self.cut_wanted = not (self.cut_wanted and self.cut_op == op)
                self.cut_op = op
            elif self.target is None:
                self.message = "Nothing to cut: the anchor is not on a mesh"
            elif self.cut and self.cut_op == op:
                self.cut = False  # the same key again turns it off
            else:
                self.cut, self.cut_op = True, op
            return True
        if key not in AXES:
            return self.extra_key(context, key)
        if self.state not in ('CENTER', self._size_states()[0]):
            self.message = "Axis can only be locked before the first size"
            return True
        self.axis_lock = None if self.axis_lock == key else key
        if self.state != 'CENTER':
            self._reorient()
        return True

    def _locked(self, normal):
        """The lock axis, pointing to the same side as ``normal``; or ``normal``."""
        if self.axis_lock is None:
            return normal
        axis = AXES[self.axis_lock]
        return axis if axis.dot(normal) >= 0.0 else -axis

    # -- stages ----------------------------------------------------------------

    def update(self, context):
        if self.state == 'CENTER':
            self._hover(context)
        elif self.state == 'HEIGHT':
            self._update_height(context)
        elif self.state == self.FINAL_STATE:
            self._update_final(context)
        else:
            self._update_size(context)
        self._sync_cut()
        self._refresh(context)

    def _hover(self, context):
        self.origin_mode = box_origin_mode(context)
        hit, inference = self.pick(context)
        self.snapped = False
        self.hover_target = hit.obj if hit is not None and cuts.can_cut(hit.obj) else None
        if inference is not None and inference.obj is None:  # the 3D cursor: wherever it is, in the air too
            loc, nor = inference.point, inference.normal
            self.snapped, self.inference = True, inference.kind
        elif hit is not None:
            loc, nor = hit.location, hit.normal
            capsnap = None if (inference is not None or self.alt) else cap_center_snap(hit.obj, loc, nor)
            if inference is not None:
                loc, nor = inference.point, inference.normal
                self.snapped, self.inference = True, inference.kind
            elif capsnap is not None:
                loc, nor = capsnap
                self.snapped, self.inference = True, "Cap center"
            else:
                self.inference = f"On {hit.obj.name}"
        else:
            origin, direction = self.ray()
            ground = ground_hit(origin, direction)
            if ground is None:
                loc, nor = origin + direction * self.rv3d.view_distance, Z.copy()
            else:
                loc, nor = ground
            if self.ctrl:
                step = snap_step(self.rv3d, self.units)
                loc = Vector([snap(c, step) for c in loc])
                self.snapped = True
            self.inference = "On ground"
        self.hover_hit = hit
        self.center, self.surface_normal = loc, nor
        self.normal = self._locked(nor)

    def _frame(self):
        """Rotation of the shape's frame: Z along the axis, X along ``x_hint`` if there is one."""
        if self.x_hint is not None:
            rotation = orientation_from_axes(self.normal, self.x_hint)
            if rotation is not None:
                return rotation
        return orientation_for_normal(self.normal)

    def _place(self):
        self.base_rotation = self._frame()
        self.rotation = self.base_rotation @ Quaternion(Z, self.spin) if self.spin else self.base_rotation
        origin = self.center
        if self.origin_3d:  # halfway up the shape: it follows the height
            origin = origin + (self.rotation @ Z) * (self.height * 0.5)
        return Matrix.LocRotScale(origin, self.rotation, None)

    def _confirm_center(self, context):
        self.target = self.hover_target
        self.cut = (self.cut_wanted or self.ctrl) and self.target is not None
        if self.ctrl and not self.cut_wanted:
            self.cut_op = cuts.DIFFERENCE  # Ctrl+click always cuts a hole
        self.spin = 0.0
        self.origin_3d = self.ROUND and box_origin_mode(context) == 'CENTER3D'
        self.obj = self._create_object(context, self._place())
        self.mod = cn.find_modifier(self.obj)
        self.state = self._size_states()[0]
        self._start_size()

    def _reorient(self):
        """Apply a changed axis lock to the shape that is already placed."""
        self.normal = self._locked(self.surface_normal)
        self.spin = 0.0
        self.obj.matrix_world = self._place()
        self._start_size()

    def _reject(self):
        """Ignore snap points on the hole this very cutter makes."""
        if self.cut and self.obj is not None:
            return lambda p: cuts.inside_cutter(self.obj, p)
        return None

    def _update_height(self, context):
        origin, direction = self.ray()
        measured = closest_on_axis(origin, direction, self.center, self.normal)
        value = self.drag.update(self.region, self.rv3d, self.mouse, measured,
                                 self.center + self.normal * self.drag.value, self.normal)
        cutter_reject = self._reject()
        flat = pixel_size(self.region, self.rv3d, self.center)  # one pixel: points on the base plane

        def reject(point):
            # A snap to height 0 would make a flat shape and is never wanted. It also is what sticks to the many
            # rim points of the face a cutter is drawn on, so a cursor over that face could not pull it down.
            return abs((point - self.center).dot(self.normal)) < flat or (cutter_reject is not None and cutter_reject(point))

        _hit, inference = self.pick(context, (self.obj,), reject)
        if inference is not None:
            value = (inference.point - self.center).dot(self.normal)
        self.height = self.apply_input(value, snapped_ok=inference is None)
        cn.set_params(self.obj, self.mod, height=self.height)
        if self.origin_3d:
            self.obj.matrix_world = self._place()

    def _sync_cut(self):
        """Keep the Boolean in step with the cut toggle and the cutter's size."""
        if self.obj is None:
            return
        if self.cut and self.cut_mod is None:
            self.cut_mod = cuts.add_cut(self.target, self.obj, self.cut_op)
        elif not self.cut and self.cut_mod is not None:
            cuts.remove_cut(self.obj)
            self.cut_mod = None
        if self.cut_mod is not None:
            if self.cut_mod.operation != self.cut_op:
                self.cut_mod.operation = self.cut_op
            live = self._cut_live()
            if self.cut_mod.show_viewport != live:
                self.cut_mod.show_viewport = live

    def confirm(self, context):
        if self.state == 'CENTER':
            self._confirm_center(context)
            return 'RUNNING'
        if self.state == 'HEIGHT' and self.FINAL_STATE:
            self.state = self.FINAL_STATE
            self.drag = Drag1D(0.0, offset=0.0)
            return 'RUNNING'
        if self.state not in ('HEIGHT', self.FINAL_STATE):
            if not self._size_ready():
                return 'RUNNING'
            states = self._size_states()
            following = states[states.index(self.state) + 1:]
            if following:
                self.state = following[0]
                self._enter_size_state()
            else:
                self.drag = Drag1D(0.0)  # height starts at 0 wherever the cursor is
                self.state = 'HEIGHT'
            return 'RUNNING'
        return 'FINISHED'

    def cancel(self, context):
        if self.obj is not None:
            cuts.remove_cut(self.obj)
            cn.remove_object(self.obj)
            self.obj = self.cut_mod = None

    # -- feedback -----------------------------------------------------------------

    def _enter_size_state(self):
        """A later size step starts (after the previous one was confirmed)."""

    def _cut_text(self):
        if self.state == 'CENTER':
            if not (self.cut_wanted or self.ctrl):
                return ""
            verb = cuts.VERBS[self.cut_op if self.cut_wanted else cuts.DIFFERENCE]
            return f"{verb} {self.hover_target.name}" if self.hover_target else "No mesh here to cut"
        return f"{cuts.VERBS[self.cut_op]} {self.target.name}" if self.cut else ""

    def _draw_axis_lock(self, origin):
        if self.axis_lock is None:
            return
        axis = AXES[self.axis_lock]
        reach = pixel_size(self.region, self.rv3d, origin) * 400.0
        self.overlay.line(origin - axis * reach, origin + axis * reach, axis_color(axis), 1.5)

    def _refresh(self, context):
        ov = self.overlay
        ov.clear()
        lock = f"Axis: {self.axis_lock}" if self.axis_lock else ""
        cut = self._cut_text()
        words = [lock, cut, *self._flags()]
        flags = "   ".join(t for t in words if t)
        self.flags = f"   [{flags}]" if flags else ""
        if self.state == 'CENTER':
            size = pixel_size(self.region, self.rv3d, self.center) * 14.0
            color = SNAP_COLOR if self.snapped else POINT_COLOR
            ov.point(self.center, color)
            self._draw_anchor(ov, size, color)
            ov.line(self.center, self.center + self.normal * size, axis_color(self.normal))
            self._draw_axis_lock(self.center)
            shown = [t for t in ((self.inference if self.snapped else ""), *words) if t]
            ov.label = ("   ".join(shown), False) if shown else None
            self.header(context, f"Click the {self.anchor_name}   ({self.inference}){self.flags}")
        elif self.state == 'HEIGHT':
            if self.infer_point is not None:
                ov.point(self.infer_point, SNAP_COLOR)
            self.header(context, f"{self._draw_height(ov)}{self.flags}")
        elif self.state == self.FINAL_STATE:
            if self.infer_point is not None:
                ov.point(self.infer_point, SNAP_COLOR)
            self.header(context, f"{self._draw_final(ov)}{self.flags}")
        else:
            ov.point(self.center, POINT_COLOR)
            self._draw_axis_lock(self.center)
            if self.infer_point is not None:
                ov.point(self.infer_point, SNAP_COLOR)
            self.header(context, f"{self._draw_size(ov)}{self.flags}")

    anchor_name = "center"


class CylinderCreate(ShapeCreate):
    """Click center -> click radius -> click height. ``polygon`` makes a flat-shaded prism
    whose first vertex follows the cursor (set the sides with the wheel)."""

    def __init__(self, context, mouse, segments=32, polygon=False):
        super().__init__(context, mouse, segments)
        self.polygon = polygon
        self.title = "EZ Polygon" if polygon else "EZ Cylinder"
        if polygon:
            self.status_text = POLYGON_STATUS_TEXT
        self.segments_label = "Sides" if polygon else "Segments"
        self.radial_dir = None
        self.radius = 0.0

    def set_segments(self, value):
        super().set_segments(value)
        if self.obj is not None:
            cn.set_params(self.obj, self.mod, segments=self.segments)

    ROUND = True

    def _create_object(self, context, matrix):
        return cn.create_cylinder(context, matrix, 0.0, 0.0, self.segments, smooth=not self.polygon,
                                  name="EZ Polygon" if self.polygon else "EZ Cylinder", centered_z=self.origin_3d)

    def extra_key(self, context, key):
        if key != 'O' or not self.ROUND:
            return False
        if self.state != 'CENTER':
            self.message = "The origin is chosen with the first click"
        else:  # round shapes have no corner: Center (3D), or the center of the base
            self.origin_mode = 'PLANE' if self.origin_mode == 'CENTER3D' else 'CENTER3D'
            context.window_manager.ezhs_box_origin = self.origin_mode
            self.message = "Origin: Center (3D)" if self.origin_mode == 'CENTER3D' else "Origin: center of the base"
        return True

    def _flags(self):
        return ["Origin: Center (3D)"] if (self.origin_3d if self.state != 'CENTER' else
                                           self.origin_mode == 'CENTER3D') and self.ROUND else []

    def _start_size(self):
        self.radial_dir = self.rotation @ X
        self.drag = Drag1D(0.0, offset=0.0)  # radius = distance to the cursor

    def _update_size(self, context):
        origin, direction = self.ray()
        measured = None
        hit = radial_on_plane(origin, direction, self.center, self.center, self.normal)
        if hit is not None:
            measured, radial = hit
            if radial is not None:
                self.radial_dir = radial
        value = self.drag.update(self.region, self.rv3d, self.mouse, measured,
                                 self.center + self.radial_dir * self.drag.value, self.radial_dir)
        _hit, inference = self.pick(context, (self.obj,), self._reject())
        if inference is not None:
            rel = inference.point - self.center
            radial = rel - self.normal * rel.dot(self.normal)
            value = radial.length
            if value > 1e-9:
                self.radial_dir = radial / value
        self.radius = abs(self.apply_input(value, snapped_ok=inference is None))
        cn.set_params(self.obj, self.mod, radius=self.radius)
        if self.polygon:
            self._spin_to(self.radial_dir)

    def _spin_to(self, direction):
        """Turn the polygon about its axis so a vertex points along ``direction``."""
        ex, ey = self.base_rotation @ X, self.base_rotation @ Y
        self.spin = math.atan2(direction.dot(ey), direction.dot(ex))
        self.obj.matrix_world = self._place()

    def _size_ready(self):
        return self.radius > 1e-6

    def _cut_live(self):
        return self.radius > 1e-6 and abs(self.height) > 1e-6

    def _draw_size(self, ov):
        ov.line(self.center, self.center + self.radial_dir * self.radius, GUIDE_COLOR)
        ov.label = self.label("Radius", self.radius)
        return f"Radius: {format_length(self.radius, self.units)}"

    def _draw_height(self, ov):
        top = self.center + self.normal * self.height
        ov.circle(self.center, self.normal, self.radius, GUIDE_COLOR, 1.0)
        ov.line(self.center, top, axis_color(self.normal))
        ov.point(top, POINT_COLOR)
        ov.label = self.label("Height", self.height)
        return (f"Radius: {format_length(self.radius, self.units)}   "
                f"Height: {format_length(self.height, self.units)}")


class ConeCreate(CylinderCreate):
    """Click center -> click the base radius -> click the height -> click the top radius.

    The top radius is the cursor's distance from the axis; within a few pixels of the axis it snaps to 0
    (a pointed cone, labelled Apex). Type a number for an exact value.
    """

    FINAL_STATE = 'TOP'
    title = "EZ Cone"

    def __init__(self, context, mouse, segments=32):
        super().__init__(context, mouse, segments)
        self.title = "EZ Cone"
        self.top_radius = 0.0

    def _create_object(self, context, matrix):
        return cn.create_cone(context, matrix, 0.0, 0.0, 0.0, self.segments, centered_z=self.origin_3d)

    def _top_center(self):
        return self.center + self.normal * self.height

    def _update_final(self, context):
        """Top radius: the distance from the axis, measured on the plane of the top."""
        top = self._top_center()
        origin, direction = self.ray()
        measured = None
        hit = radial_on_plane(origin, direction, top, top, self.normal)
        if hit is not None:
            measured, radial = hit
            if radial is not None:
                self.radial_dir = radial
        value = self.drag.update(self.region, self.rv3d, self.mouse, measured,
                                 top + self.radial_dir * self.drag.value, self.radial_dir)
        _hit, inference = self.pick(context, (self.obj,), self._reject())
        # Near the axis the cursor means "pointed", even when some other geometry happens to lie behind it.
        apex = not self.alt and abs(value) < pixel_size(self.region, self.rv3d, top) * self.snap_px * 0.7
        if apex:
            value, inference = 0.0, None
            self.infer_kind, self.infer_point = "Apex", None
        elif inference is not None:
            rel = inference.point - top
            radial = rel - self.normal * rel.dot(self.normal)
            value = radial.length
            if value > 1e-9:
                self.radial_dir = radial / value
        self.top_radius = abs(self.apply_input(value, snapped_ok=inference is None))
        cn.set_params(self.obj, self.mod, topradius=self.top_radius)

    def _circles(self, ov, base_color, top_color):
        top = self._top_center()
        ov.circle(self.center, self.normal, self.radius, base_color, 1.0)
        if self.top_radius > 1e-9:
            ov.circle(top, self.normal, self.top_radius, top_color, 1.0)
        for angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
            u = Quaternion(self.normal, angle) @ self.radial_dir
            ov.line(self.center + u * self.radius, top + u * self.top_radius, GUIDE_COLOR, 1.0)

    def _draw_height(self, ov):
        self._circles(ov, GUIDE_COLOR, GUIDE_COLOR)
        top = self._top_center()
        ov.line(self.center, top, axis_color(self.normal))
        ov.point(top, POINT_COLOR)
        ov.label = self.label("Height", self.height)
        return (f"Radius: {format_length(self.radius, self.units)}   "
                f"Height: {format_length(self.height, self.units)}")

    def _draw_final(self, ov):
        self._circles(ov, GUIDE_COLOR, HILITE_COLOR)
        top = self._top_center()
        ov.line(top, top + self.radial_dir * self.top_radius, GUIDE_COLOR)
        ov.point(top, POINT_COLOR)
        ov.label = self.label("Top radius", self.top_radius)
        return (f"Top radius: {format_length(self.top_radius, self.units)}   "
                f"(radius {format_length(self.radius, self.units)}, height {format_length(self.height, self.units)})")


class SphereCreate(CylinderCreate):
    """Click the center -> click the radius. The sphere's Z axis follows the surface normal (or the locked axis).

    Like the cylinder's radius step the radius is the cursor's distance from the center, measured on the plane
    through the center, so a sphere resting its center on a face has the face's circle as its equator.
    """

    title = "EZ Sphere"
    ROUND = False  # its origin is already the center

    def __init__(self, context, mouse, segments=32):
        super().__init__(context, mouse, segments)
        self.title = "EZ Sphere"

    def _create_object(self, context, matrix):
        return cn.create_sphere(context, matrix, 0.0, self.segments)

    def _cut_live(self):
        return self.radius > 1e-6

    def confirm(self, context):
        if self.state == 'CENTER':
            return super().confirm(context)
        return 'FINISHED' if self._size_ready() else 'RUNNING'

    def _draw_size(self, ov):
        ov.circle(self.center, self.normal, self.radius, GUIDE_COLOR, 1.0)
        return super()._draw_size(ov)


class QuadSphereCreate(SphereCreate):
    """The sphere tool, making a quad sphere (a cube pushed onto a sphere: no poles)."""

    title = "EZ Quad Sphere"

    def __init__(self, context, mouse, segments=32):
        super().__init__(context, mouse, segments)
        self.title = "EZ Quad Sphere"

    def _create_object(self, context, matrix):
        return cn.create_quadsphere(context, matrix, 0.0, self.segments)


class BoxCreate(ShapeCreate):
    """Click a corner -> click the opposite corner (or type ``w;d``) -> click the height.

    On a face the box lines up with the face's nearest edge (``A`` toggles that).
    ``R`` switches to a *rotated rectangle*: the corner, then one edge (direction
    and length, typed as ``length;angle``), then the depth, then the height.

    The option *Draw start: Center* (``M`` before the first click) draws it like a cylinder instead: the first
    click is the center of the base, which starts as a square that grows with the cursor; ``X`` or ``Y`` then
    makes only the width or only the depth follow it (the other side keeps its size, the same key again goes
    back to a square). Sizes, typed or dragged, are those of the whole base; the box object still starts at a
    corner of it, so everything else works the same.

    The option *Box origin* (``O`` before the first click cycles it) puts the object's origin at a corner
    (default), at the center of the base (*Plane*) or at the center of the whole box (*3D*), with either way of
    drawing, so Blender's own Rotate and Scale work around the middle.
    """

    SIZE_STATE = 'BASE'
    title = "EZ Box"
    status_text = BOX_STATUS_TEXT
    segments_label = None

    def __init__(self, context, mouse, segments=0):
        super().__init__(context, mouse, segments)
        self.centered = False   # Draw start: Center
        self.origin_mode = box_origin_mode(context)  # re-read until the first click, then fixed for this box
        self._read_options(context)
        self.dim_lock = None    # centered: 'X' / 'Y' = only that side follows the cursor, the other keeps ``frozen``
        self.frozen = (0.0, 0.0)
        self.width = 0.0
        self.depth = 0.0
        self.drag_u = self.drag_v = None
        self.align = True       # A: follow the nearest edge of the face under the cursor
        self.rotated = False    # R: rotated rectangle (an EDGE step before the depth)
        self.edge = None        # the edge the box aligns to while hovering
        self.auto_hint = None   # direction of that edge (the X axis of an auto-aligned box)
        self.ref_x = X.copy()   # X axis before any rotation (angles are measured from it)
        self.angle = 0.0        # direction of the first edge, from ref_x (rotated mode)

    def _read_options(self, context):
        self.centered = box_from_center(context)
        self.origin_mode = box_origin_mode(context)

    @property
    def origin_centered(self):
        return self.origin_mode != 'CORNER'

    @property
    def anchor_name(self):
        return "center" if self.centered else "corner"

    def _create_object(self, context, matrix):
        return cn.create_box(context, matrix, 0.0, 0.0, 0.0, centered=self.origin_mode != 'CORNER',
                             centered_z=self.origin_mode == 'CENTER3D')

    def _axes(self):
        return self.rotation @ X, self.rotation @ Y

    def _place(self):
        """The object's matrix: its origin is a corner of the base, or the center of it with that option."""
        matrix = super()._place()
        ex, ey = self._axes()
        corner = self.center
        if self.centered:  # the base stays centered on the click
            corner = corner - ex * (self.width * 0.5) - ey * (self.depth * 0.5)
        if self.origin_mode != 'CORNER':
            corner = corner + ex * (self.width * 0.5) + ey * (self.depth * 0.5)
        if self.origin_mode == 'CENTER3D':
            corner = corner + (self.rotation @ Z) * (self.height * 0.5)
        matrix.translation = corner
        return matrix

    def _update_height(self, context):
        super()._update_height(context)
        if self.origin_mode == 'CENTER3D':  # the origin is halfway up: it follows the height
            self.obj.matrix_world = self._place()

    def _size_states(self):
        return ('EDGE', 'BASE') if self.rotated else ('BASE',)

    # -- alignment to the edges of the face under the cursor --------------------------

    def _hover(self, context):
        super()._hover(context)
        self._read_options(context)
        self.edge = self.auto_hint = None
        hit = self.hover_hit
        if self.align and not self.alt and hit is not None:
            edge = find_edge(context, self.region, self.rv3d, self.mouse, hit)
            if (edge is not None and edge.corners <= ALIGN_MAX_CORNERS
                    and orientation_from_axes(self.normal, edge.direction) is not None):
                self.edge, self.auto_hint = edge, edge.direction
        self.x_hint = self.auto_hint
        if (self.inference == KIND_CURSOR and self.snapped and self.cursor_x is not None
                and orientation_from_axes(self.normal, self.cursor_x) is not None):
            self.edge = None
            self.x_hint = self.cursor_x  # a box drawn on the 3D cursor lines up with the cursor's axes

    def extra_key(self, context, key):
        if key == 'A':
            self.align = not self.align
            if self.state != 'CENTER':
                self.message = f"Alignment is chosen when the {self.anchor_name} is placed"
            return True
        if key == 'O':
            if self.state != 'CENTER':
                self.message = "The origin is chosen with the first click"
            else:
                order = [mode[0] for mode in ORIGIN_MODES]
                self.origin_mode = order[(order.index(self.origin_mode) + 1) % len(order)]
                context.window_manager.ezhs_box_origin = self.origin_mode
                self.message = f"Box origin: {dict((m[0], m[1]) for m in ORIGIN_MODES)[self.origin_mode]}"
            return True
        if key == 'M':
            if self.state != 'CENTER':
                self.message = f"Draw start is chosen with the first click ({self.anchor_name} now)"
            else:
                wanted = not box_from_center(context)
                context.window_manager.ezhs_box_start = 'CENTER' if wanted else 'CORNER'
                self._read_options(context)
                self.message = "Draw start: Center" if wanted else "Draw start: Corner"
            return True
        if key == 'R':
            self.rotated = not self.rotated
            if self.state not in ('CENTER', 'HEIGHT'):
                self._restart_size()
            return True
        return False

    def key(self, context, key):
        if self.centered and key in ('X', 'Y') and self.state in ('BASE', 'EDGE'):
            # From the center, X/Y while sizing pick the side that grows (the axis lock is for before the click).
            if self.rotated:
                self.message = "X/Y: in a rotated rectangle the first step sets the width"
            else:
                self.dim_lock = None if self.dim_lock == key else key
                self.frozen = (self.width, self.depth)
            return True
        return super().key(context, key)

    def _flags(self):
        words = []
        if self.origin_mode != 'CORNER':
            words.append("Origin: " + dict((m[0], m[1]) for m in ORIGIN_MODES)[self.origin_mode])
        if self.centered:
            words.append("Draw start: Center")
            if self.dim_lock and self.state == 'BASE':
                words.append("Width only" if self.dim_lock == 'X' else "Depth only")
        if self.rotated:
            words.append("Rotated")
        if self.state == 'CENTER' and self.edge is not None:
            words.append("Align: edge")
        elif self.state == 'CENTER' and not self.align:
            words.append("Align: off")
        return words

    # -- size steps ----------------------------------------------------------------

    def _start_size(self):
        self.dim_lock = None
        self.drag_u = Drag1D(0.0, offset=0.0)
        self.drag_v = Drag1D(0.0, offset=0.0)
        self.ref_x = self.rotation @ X
        self.angle = 0.0

    def _restart_size(self):
        """R was pressed while sizing: go back to the first size step in the other mode."""
        self.state = self._size_states()[0]
        self.x_hint = self.auto_hint
        self.width = self.depth = 0.0
        self.obj.matrix_world = self._place()
        self._start_size()
        cn.set_params(self.obj, self.mod, width=0.0, depth=0.0)

    def _enter_size_state(self):
        if self.state == 'BASE':  # after the first edge: its direction is the box's X axis
            self.x_hint = self._edge_direction()
            self.obj.matrix_world = self._place()
            self.drag_v = Drag1D(0.0, offset=0.0)
            self.depth = 0.0

    def _edge_direction(self):
        return Quaternion(self.normal, self.angle) @ self.ref_x

    def _typed_pair(self):
        """(width, depth) typed so far; a single value makes a square; None parts are not typed yet."""
        parts = self.num.parts(self.units)
        if not parts:
            return None
        if len(parts) == 1 and self.centered and not self.rotated and self.dim_lock:
            return (parts[0], None) if self.dim_lock == 'X' else (None, parts[0])  # just the side X/Y picked
        return (parts[0], parts[0]) if len(parts) == 1 else (parts[0], parts[1])

    def _update_size(self, context):
        origin, direction = self.ray()
        _hit, inference = self.pick(context, (self.obj,), self._reject())
        point = ray_plane_point(origin, direction, self.center, self.normal)
        if inference is not None:
            point = inference.point
        if self.state == 'EDGE':
            self._update_edge(point, inference is None)
        else:
            self._update_base(origin, direction, point, inference)

    def _update_edge(self, point, free):
        """First edge of a rotated rectangle: its direction and length follow the cursor."""
        if point is not None:
            rel = point - self.center
            rel -= self.normal * rel.dot(self.normal)
            if rel.length > 1e-9:
                self.angle = signed_angle(self.ref_x, rel, self.normal)
                length = rel.length * (2.0 if self.centered else 1.0)  # from the center: the cursor is half of it
            else:
                length = self.width
            if free and self.ctrl:
                length = snap(length, snap_step(self.rv3d, self.units))
                self.angle = round(self.angle / ANGLE_STEP) * ANGLE_STEP
        else:
            length = self.width
        typed = self.num.raw_parts()
        if typed:
            typed_length = self.num.parts(self.units)[0]
            length = typed_length if typed_length is not None else length
            if len(typed) > 1:
                try:
                    self.angle = math.radians(parse_angle(typed[1]))
                except ValueError:
                    pass
        self.width = abs(length)
        self.x_hint = self._edge_direction()
        self.obj.matrix_world = self._place()
        cn.set_params(self.obj, self.mod, width=self.width, depth=0.0)

    def _update_base(self, origin, direction, point, inference):
        if self.centered:
            self._update_base_centered(origin, direction, point, inference)
            return
        ex, ey = self._axes()
        mu = mv = None
        if point is not None:
            rel = point - self.center
            mu, mv = rel.dot(ex), rel.dot(ey)
        u = self.drag_u.update(self.region, self.rv3d, self.mouse, mu,
                               self.center + ex * self.drag_u.value, ex)
        v = self.drag_v.update(self.region, self.rv3d, self.mouse, mv,
                               self.center + ey * self.drag_v.value, ey)
        if inference is not None and point is not None:
            u, v = mu, mv
        elif self.ctrl:
            step = snap_step(self.rv3d, self.units)
            u, v = snap(u, step), snap(v, step)
        typed = self._typed_pair()
        if self.rotated:  # the width is the edge that was just drawn
            u = self.width
            if typed is not None and typed[0] is not None:
                v = typed[0]
        elif typed is not None:
            u = typed[0] if typed[0] is not None else u
            v = typed[1] if typed[1] is not None else v
        self.width, self.depth = u, v
        self.obj.matrix_world = self._place()  # (the origin follows the size when it is the center of the base)
        cn.set_params(self.obj, self.mod, width=u, depth=v)

    def _update_base_centered(self, origin, direction, point, inference):
        """The base grows from its center: twice the cursor's distance along each side, a square at first."""
        ex, ey = self._axes()
        mu = mv = None
        if point is not None:
            rel = point - self.center
            mu, mv = rel.dot(ex), rel.dot(ey)
        u = self.drag_u.update(self.region, self.rv3d, self.mouse, mu,
                               self.center + ex * self.drag_u.value, ex)
        v = self.drag_v.update(self.region, self.rv3d, self.mouse, mv,
                               self.center + ey * self.drag_v.value, ey)
        snapped = inference is not None and point is not None
        if snapped:
            u, v = mu, mv
        w, d = abs(u) * 2.0, abs(v) * 2.0
        if self.ctrl and not snapped:
            step = snap_step(self.rv3d, self.units)
            w, d = snap(w, step), snap(d, step)
        typed = self._typed_pair()
        if self.rotated:  # the width is the side that was just drawn
            w = self.width
            if typed is not None and typed[0] is not None:
                d = typed[0]
        else:
            if self.dim_lock == 'X':
                d = self.frozen[1]
            elif self.dim_lock == 'Y':
                w = self.frozen[0]
            elif not snapped:
                w = d = max(w, d)  # a square until X or Y picks a side (a snap point is the corner itself)
            if typed is not None:
                w = typed[0] if typed[0] is not None else w
                d = typed[1] if typed[1] is not None else d
        self.width, self.depth = w, d
        self.obj.matrix_world = self._place()
        cn.set_params(self.obj, self.mod, width=w, depth=d)

    def _size_ready(self):
        if self.state == 'EDGE':
            return self.width > 1e-6
        return abs(self.width) > 1e-6 and abs(self.depth) > 1e-6

    def _cut_live(self):
        return abs(self.width) > 1e-6 and abs(self.depth) > 1e-6 and abs(self.height) > 1e-6

    # -- feedback -------------------------------------------------------------------

    def _corners(self, lift=0.0):
        ex, ey = self._axes()
        base = self.center + self.normal * lift
        if self.centered:
            hw, hd = ex * (self.width * 0.5), ey * (self.depth * 0.5)
            return [base - hw - hd, base + hw - hd, base + hw + hd, base - hw + hd]
        a = base + ex * self.width
        return [base, a, a + ey * self.depth, base + ey * self.depth]

    def _outline(self, ov, corners, color, width=2.0):
        for i, c in enumerate(corners):
            ov.line(c, corners[(i + 1) % 4], color, width)

    def _draw_anchor(self, ov, size, color):
        # a small square: where the corner (or center) of the box will be, turned like the box will be
        self.rotation = self._frame()
        ex, ey = self._axes()
        r = size * 0.7
        pts = [self.center + ex * a + ey * b for a, b in ((-r, -r), (r, -r), (r, r), (-r, r))]
        self._outline(ov, pts, color)
        if self.edge is not None:
            ov.line(self.edge.a, self.edge.b, ALIGN_COLOR, 3.0)

    def _angle_text(self):
        return f"{math.degrees(self.angle):.1f}\N{DEGREE SIGN}"

    def _draw_size(self, ov):
        ex, ey = self._axes()
        c = self._corners()
        size = f"{format_length(self.width, self.units)} x {format_length(self.depth, self.units)}"
        if self.state == 'EDGE':
            reach = max(self.width * (0.5 if self.centered else 1.0), 1e-9)
            ov.line(self.center, self.center + self.ref_x * reach, (0.7, 0.7, 0.7, 0.6), 1.0)
            ov.line(c[0], c[1], ALIGN_COLOR, 3.0)
            if self.num.active:
                ov.label = f"Length;Angle = {self.num.text}_", True
            else:
                text = f"{format_length(self.width, self.units)}  {self._angle_text()}"
                ov.label = (f"{text}   ({self.infer_kind})" if self.infer_kind else text), False
            return f"Edge: {format_length(self.width, self.units)} at {self._angle_text()}"
        ov.line(c[0], c[1], axis_color(ex))
        ov.line(c[0], c[3], axis_color(ey))
        ov.line(c[1], c[2], GUIDE_COLOR)
        ov.line(c[3], c[2], GUIDE_COLOR)
        if self.num.active:
            ov.label = (f"Depth = {self.num.text}_" if self.rotated else f"Size = {self.num.text}_"), True
        else:
            ov.label = (f"{size}   ({self.infer_kind})" if self.infer_kind else size), False
        return f"Size: {size}"

    def _draw_height(self, ov):
        base, top = self._corners(), self._corners(self.height)
        self._outline(ov, base, GUIDE_COLOR, 1.0)
        self._outline(ov, top, GUIDE_COLOR, 1.0)
        for b, t in zip(base, top):
            ov.line(b, t, GUIDE_COLOR, 1.0)
        ov.line(self.center, self.center + self.normal * self.height, axis_color(self.normal))
        ov.point(self.center + self.normal * self.height, POINT_COLOR)
        ov.label = self.label("Height", self.height)
        return (f"Size: {format_length(self.width, self.units)} x {format_length(self.depth, self.units)}   "
                f"Height: {format_length(self.height, self.units)}")


_FACE_NAMES = {v: k for k, v in FACES.items()}


def classify_hit(obj, mod, loc, nor):
    """Which part of an EZ shape was hit: a face part, or SIDE/TOPR for a round shape's side."""
    mw = obj.matrix_world
    n_local = (mw.to_3x3().transposed() @ nor).normalized()
    kind, params = cn.kind_of(mod), cn.get_params(mod)
    if kind in cn.SPHERE_KINDS:
        return SIDE
    if kind in (cn.KIND_CYLINDER, cn.KIND_CONE):
        z = (mw.inverted_safe() @ loc).z + cn.origin_offset(kind, params).z  # measured from the base
        # A cone's slanted side has a vertical normal component, so its caps need a stricter test.
        if abs(n_local.z) > (0.7 if kind == cn.KIND_CYLINDER else 0.97):
            return 'TOP' if abs(z - params["Height"]) <= abs(z) else 'BOTTOM'
        if kind == cn.KIND_CONE and abs(z - params["Height"]) < abs(z):
            return TOPR  # the upper half of a cone's side drags the top radius
        return SIDE
    axis = max(range(3), key=lambda i: abs(n_local[i]))
    size = params[cn.DIMENSIONS[kind][axis]]
    # The face looking along +axis is the far face unless the size is negative.
    end = 1 if (n_local[axis] > 0) == (size >= 0) else 0
    return _FACE_NAMES[(axis, end)]


class ShapePushPull(ModalController):
    """Drag a face along its axis (a size), or a cylinder's side outward (the radius)."""

    title = "EZ Push/Pull"

    def __init__(self, context, mouse, obj, mod, hit_loc, hit_nor, part=None):
        frame = frame_of(obj, mod)
        super().__init__(context, mouse, frame.params.get("Segments", 0))
        self.segments_label = "Segments" if "Segments" in frame.params else None
        self.obj, self.mod, self.frame0 = obj, mod, frame
        self.params0 = frame.params
        self.matrix0 = obj.matrix_world.copy()
        self.base0 = frame.base
        self.corner0 = corner_base(frame)  # where the faces are measured from (the origin, unless it is centered)
        self.part = part or classify_hit(obj, mod, hit_loc, hit_nor)
        self.hit = hit_loc.copy()
        self.pname = None
        self.size = 0.0
        self.shift = 0.0  # how far the origin moved along the axis (origin faces)
        self.radius = 0.0

        if self.part in (SIDE, TOPR):
            self.axis = frame.axes[2]
            self.scale = frame.scales[0]
            self.height_world = dimension(frame, 2)
            self.radius_name = "TopRadius" if self.part == TOPR else "Radius"
            self.radius = frame.params[self.radius_name] * self.scale
            if frame.kind == cn.KIND_CONE:
                # The radius is measured at the rim the handle sits on, wherever the side was grabbed.
                self.hit = self.corner0 + self.axis * (self.height_world if self.part == TOPR else 0.0)
            elif frame.kind in cn.SPHERE_KINDS:
                self.hit = self.base0  # the radius is measured on the plane through the center
            rel = hit_loc - self.base0
            radial = rel - self.axis * rel.dot(self.axis)
            self.radial_dir = radial.normalized() if radial.length > 1e-9 else frame.axes[0].copy()
            self.drag = Drag1D(self.radius)
        else:
            self.axis_index, self.face_end = FACES[self.part]
            self.axis = frame.axes[self.axis_index]
            self.scale = frame.scales[self.axis_index]
            self.pname = cn.DIMENSIONS[frame.kind][self.axis_index]
            self.size0 = self.size = dimension(frame, self.axis_index)
            self.face0 = face_center(frame, self.axis_index, self.face_end)
            self.value0 = (self.face0 - self.corner0).dot(self.axis)  # the far face's size, or 0
            # A box whose origin is the center of its base keeps it halfway along the sides it grows on X and Y.
            if frame.kind == cn.KIND_BOX:
                self.origin_halves = bool(frame.params.get(("Centered", "Centered", "CenteredZ")[self.axis_index]))
            else:  # cylinders and cones: only the height, with the origin halfway up
                self.origin_halves = bool(frame.params.get("CenteredZ"))
            self.drag = Drag1D(self.value0)

    @property
    def height(self):
        return self.size if self.pname == "Height" else None

    def set_segments(self, value):
        super().set_segments(value)
        if self.segments_label:
            cn.set_params(self.obj, self.mod, segments=self.segments)

    def _reject(self):
        """When dragging a cutter, ignore snap points on the hole it makes."""
        if cuts.is_cutter(self.obj):
            return lambda p: cuts.inside_cutter(self.obj, p)
        return None

    def update(self, context):
        origin, direction = self.ray()
        _hit, inference = self.pick(context, (self.obj,), self._reject())
        snapped_ok = inference is None
        if self.part in (SIDE, TOPR):
            measured = None
            hit = radial_on_plane(origin, direction, self.hit, self.base0, self.axis)
            if hit is not None:
                measured = hit[0]
            axial = (self.hit - self.base0).dot(self.axis)
            ref = self.base0 + self.axis * axial + self.radial_dir * self.drag.value
            value = self.drag.update(self.region, self.rv3d, self.mouse, measured, ref, self.radial_dir)
            if inference is not None:
                rel = inference.point - self.base0
                radial = rel - self.axis * rel.dot(self.axis)
                value = radial.length
                if value > 1e-9:
                    self.radial_dir = radial / value
            self.radius = abs(self.apply_input(value, snapped_ok))
            cn.set_params(self.obj, self.mod, **{self.radius_name.lower(): self.radius / self.scale})
        else:
            t = closest_on_axis(origin, direction, self.face0, self.axis)
            measured = None if t is None else self.value0 + t
            ref = self.face0 + self.axis * (self.drag.value - self.value0)
            value = self.drag.update(self.region, self.rv3d, self.mouse, measured, ref, self.axis)
            if inference is not None:
                value = (inference.point - self.corner0).dot(self.axis)
            if self.face_end == 1:
                self.size = self.apply_input(value, snapped_ok)
                self.shift = 0.0
            else:
                # The far face stays put: moving the near face changes size and location.
                self.size = self.apply_input(self.size0 - value, snapped_ok)
                self.shift = self.size0 - self.size
            # The origin moves with the near face; a centered origin sits halfway between the faces on its axes.
            delta = self.shift + ((self.size - self.size0) * 0.5 if self.origin_halves else 0.0)
            if delta != 0.0 or self.face_end == 0:
                matrix = self.matrix0.copy()
                matrix.translation = self.base0 + self.axis * delta
                self.obj.matrix_world = matrix
            cn.set_params(self.obj, self.mod, **{self.pname.lower(): self.size / self.scale})
        self._refresh(context)

    def confirm(self, context):
        return 'FINISHED'

    def cancel(self, context):
        self.obj.matrix_world = self.matrix0
        cn.set_params(self.obj, self.mod, **{k.lower(): v for k, v in self.params0.items()})

    def _face_outline(self, ov, center, color):
        """Outline of the dragged face, centered at ``center``."""
        frame = self.frame0
        if frame.kind in (cn.KIND_CYLINDER, cn.KIND_CONE):
            name = "TopRadius" if frame.kind == cn.KIND_CONE and self.face_end == 1 else "Radius"
            ov.circle(center, self.axis, frame.params[name] * frame.scales[0], color)
            return
        a, b = [i for i in range(3) if i != self.axis_index]
        ea, eb = frame.axes[a] * (dimension(frame, a) * 0.5), frame.axes[b] * (dimension(frame, b) * 0.5)
        pts = [center - ea - eb, center + ea - eb, center + ea + eb, center - ea + eb]
        for i, p in enumerate(pts):
            ov.line(p, pts[(i + 1) % 4], color)

    def _refresh(self, context):
        ov = self.overlay
        ov.clear()
        if self.infer_point is not None:
            ov.point(self.infer_point, SNAP_COLOR)
        if self.part in (SIDE, TOPR):
            base = self.corner0  # the center of the base (the origin, unless it is halfway up)
            top = base + self.axis * self.height_world
            if self.frame0.kind == cn.KIND_CONE:
                scale = self.frame0.scales[0]
                base_radius = self.radius if self.part == SIDE else self.frame0.params["Radius"] * scale
                top_radius = self.radius if self.part == TOPR else self.frame0.params["TopRadius"] * scale
                grab = base if self.part == SIDE else top
                ov.circle(base, self.axis, base_radius, HILITE_COLOR if self.part == SIDE else GUIDE_COLOR)
                ov.circle(top, self.axis, top_radius, HILITE_COLOR if self.part == TOPR else GUIDE_COLOR)
                ov.line(grab, grab + self.radial_dir * self.radius, GUIDE_COLOR)
            else:
                ov.circle(base, self.axis, self.radius, HILITE_COLOR)
                ov.circle(top, self.axis, self.radius, HILITE_COLOR)
                mid = (base + top) * 0.5
                ov.line(mid, mid + self.radial_dir * self.radius, GUIDE_COLOR)
            name = "Top radius" if self.part == TOPR else "Radius"
            ov.label = self.label(name, self.radius)
            self.header(context, f"{name}: {format_length(self.radius, self.units)}")
            return
        delta = (self.size - self.size0) if self.face_end == 1 else self.shift
        moving = self.face0 + self.axis * delta
        self._face_outline(ov, moving, HILITE_COLOR)
        ov.line(self.face0, moving, axis_color(self.axis))
        ov.point(moving, POINT_COLOR)
        ov.label = self.label(self.pname, self.size)
        self.header(context, f"{self.pname}: {format_length(self.size, self.units)}")


class ShapeRotate(ModalController):
    """Turn a shape about a pivot (the dial gizmo): by default about its own Z axis, pivoting on its origin.

    The shape follows the cursor around the pivot. A snap point under the cursor makes it face that point; an
    edge under the cursor makes one of its axes parallel to that edge. Ctrl rounds to 15 degrees, a typed
    number is degrees.

    ``X``/``Y``/``Z`` pick the axis to turn about (the world axis, press again for the shape's own axis, again
    to go back to its Z): that is how a shape is tilted. ``P`` picks the pivot with a click (a snap point, or
    the surface; ``N`` there makes the face's normal the axis), ``C`` pivots on the center of the shape and
    ``O`` on its origin again.

    When the turn about the current axis can make the shape's Z axis vertical, the turn snaps to that
    (``Vertical``) near it, and ``U`` jumps there: if the current axis cannot stand the shape up, ``U`` switches
    to the axis that can, turning about the chosen pivot (Alt: no snapping).
    """

    title = "EZ Rotate"
    status_text = ROTATE_STATUS_TEXT
    segments_label = None

    def __init__(self, context, mouse, obj, mod, axis_index=2):
        frame = frame_of(obj, mod)
        super().__init__(context, mouse, 0)
        self.obj, self.mod = obj, mod
        self.matrix0 = obj.matrix_world.copy()
        self.origin = frame.base.copy()
        self.center = self.matrix0 @ cn.center_local(frame.kind, frame.params)
        self.local_axes = [a.copy() for a in frame.axes]
        self.pivot = self.origin.copy()
        self.pivot_name = "Origin"
        self.state = 'ANGLE'  # 'PIVOT' while the pivot is being picked with a click
        self.axis_lock = None  # (key, 'WORLD' | 'LOCAL') or None for the shape's own Z
        self.use_normal = False  # N: the face under the pivot click becomes the axis
        self.axis_name = "local Z"
        self.axis = self.x0 = self.y0 = None
        self.set_axis(self.local_axes[2])
        if axis_index != 2:  # started from a tilt ring: that local axis is the axis
            key = "XYZ"[axis_index]
            self.axis_lock = (key, 'LOCAL')
            self.set_axis(self.local_axes[axis_index], f"local {key}")
        self.edge = None
        self.pick_point = self.pick_normal = None
        self.pick_kind = ""

    # -- axis and pivot ------------------------------------------------------------------

    def set_axis(self, axis, name=None):
        """Turn about ``axis`` from now on (an angle is measured from a direction that is square to it)."""
        self.axis = axis.normalized()
        if name:
            self.axis_name = name
        for ref in (*self.local_axes, X, Y, Z):
            perp = ref - self.axis * ref.dot(self.axis)
            if perp.length > 0.5:
                self.x0 = perp.normalized()
                break
        self.y0 = self.axis.cross(self.x0)
        self.restart()

    def restart(self):
        """Forget the turn so far: the next measurement is the new starting point."""
        self.grab = None     # angle of the cursor when the drag started (from x0)
        self.prev = None     # last measured cursor angle
        self.theta = 0.0     # how far the cursor went around the pivot, continuous
        self.delta = 0.0     # the rotation that is applied
        self.obj.matrix_world = self.matrix0

    def set_pivot(self, point, name):
        self.pivot, self.pivot_name = point.copy(), name
        self.restart()

    def key(self, context, key):
        if key in AXES:
            index = "XYZ".index(key)
            if self.axis_lock is None or self.axis_lock[0] != key:
                self.axis_lock = (key, 'WORLD')
                self.set_axis(AXES[key], f"world {key}")
            elif self.axis_lock[1] == 'WORLD':
                self.axis_lock = (key, 'LOCAL')
                self.set_axis(self.local_axes[index], f"local {key}")
            else:
                self.axis_lock = None
                self.set_axis(self.local_axes[2], "local Z")
            return True
        if key == 'P':
            self.state = 'ANGLE' if self.state == 'PIVOT' else 'PIVOT'
            self.press_mouse = None  # a button still held from the gizmo drag must not confirm the pick
            self.edge = None
            return True
        if key == 'O':
            self.state = 'ANGLE'
            self.set_pivot(self.origin, "Origin")
            return True
        if key == 'C':
            self.state = 'ANGLE'
            self.set_pivot(self.center, "Center")
            return True
        if key == 'U' and self.state == 'ANGLE':
            self._go_vertical()
            return True
        if key == 'N' and self.state == 'PIVOT':
            self.use_normal = not self.use_normal
            return True
        return False

    # -- vertical ------------------------------------------------------------------------

    def _vertical_turn(self, reference):
        """The turn about the current axis after which the shape's Z axis is vertical (nearest to ``reference``).

        None if no turn about this axis can do it: the axis must keep the angle to the vertical that Z has now.
        """
        axis, z, up = self.axis, self.local_axes[2], Z
        along = axis.dot(z)
        if abs(axis.dot(up) - along) > VERTICAL_TOLERANCE:
            return None
        here, there = z - axis * along, up - axis * along
        if here.length < 1e-6 or there.length < 1e-6:
            return None  # Z lies along the axis (nothing to turn), or the axis is the vertical itself
        return self._nearest(signed_angle(here, there, axis), reference)

    def _go_vertical(self):
        """U: stand the shape up now, about the current axis if that can do it, else about the axis that can."""
        z = self.local_axes[2]
        if z.dot(Z) > 1.0 - 1e-9:
            self.message = "Already vertical"
            return
        turn = self._vertical_turn(self.theta)
        if turn is None:
            axis = z.cross(Z)
            if axis.length < 1e-9:  # upside down: the shape's own X is as good as any axis across Z
                axis = self.local_axes[0]
            self.axis_lock = None
            self.set_axis(axis, "upright axis")
            turn = self._vertical_turn(0.0)
            self.message = "Axis: the one that stands it up"
        self.theta = turn  # the cursor goes on from here; the snap keeps it there while the cursor is near

    # -- measuring -----------------------------------------------------------------

    def _angle_of(self, point):
        return signed_angle(self.x0, point - self.pivot, self.axis)

    def _cursor_angle(self):
        """The cursor's angle about the axis, or None if it cannot be told (edge-on plane, cursor on the pivot)."""
        spot = location_3d_to_region_2d(self.region, self.rv3d, self.pivot)
        if spot is not None and (self.mouse - spot).length < self.snap_px * 0.5:
            return None
        origin, direction = self.ray()
        point = ray_plane_point(origin, direction, self.pivot, self.axis)
        return None if point is None else self._angle_of(point)

    def _nearest(self, target, reference):
        """``target`` turned by whole turns so it is the closest to ``reference``."""
        return reference + wrap_angle(target - reference)

    def _reject(self):
        if cuts.is_cutter(self.obj):
            return lambda p: cuts.inside_cutter(self.obj, p)
        return None

    def _edge_turn(self, direction, raw):
        """The turn that makes one of the shape's axes parallel to ``direction`` (None: no axis can)."""
        candidates = []
        for axis in self.local_axes:
            proj = axis - self.axis * axis.dot(self.axis)
            if proj.length < 0.3:  # that axis points along the rotation axis: turning does not move it
                continue
            base = signed_angle(proj, direction, self.axis)
            candidates += [base, base + math.pi]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(wrap_angle(c - raw)))

    def _snapped_delta(self, context, raw):
        """(delta, label) after the inference snaps; ``label`` says what it snapped to."""
        self.edge = None
        hit, inference = self.pick(context, (self.obj,), self._reject())
        if inference is not None:
            rel = inference.point - self.pivot
            if (rel - self.axis * rel.dot(self.axis)).length > 1e-9:
                return self._nearest(self._angle_of(inference.point) - self.grab, raw), inference.kind
        if hit is not None and not self.alt:
            edge = find_edge(context, self.region, self.rv3d, self.mouse, hit, self.snap_px * 0.8)
            if edge is not None:
                direction = edge.direction - self.axis * edge.direction.dot(self.axis)
                if direction.length > 0.2:  # an edge along the axis says nothing about the turn
                    delta = self._edge_turn(direction, raw)
                    if delta is not None:
                        self.edge = edge
                        return self._nearest(delta, raw), "Edge"
        return None, ""

    # -- events --------------------------------------------------------------------------

    def update(self, context):
        if self.state == 'PIVOT':
            self._update_pivot(context)
            return
        measured = self._cursor_angle()
        if measured is not None:
            if self.prev is None:
                self.grab = self.prev = measured
            self.theta += wrap_angle(measured - self.prev)
            self.prev = measured
        raw = self.theta
        self.infer_kind = ""
        delta, kind = self._snapped_delta(context, raw) if self.grab is not None else (None, "")
        if delta is not None:
            raw, self.infer_kind = delta, kind
        elif self.grab is not None and not self.alt and self._snap_vertical(raw) is not None:
            raw, self.infer_kind = self._snap_vertical(raw), "Vertical"
        elif self.ctrl:
            raw = round(raw / ANGLE_STEP) * ANGLE_STEP
        if self.num.active:
            try:
                raw = math.radians(parse_angle(self.num.text))
            except ValueError:
                pass
        self.delta = raw
        pivot = Matrix.Translation(self.pivot)
        self.obj.matrix_world = pivot @ Matrix.Rotation(self.delta, 4, self.axis) @ pivot.inverted() @ self.matrix0
        self._refresh(context)

    def _snap_vertical(self, raw):
        turn = self._vertical_turn(raw)
        return turn if turn is not None and abs(wrap_angle(turn - raw)) < VERTICAL_SNAP else None

    def _update_pivot(self, context):
        """Where the click would put the pivot: a snap point, else the surface under the cursor, else the ground."""
        hit, inference = self.pick(context, (self.obj,), self._reject())
        self.pick_kind = ""
        if inference is not None:
            self.pick_point, self.pick_normal, self.pick_kind = inference.point, inference.normal, inference.kind
        elif hit is not None:
            self.pick_point, self.pick_normal, self.pick_kind = hit.location, hit.normal, "On surface"
        else:
            origin, direction = self.ray()
            ground = ground_hit(origin, direction)
            self.pick_point, self.pick_normal = ground if ground is not None else (None, None)
            self.pick_kind = "On ground"
        self._refresh(context)

    def confirm(self, context):
        if self.state == 'PIVOT':
            if self.pick_point is None:
                return 'RUNNING'
            if self.use_normal and self.pick_normal is not None:
                self.set_axis(self.pick_normal, "face normal")
                self.axis_lock = None
            self.set_pivot(self.pick_point, self.pick_kind or "Point")
            self.state = 'ANGLE'
            return 'RUNNING'
        return 'FINISHED'

    def cancel(self, context):
        self.obj.matrix_world = self.matrix0

    # -- feedback --------------------------------------------------------------------

    def _draw_tilt(self, ov, reach):
        """The shape's own axes where it stands now, and the way its Z axis has leant away from where it started."""
        m3 = self.obj.matrix_world.to_3x3()
        here = self.obj.matrix_world.translation
        length = reach * 0.6
        turning = "XYZ".find(self.axis_name[-1]) if self.axis_name.startswith("local") else -1
        for i, color in enumerate(LOCAL_COLORS):
            ov.line(here, here + (m3 @ (X, Y, Z)[i]).normalized() * length, color, 3.0 if i == turning else 1.5)
        start, now = self.local_axes[2], (m3 @ Z).normalized()
        if start.angle(now) < math.radians(0.5):
            return
        ov.line(here, here + start * length, GHOST_COLOR, 1.0)  # where the Z axis pointed before
        turn = start.rotation_difference(now)
        arc = [here + (Quaternion().slerp(turn, i / 12.0) @ start) * length * 0.85 for i in range(13)]
        for a, b in zip(arc, arc[1:]):
            ov.line(a, b, LOCAL_COLORS[2], 2.0)

    def _draw_axis(self, ov, point, reach):
        ov.line(point - self.axis * reach, point + self.axis * reach, axis_color(self.axis), 1.5)

    def _refresh(self, context):
        ov = self.overlay
        ov.clear()
        reach = pixel_size(self.region, self.rv3d, self.pivot) * 90.0
        if self.state == 'PIVOT':
            ov.point(self.pivot, GUIDE_COLOR)
            if self.pick_point is not None:
                ov.point(self.pick_point, SNAP_COLOR if self.infer_kind else POINT_COLOR)
                if self.use_normal and self.pick_normal is not None:
                    tip = self.pick_point + self.pick_normal * reach * 0.6
                    ov.line(self.pick_point, tip, axis_color(self.pick_normal), 2.0)
            if self.infer_point is not None:
                ov.point(self.infer_point, SNAP_COLOR)
            text = "Click the pivot" + ("  (axis: face normal)" if self.use_normal else "")
            ov.label = (f"{text}   ({self.pick_kind})" if self.pick_kind else text), False
            self.header(context, f"{text}   N: axis = face normal   P: back")
            return
        turned = Quaternion(self.axis, self.delta) @ self.x0
        self._draw_axis(ov, self.pivot, reach)
        ov.line(self.pivot, self.pivot + self.x0 * reach, (0.7, 0.7, 0.7, 0.7), 1.0)
        ov.line(self.pivot, self.pivot + turned * reach, HILITE_COLOR, 2.5)
        steps = max(2, int(abs(self.delta) / math.radians(5.0)) + 1)
        arc = [self.pivot + (self.x0 * math.cos(self.delta * i / steps) + self.y0 * math.sin(self.delta * i / steps))
               * reach * 0.8 for i in range(steps + 1)]
        for a, b in zip(arc, arc[1:]):
            ov.line(a, b, HILITE_COLOR, 2.0)
        ov.point(self.pivot, POINT_COLOR)
        if self.infer_point is not None:
            ov.point(self.infer_point, SNAP_COLOR)
        if self.edge is not None:
            ov.line(self.edge.a, self.edge.b, ALIGN_COLOR, 3.0)
        self._draw_tilt(ov, reach)
        degrees = math.degrees(self.delta)
        if self.num.active:
            ov.label = f"Angle = {self.num.text}_", True
        else:
            text = f"Angle {degrees:.1f}\N{DEGREE SIGN}"
            ov.label = (f"{text}   ({self.infer_kind})" if self.infer_kind else text), False
        tilt = tilt_degrees(self.obj)
        lean = f"   Tilt: {tilt:.1f}\N{DEGREE SIGN}" if tilt > 0.05 else ""
        self.header(context, f"Rotate: {degrees:.1f}\N{DEGREE SIGN}   about {self.axis_name}   "
                             f"Pivot: {self.pivot_name}{lean}")
