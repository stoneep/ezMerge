"""Geometry-only diagnostics; rotated/downward faces are not errors by themselves.

The meridian check is scoped to the axisymmetric flat-slab fixture. It is
not a general 3D self-intersection or inside/outside classifier.
"""
import math


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def proper_crossings(profile):
    """Strict interior segment crossings; shared/touching endpoints excluded."""
    hits = []
    for i in range(len(profile)-1):
        a, b = profile[i:i+2]
        for j in range(i+2, len(profile)-1):
            c, d = profile[j:j+2]
            if (_cross(a,b,c)*_cross(a,b,d) < -1e-16 and
                    _cross(c,d,a)*_cross(c,d,b) < -1e-16):
                hits.append((i,j))
    return hits


def verify_classifier():
    # A lip can point downward without crossing itself.
    assert not proper_crossings([(0.7,0),(0.8,0.03),(0.75,0.1),(0.6,0.3)])
    assert proper_crossings([(0,0),(1,1),(0,1),(1,0)]) == [(0,2)]
    assert not proper_crossings([(0,0),(1,0),(2,0),(3,0)])


def diagnose_profile(reference, points, faces):
    verify_classifier()
    collapsed = rotated = triangles = 0
    ratios = []
    for face in faces:
        for k in range(1,len(face)-1):
            a,b,c = face[0],face[k],face[k+1]
            old = (reference[b]-reference[a]).cross(reference[c]-reference[a])
            new = (points[b]-points[a]).cross(points[c]-points[a])
            if old.length < 1e-10:
                continue
            triangles += 1
            ratio = new.length/old.length
            ratios.append(ratio)
            collapsed += ratio < 1e-4
            rotated += old.dot(new) < 0
    # Follow the original +X side meridian, not the deformed height ordering:
    # sorting by final height would conceal a fold. Exclude the flat cap.
    ids = [i for i,p in enumerate(reference)
           if p.x > 0.05 and abs(p.y) < 1e-6 and p.z > 1e-6]
    ids.sort(key=lambda i: reference[i].z)
    profile = [(math.hypot(points[i].x,points[i].y),points[i].z) for i in ids]
    crossings = proper_crossings(profile)
    descending = sum(b[1] < a[1]-1e-6 for a,b in zip(profile,profile[1:]))
    outward = sum(b[0] > a[0]+1e-6 for a,b in zip(profile,profile[1:]))
    print('FOLD PROBE triangles',triangles,'collapsed',collapsed,
          'rotated beyond 90 degrees (not alone an error)',rotated,
          'minimum area ratio',min(ratios,default=None),
          'meridian samples',len(profile),'strict profile crossings',len(crossings),
          'descending segments',descending,'outward segments',outward,
          'crossing pairs',crossings[:8],flush=True)
    assert len(profile) >= 3, 'No usable meridian: fixture assumptions no longer hold'
    return {'collapsed':collapsed,'crossings':crossings,'descending':descending,
            'outward':outward,'profile':profile}
