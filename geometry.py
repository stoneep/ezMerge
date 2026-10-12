"""View/ray math shared by the interactive tools."""

import math
from collections import namedtuple

from bpy_extras import view3d_utils
from mathutils import Matrix, Quaternion, Vector

# Below these ray/axis angles the absolute mapping is too unstable and the
# drag falls back to screen-space increments.
_MIN_AXIS_SIN = math.sin(math.radians(8.0))
_MIN_PLANE_COS = math.cos(math.radians(82.0))

WORLD_AXES = (
    (Vector((1.0, 0.0, 0.0)), (0.95, 0.25, 0.25, 1.0)),
    (Vector((0.0, 1.0, 0.0)), (0.35, 0.85, 0.25, 1.0)),
    (Vector((0.0, 0.0, 1.0)), (0.30, 0.50, 1.00, 1.0)),
)
OFF_AXIS_COLOR = (0.90, 0.30, 0.90, 1.0)


def mouse_ray(region, rv3d, coord):
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord).normalized()
    return origin, direction


RayHit = namedtuple("RayHit", "location normal obj index")


def _ray_hits_box(obj, inv, origin, direction):
    """Slab test of a world ray against an object's bounding box."""
    o = inv @ origin
    d = inv.to_3x3() @ direction
    corners = [Vector(c) for c in obj.bound_box]
    t_min, t_max = 0.0, float("inf")
    for i in range(3):
        lo = min(c[i] for c in corners)
        hi = max(c[i] for c in corners)
        if abs(d[i]) < 1e-12:
            if o[i] < lo or o[i] > hi:
                return False
            continue
        t1, t2 = (lo - o[i]) / d[i], (hi - o[i]) / d[i]
        t_min, t_max = max(t_min, min(t1, t2)), min(t_max, max(t1, t2))
        if t_min > t_max:
            return False
    return True


def _nearest_hit_except(context, depsgraph, origin, direction, skip):
    """Closest hit among all visible meshes whose name is not in ``skip``.

    ``Scene.ray_cast`` can only return the closest hit overall, and stepping
    past an excluded object can jump over a surface lying exactly in the same
    plane (a boss on a face, a cutter starting on a face), so the remaining
    objects are cast one by one.
    """
    best, best_t = None, float("inf")
    for obj in context.visible_objects:
        if obj.type != 'MESH' or obj.name in skip:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mw = evaluated.matrix_world
        inv = mw.inverted_safe()
        if not _ray_hits_box(evaluated, inv, origin, direction):
            continue
        hit, loc, nor, index = evaluated.ray_cast(inv @ origin, inv.to_3x3() @ direction)
        if not hit:
            continue
        world = mw @ loc
        t = (world - origin).length
        if t < best_t:
            normal = (inv.to_3x3().transposed() @ nor).normalized()
            if normal.dot(direction) > 0.0:
                normal = -normal
            best, best_t = RayHit(world, normal, obj, index), t
    return best


def scene_raycast_ex(context, origin, direction, exclude=()):
    """First hit as a RayHit(location, normal, original_object, face_index), or None.

    ``index`` is the polygon index in the *evaluated* mesh. Objects in
    ``exclude`` (originals) are looked through, so a tool can ignore the
    object it is currently shaping.
    """
    depsgraph = context.evaluated_depsgraph_get()
    hit, loc, nor, index, obj, _matrix = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit:
        return None
    original = getattr(obj, "original", obj)
    skip = {o.name for o in exclude if o is not None}
    if original.name in skip:
        return _nearest_hit_except(context, depsgraph, origin, direction, skip)
    nor = nor.normalized()
    if nor.dot(direction) > 0.0:
        nor = -nor
    return RayHit(loc, nor, original, index)


def scene_raycast(context, origin, direction, exclude=()):
    """Return (location, normal, original_object) of the first hit, or None."""
    hit = scene_raycast_ex(context, origin, direction, exclude)
    return None if hit is None else (hit.location, hit.normal, hit.obj)


def ground_hit(origin, direction):
    """Hit the XY ground plane, or a vertical world plane in side views."""
    if abs(direction.z) > 0.05:
        t = -origin.z / direction.z
        if t > 0.0:
            return origin + direction * t, Vector((0.0, 0.0, 1.0 if direction.z < 0.0 else -1.0))
    axis = 0 if abs(direction.x) >= abs(direction.y) else 1
    normal = Vector((0.0, 0.0, 0.0))
    normal[axis] = -1.0 if direction[axis] > 0.0 else 1.0
    denom = direction.dot(normal)
    if abs(denom) > 1e-6:
        t = -origin.dot(normal) / denom
        if t > 0.0:
            return origin + direction * t, normal
    return None


def closest_on_axis(origin, direction, point, axis):
    """Parameter t of the point on line (point + t*axis) closest to the ray.

    Returns None when the ray is nearly parallel to the axis.
    """
    b = direction.dot(axis)
    denom = 1.0 - b * b
    if denom < _MIN_AXIS_SIN ** 2:
        return None
    w = origin - point
    return (axis.dot(w) - b * direction.dot(w)) / denom


def radial_on_plane(origin, direction, plane_point, center, axis):
    """Intersect the ray with the plane through plane_point perpendicular to axis.

    Returns (distance from the axis line through center, radial unit vector),
    or None when the plane is seen edge-on or lies behind the view.
    """
    denom = direction.dot(axis)
    if abs(denom) < _MIN_PLANE_COS:
        return None
    t = (plane_point - origin).dot(axis) / denom
    if t <= 0.0:
        return None
    rel = origin + direction * t - center
    radial = rel - axis * rel.dot(axis)
    length = radial.length
    if length < 1e-9:
        return 0.0, None
    return length, radial / length


def orientation_for_normal(normal):
    """Rotation taking local +Z to ``normal``.

    Floors and ceilings take the shortest arc, so a cylinder drawn on the
    ground gets no rotation at all; walls keep local Y pointing up.
    """
    if normal.z > 0.9999:
        return Quaternion()
    if normal.z < -0.9999:
        return Quaternion((1.0, 0.0, 0.0), math.pi)
    if abs(normal.z) > 0.7:
        return Vector((0.0, 0.0, 1.0)).rotation_difference(normal)
    return normal.to_track_quat('Z', 'Y')


def orientation_from_axes(normal, x_hint):
    """Rotation with local Z along ``normal`` and local X as close to ``x_hint`` as possible.

    Returns None when ``x_hint`` is (nearly) parallel to the normal.
    """
    x = x_hint - normal * x_hint.dot(normal)
    if x.length < 1e-6:
        return None
    x.normalize()
    return Matrix((x, normal.cross(x), normal)).transposed().to_quaternion()


def signed_angle(a, b, axis):
    """Angle that turns ``a`` into ``b`` about the unit vector ``axis`` (both are projected onto its plane)."""
    a = a - axis * a.dot(axis)
    b = b - axis * b.dot(axis)
    return math.atan2(axis.dot(a.cross(b)), a.dot(b))


def wrap_angle(angle):
    """Angle in (-pi, pi]."""
    wrapped = (angle + math.pi) % (2.0 * math.pi) - math.pi  # [-pi, pi)
    return math.pi if wrapped == -math.pi else wrapped


def ray_plane_point(origin, direction, plane_point, normal):
    """Where the ray meets the plane, or None if it is seen edge-on or lies behind the view."""
    denom = direction.dot(normal)
    if abs(denom) < _MIN_PLANE_COS:
        return None
    t = (plane_point - origin).dot(normal) / denom
    if t <= 0.0:
        return None
    return origin + direction * t


def any_perpendicular(axis):
    other = Vector((1.0, 0.0, 0.0)) if abs(axis.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return axis.cross(other).normalized()


def pixel_size(region, rv3d, point):
    """World units covered by one pixel at the depth of ``point``."""
    p = view3d_utils.location_3d_to_region_2d(region, rv3d, point)
    if p is None:
        return 1.0 / max(region.width, 1)
    a = view3d_utils.region_2d_to_location_3d(region, rv3d, p, point)
    b = view3d_utils.region_2d_to_location_3d(region, rv3d, p + Vector((1.0, 0.0)), point)
    return max((b - a).length, 1e-9)


def screen_delta(region, rv3d, ref_point, dir3d, mouse_from, mouse_to):
    """World-space movement along dir3d for a mouse move, measured on screen."""
    size = pixel_size(region, rv3d, ref_point)
    p0 = view3d_utils.location_3d_to_region_2d(region, rv3d, ref_point)
    p1 = view3d_utils.location_3d_to_region_2d(region, rv3d, ref_point + dir3d * size * 50.0)
    move = Vector(mouse_to) - Vector(mouse_from)
    if p0 is None or p1 is None:
        return move.y * size
    s = p1 - p0
    if s.length < 5.0:
        # Direction points at the viewer: map vertical mouse motion instead.
        return move.y * size
    return move.dot(s.normalized()) * (size * 50.0) / s.length


def view_key(rv3d):
    return tuple(tuple(row) for row in rv3d.view_matrix) + (rv3d.is_perspective,)


class Drag1D:
    """Map mouse motion to one scalar value.

    When the view is well conditioned the value follows the cursor exactly
    (value = measure + offset). The offset is re-anchored when the view
    changes so orbiting mid-drag never makes the value jump. When the
    measurement is unstable (looking straight down an axis, plane edge-on) it
    falls back to screen-space increments.
    """

    def __init__(self, value, offset=None):
        self.value = value
        self.offset = offset
        self._view = None
        self._prev_mouse = None

    def update(self, region, rv3d, mouse, measured, ref_point, dir3d):
        key = view_key(rv3d)
        if measured is not None:
            if self.offset is None or (self._view is not None and key != self._view):
                self.offset = self.value - measured
            self.value = measured + self.offset
        else:
            self.offset = None
            if self._prev_mouse is not None:
                self.value += screen_delta(region, rv3d, ref_point, dir3d, self._prev_mouse, mouse)
        self._view = key
        self._prev_mouse = Vector(mouse)
        return self.value


def axis_color(axis):
    for world, color in WORLD_AXES:
        if abs(axis.dot(world)) > 0.9999:
            return color
    return OFF_AXIS_COLOR


def snap_step(rv3d, unit_settings):
    """Adaptive "nice" increment in Blender units, based on zoom level."""
    from .numinput import unit_factor
    scale = unit_settings.scale_length if unit_settings.system != 'NONE' else 1.0
    factor, _suffix = unit_factor(unit_settings)
    display = max(rv3d.view_distance * scale / factor * 0.02, 1e-9)
    step_display = 10.0 ** math.floor(math.log10(display))
    return step_display * factor / scale


def snap(value, step):
    return round(value / step) * step
