"""Where the always-visible edit handles of an EZ shape sit and point.

Pure math (no gizmo API) so the placement can be tested headless.

A *face part* is a face that can be pushed/pulled along one local axis:
``TOP``/``BOTTOM`` (axis Z) and, for boxes, ``X1``/``X0`` and ``Y1``/``Y0``.
The "far" face (``end`` 1) sits at the parameter value along its axis, the
"origin" face (``end`` 0) at the object origin. ``SIDE`` is the radius
handle of a cylinder (at mid-height), of a sphere (at its equator) or the base radius of a cone (at the base rim); ``TOPR`` is
the top radius of a cone, at the top rim. ``ROTATE`` is the rotation dial: it sits at the origin and its
axis is the local Z axis. ``ROTATE_X`` and ``ROTATE_Y`` are the two tilt rings at the same place, about the
local X and Y axes.
"""

import math
from collections import namedtuple

from mathutils import Matrix, Quaternion, Vector

from . import shapes

SIDE = 'SIDE'
TOPR = 'TOPR'
ROTATE = 'ROTATE'
ROTATE_X = 'ROTATE_X'
ROTATE_Y = 'ROTATE_Y'
# dial handle -> index of the local axis it turns about (Z turns the shape, X and Y tilt it)
DIALS = {ROTATE: 2, ROTATE_X: 0, ROTATE_Y: 1}
TILT_PARTS = (ROTATE_X, ROTATE_Y)
# part -> (local axis index, end)
FACES = {'TOP': (2, 1), 'BOTTOM': (2, 0), 'X1': (0, 1), 'X0': (0, 0), 'Y1': (1, 1), 'Y0': (1, 0)}
ALL_PARTS = ('TOP', 'BOTTOM', SIDE, TOPR, 'X0', 'X1', 'Y0', 'Y1', ROTATE, ROTATE_X, ROTATE_Y)
_PARTS = {
    shapes.KIND_CYLINDER: ('TOP', 'BOTTOM', SIDE),
    shapes.KIND_CONE: ('TOP', 'BOTTOM', SIDE, TOPR),
    shapes.KIND_BOX: ('TOP', 'BOTTOM', 'X0', 'X1', 'Y0', 'Y1'),
    shapes.KIND_SPHERE: (SIDE,),
    shapes.KIND_QUADSPHERE: (SIDE,),
}

Frame = namedtuple("Frame", "kind params base axes scales offset")


def parts_for(kind):
    """The face/radius handles of a kind (the rotation dial is separate: every shape has one)."""
    return _PARTS[kind]


def all_parts_for(mod):
    return parts_for(shapes.kind_of(mod)) + (ROTATE, ROTATE_X, ROTATE_Y)


def frame_of(obj, mod):
    """Unit axes and scales of the object's matrix plus its parameters."""
    m3 = obj.matrix_world.to_3x3()
    cols = [m3.col[i].copy() for i in range(3)]
    scales = [max(c.length, 1e-12) for c in cols]
    axes = [c / s for c, s in zip(cols, scales)]
    kind, params = shapes.kind_of(mod), shapes.get_params(mod)
    # The face positions below are worked out in the corner frame (a box spans 0..size on each axis);
    # ``offset`` is how far the object origin is from that frame's origin, in world space (0 unless the
    # origin is the center of the base).
    local = shapes.origin_offset(kind, params)
    offset = sum((axes[i] * (local[i] * scales[i]) for i in range(3)), Vector())
    return Frame(kind, params, obj.matrix_world.translation.copy(), axes, scales, offset)


def corner_base(frame):
    """World position of the origin of the corner frame (the object origin unless it is centered)."""
    return frame.base - frame.offset


def dimension(frame, axis):
    """Signed world-space extent of the shape along a local axis (0 if it has none)."""
    name = shapes.DIMENSIONS[frame.kind].get(axis)
    return frame.params[name] * frame.scales[axis] if name else 0.0


def face_center(frame, axis, end):
    """World position of the center of a face part."""
    center = corner_base(frame)
    for i in range(3):
        if i == axis:
            coord = dimension(frame, i) if end == 1 else 0.0
        else:
            coord = dimension(frame, i) * 0.5  # 0 for a cylinder's lateral axes
        center += frame.axes[i] * coord
    return center


def face_outward(frame, axis, end):
    """Unit direction pointing away from the solid (flipped for negative sizes)."""
    up = frame.axes[axis] if dimension(frame, axis) >= 0.0 else -frame.axes[axis]
    return up if end == 1 else -up


def handle_pose(obj, mod, part):
    """(location, outward direction) of a handle in world space."""
    frame = frame_of(obj, mod)
    if part in DIALS:
        return frame.base.copy(), frame.axes[DIALS[part]].copy()
    if part in FACES:
        axis, end = FACES[part]
        if axis not in shapes.DIMENSIONS[frame.kind]:
            raise ValueError(f"{frame.kind} has no {part} handle")
        return face_center(frame, axis, end), face_outward(frame, axis, end)
    corner = corner_base(frame)  # the center of the base (the origin, unless it is halfway up)
    if frame.kind == shapes.KIND_CONE and part in (SIDE, TOPR):
        name, along = ("Radius", 0.0) if part == SIDE else ("TopRadius", dimension(frame, 2))
        radius = frame.params[name] * frame.scales[0]
        return corner + frame.axes[2] * along + frame.axes[0] * radius, frame.axes[0].copy()
    if part == SIDE and frame.kind in shapes.SPHERE_KINDS:
        return frame.base + frame.axes[0] * (frame.params["Radius"] * frame.scales[0]), frame.axes[0].copy()
    if part == SIDE and frame.kind == shapes.KIND_CYLINDER:
        radius = frame.params["Radius"] * frame.scales[0]
        return (corner + frame.axes[2] * (dimension(frame, 2) * 0.5) + frame.axes[0] * radius,
                frame.axes[0].copy())
    raise ValueError(part)


def tilt_degrees(obj):
    """How far the shape's own Z axis leans from the world's vertical, in degrees (0 = upright)."""
    z = obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))
    if z.length < 1e-12:
        return 0.0
    return math.degrees(z.angle(Vector((0.0, 0.0, 1.0))))


TILT_EPSILON = 0.05  # degrees: less than this counts as upright


def upright_matrix(matrix):
    """``matrix`` turned the least that makes its Z axis vertical, about its own origin (None if it already is).

    The scale stays on the shape's own axes, so a stretched shape does not shear.
    """
    loc, rot, scale = matrix.decompose()
    up = Vector((0.0, 0.0, 1.0))
    if math.degrees((rot @ up).angle(up)) < TILT_EPSILON:
        return None
    for _ in range(3):  # near upside down the axis is a difference of nearly opposite vectors: refine it
        z = rot @ up
        angle = z.angle(up)
        if angle < 1e-9:
            break
        axis = z.cross(up)
        if axis.length < 1e-9:  # upside down: any axis across Z will do; the shape's own X keeps it predictable
            axis = rot @ Vector((1.0, 0.0, 0.0))
        rot = Quaternion(axis.normalized(), angle) @ rot
    return Matrix.LocRotScale(loc, rot, scale)


def handle_matrix(location, direction):
    """Gizmo matrix: origin at ``location``, local +Z along ``direction``."""
    rot = direction.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    return Matrix.Translation(location) @ rot

