"""SketchUp-style inference: snap to the corners, edge midpoints and center of
the face under the cursor.
"""

from collections import namedtuple

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from .geometry import pixel_size

Inference = namedtuple("Inference", "point normal kind obj")
Edge = namedtuple("Edge", "a b direction obj corners")

SNAP_PIXELS = 14.0
# Corners win over midpoints, midpoints over the face center (as in SketchUp).
KIND_ENDPOINT, KIND_MIDPOINT, KIND_CENTER = "Endpoint", "Midpoint", "Center"
KIND_CURSOR = "3D Cursor"


def cursor_inference(context, region, rv3d, mouse, threshold=SNAP_PIXELS, reject=None):
    """The 3D cursor as a snap point when it is within ``threshold`` pixels of ``mouse``, else None.

    Its normal is the cursor's own Z axis, so shapes drawn there take the cursor's orientation.
    ``obj`` is None: that is what tells it apart from the snap points of a mesh.
    """
    cursor = context.scene.cursor
    spot = location_3d_to_region_2d(region, rv3d, cursor.location)
    if spot is None or (spot - mouse).length > threshold:
        return None
    point = cursor.location.copy()
    if reject is not None and reject(point):
        return None
    normal = (cursor.matrix.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    return Inference(point, normal, KIND_CURSOR, None)


def face_candidates(evaluated, index):
    """World-space (corners, midpoints, center) of one evaluated polygon, or None."""
    if evaluated.type != 'MESH':
        return None
    mesh = evaluated.data
    if not 0 <= index < len(mesh.polygons):
        return None
    poly = mesh.polygons[index]
    mw = evaluated.matrix_world
    corners = [mw @ mesh.vertices[i].co for i in poly.vertices]
    mids = [(corners[i] + corners[(i + 1) % len(corners)]) * 0.5 for i in range(len(corners))]
    return corners, mids, mw @ poly.center


def find_inference(context, region, rv3d, mouse, hit, threshold=SNAP_PIXELS, reject=None):
    """Snap target for the cursor given the ray hit under it, or None.

    ``reject(point)`` drops candidates the caller does not want, e.g. the
    surfaces a boolean cutter has just created.
    """
    evaluated = hit.obj.evaluated_get(context.evaluated_depsgraph_get())
    found = face_candidates(evaluated, hit.index)
    if found is None:
        return None
    corners, mids, center = found
    # Cheap 3D prefilter so big n-gons (cylinder caps) are not all projected.
    reach2 = (pixel_size(region, rv3d, hit.location) * threshold * 4.0) ** 2

    def nearest(points):
        best, best_d = None, threshold
        for p in points:
            if (p - hit.location).length_squared > reach2:
                continue
            if reject is not None and reject(p):
                continue
            screen = location_3d_to_region_2d(region, rv3d, p)
            if screen is None:
                continue
            d = (screen - mouse).length
            if d <= best_d:
                best, best_d = p, d
        return best

    for kind, points in ((KIND_ENDPOINT, corners), (KIND_MIDPOINT, mids), (KIND_CENTER, [center])):
        point = nearest(points)
        if point is not None:
            return Inference(point.copy(), hit.normal, kind, hit.obj)
    return None


def _segment_distance(p, a, b):
    ab = b - a
    length2 = ab.length_squared
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, (p - a).dot(ab) / length2))
    return (p - (a + ab * t)).length


def find_edge(context, region, rv3d, mouse, hit, threshold=None):
    """The edge of the hit face nearest to the cursor on screen, or None.

    ``threshold`` is a distance in pixels; None accepts the nearest edge however far it is.
    ``Edge.corners`` is the number of corners of the face (round caps have many).
    """
    evaluated = hit.obj.evaluated_get(context.evaluated_depsgraph_get())
    found = face_candidates(evaluated, hit.index)
    if found is None:
        return None
    corners = found[0]
    best, best_d = None, float("inf") if threshold is None else threshold
    for i, a in enumerate(corners):
        b = corners[(i + 1) % len(corners)]
        if (b - a).length < 1e-9:
            continue
        pa = location_3d_to_region_2d(region, rv3d, a)
        pb = location_3d_to_region_2d(region, rv3d, b)
        if pa is None or pb is None:
            continue
        d = _segment_distance(mouse, pa, pb)
        if d <= best_d:
            best, best_d = Edge(a.copy(), b.copy(), (b - a).normalized(), hit.obj, len(corners)), d
    return best
