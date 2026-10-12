"""Geometry-only diagnostics; rotated/downward faces are not errors by themselves.

The meridian check is scoped to the axisymmetric flat-slab fixture. It is
not a general 3D self-intersection or inside/outside classifier.
"""
import math


def _pierces(a,b,triangle,eps=1e-7):
    """Strict segment/triangle intersection, excluding coplanar/touch cases."""
    p,q,r = triangle
    e1,e2,direction = q-p,r-p,b-a
    h = direction.cross(e2)
    det = e1.dot(h)
    if abs(det) < eps:
        return False
    s = a-p
    u = s.dot(h)/det
    v = direction.dot(s.cross(e1))/det
    t = e2.dot(s.cross(e1))/det
    return eps < t < 1-eps and u > eps and v > eps and u+v < 1-eps


def strict_mesh_crossings(points,triangles):
    """BVH broad phase + noncoplanar narrow phase, only disjoint vertices.

    This does not certify a mesh as intersection-free: coplanar overlap,
    endpoint contact and crossings of topologically adjacent faces are omitted.
    Intersections with B are intentional contact, and are not checked here.
    """
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromPolygons(points,triangles,all_triangles=True)
    hits = []
    candidates = 0
    for i,j in tree.overlap(tree):
        if i >= j or set(triangles[i]).intersection(triangles[j]):
            continue
        candidates += 1
        a,b = [points[k] for k in triangles[i]],[points[k] for k in triangles[j]]
        if (any(_pierces(a[k],a[(k+1)%3],b) for k in range(3)) or
                any(_pierces(b[k],b[(k+1)%3],a) for k in range(3))):
            hits.append((i,j))
    return hits,candidates


def diagnose_mesh(reference,points,triangles):
    ratios = []
    rotated = 0
    for a,b,c in triangles:
        old = (reference[b]-reference[a]).cross(reference[c]-reference[a])
        new = (points[b]-points[a]).cross(points[c]-points[a])
        if old.length > 1e-10:
            ratios.append(new.length/old.length)
            rotated += old.dot(new) < 0
    hits,candidates = strict_mesh_crossings(points,triangles)
    baseline,_ = strict_mesh_crossings(reference,triangles)
    introduced = set(hits)-set(baseline)
    report = dict(collapsed=sum(r < 1e-4 for r in ratios),
                  rotated=rotated,minimum_area_ratio=min(ratios,default=None),
                  strict_crossings=len(hits),baseline_crossings=len(baseline),
                  new_crossings=len(introduced),broad_phase_candidates=candidates)
    print('MESH FOLD PROBE',report,'new pairs',sorted(introduced)[:8],flush=True)
    return report


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
