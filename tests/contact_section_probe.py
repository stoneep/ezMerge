"""Actual geometry of the flat-slab fixture; no shading-normal inference."""
import math
from mathutils import Vector


def measure_section(reference,points,faces):
    # Preserve the original meridian order instead of sorting deformed heights.
    ids = [i for i,p in enumerate(reference) if p.x>.05 and abs(p.y)<1e-6 and p.z>1e-6]
    ids.sort(key=lambda i:reference[i].z)
    samples = [(math.hypot(points[i].x,points[i].y),points[i].z) for i in ids]
    contact = [math.hypot(p.x,p.y) for p in points if abs(p.z)<1e-6]
    assert contact and samples
    footprint = max(contact)
    low = [p for p in points if 0<=p.z<.15]
    outer = max(low,key=lambda p:math.hypot(p.x,p.y))
    outer_radius = math.hypot(outer.x,outer.y)
    down = up = 0
    minimum_nz = 1.0
    for face in faces:
        center = sum((points[i] for i in face),Vector())/len(face)
        if not 1e-6<center.z<.15:
            continue
        origin = points[face[0]]
        normal = sum(((points[face[k]]-origin).cross(points[face[k+1]]-origin)
                      for k in range(1,len(face)-1)),Vector())
        if normal.length<1e-10:
            continue
        normal.normalize()
        down += normal.z < -1e-5
        up += normal.z > 1e-5
        minimum_nz = min(minimum_nz,normal.z)
    print('SECTION actual contact outer radius',footprint,
          'low outer radius',outer_radius,'height at low outer radius',outer.z,
          'radial extension beyond contact',outer_radius-footprint,
          'near-contact geometric faces downward/upward',down,up,
          'minimum geometric normal z',minimum_nz,flush=True)
    print('SECTION ordered +X meridian (radius,z)',
          [(round(r,6),round(z,6)) for r,z in samples if z<.2],flush=True)
