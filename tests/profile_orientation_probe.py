"""Stage-wise geometric orientation diagnostics; no shading-normal tests."""
import math
import statistics
from mathutils import Vector


def _area(points,face):
    origin = points[face[0]]
    result = Vector((0,0,0))
    for k in range(1,len(face)-1):
        result += (points[face[k]]-origin).cross(points[face[k+1]]-origin)
    return result


def measure_orientation(reference,before,after,faces,distances):
    rotated = []
    for i,face in enumerate(faces):
        old,new = _area(reference,face),_area(after,face)
        if old.length>1e-10 and new.length>1e-10 and old.dot(new)<0:
            rotated.append(i)
    during = [i for i,face in enumerate(faces)
              if _area(before,face).dot(_area(after,face))<0]
    print('ORIENTATION rotated polygon count source-to-final',len(rotated),
          'reattachment-only',len(during),flush=True)
    if not rotated:
        return
    ids = {v for i in rotated for v in faces[i]}
    angles,radial,ratios,steps = [],[],[],[]
    before_angles,final_angles = [],[]
    for i in rotated:
        face = faces[i]
        start,end = _area(before,face),_area(after,face)
        original = _area(reference,face)
        angles.append(math.degrees(start.angle(end)))
        before_angles.append(math.degrees(original.angle(start)))
        final_angles.append(math.degrees(original.angle(end)))
        center = sum((after[v] for v in face),Vector())/len(face)
        direction = Vector((center.x,center.y,0)).normalized()
        radial.append(end.normalized().dot(direction))
        # Follow the exact straight-line Set Position blend. A rotation alone
        # is legal; a near-zero area along its path is a separate warning.
        best = (float('inf'),None)
        for k in range(101):
            t = k/100
            points = {v:before[v].lerp(after[v],t) for v in face}
            ratio = _area(points,face).length/max(start.length,1e-10)
            if ratio<best[0]:
                best = (ratio,t)
        ratios.append(best[0])
        steps.append(best[1])
    print('ORIENTATION affected polygon ids',rotated[:16],
          'source distance range',min(distances[v] for v in ids),max(distances[v] for v in ids),
          'reference z range',min(reference[v].z for v in ids),max(reference[v].z for v in ids),
          'final z range',min(after[v].z for v in ids),max(after[v].z for v in ids),
          'reattachment angle median/max',statistics.median(angles),max(angles),
          'source-to-before angle median',statistics.median(before_angles),
          'source-to-final angle median',statistics.median(final_angles),
          'negative radial normals',sum(r<0 for r in radial),
          'minimum area ratio along blend',min(ratios),
          'near-collapse paths (<1e-3)',sum(r<1e-3 for r in ratios),
          'minimum-area blend fractions',steps[:8],flush=True)
