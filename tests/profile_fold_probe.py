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


def _coplanar_overlap_area(a,b,length_tolerance):
    """Clip projected triangles; shared edges/vertices have zero area."""
    normal = (a[1]-a[0]).cross(a[2]-a[0])
    other = (b[1]-b[0]).cross(b[2]-b[0])
    if normal.length < length_tolerance**2 or other.length < length_tolerance**2:
        return 0.0
    unit = normal.normalized()
    if abs(unit.dot(other.normalized())) < 1-1e-6:
        return 0.0
    if max(abs((p-a[0]).dot(unit)) for p in b) > length_tolerance:
        return 0.0
    axis = max(range(3),key=lambda k:abs(unit[k]))
    coords = [k for k in range(3) if k != axis]
    polygon = [tuple(p[k] for k in coords) for p in a]
    clip = [tuple(p[k] for k in coords) for p in b]
    sign = 1 if _cross(*clip) >= 0 else -1
    for i in range(3):
        if not polygon:
            return 0.0
        u,v = clip[i],clip[(i+1)%3]
        result = []
        previous = polygon[-1]
        dp = sign*_cross(u,v,previous)
        for current in polygon:
            dc = sign*_cross(u,v,current)
            if (dc >= 0) != (dp >= 0):
                t = dp/(dp-dc)
                result.append(tuple(previous[k]+t*(current[k]-previous[k]) for k in range(2)))
            if dc >= 0:
                result.append(current)
            previous,dp = current,dc
        polygon = result
    area = abs(sum(p[0]*q[1]-p[1]*q[0]
                   for p,q in zip(polygon,polygon[1:]+polygon[:1])))*0.5
    return area/abs(unit[axis])


def extended_mesh_overlaps(points,triangles):
    """Positive-area coplanar overlaps and adjacent transverse crossings.

    Normal hinges, shared edges and vertex-only touches are not errors.
    Near-degenerate triangles are measured separately by diagnose_mesh.
    """
    from mathutils.bvhtree import BVHTree
    scale = max((max(p[k] for p in points)-min(p[k] for p in points)
                 for k in range(3)),default=1.0)
    tolerance = max(scale*1e-7,1e-8)
    tree = BVHTree.FromPolygons(points,triangles,all_triangles=True,epsilon=tolerance)
    coplanar,adjacent = [],[]
    # Blender BVH can omit shared-vertex pairs. Enumerate those explicitly,
    # otherwise even an intentionally folded shared-edge pair is invisible.
    pairs = {tuple(sorted(pair)) for pair in tree.overlap(tree) if pair[0]!=pair[1]}
    incident = {}
    for i,triangle in enumerate(triangles):
        for vertex in triangle:
            incident.setdefault(vertex,[]).append(i)
    for items in incident.values():
        pairs.update((items[i],items[j]) for i in range(len(items)) for j in range(i+1,len(items)))
    # Coplanar pairs can also be absent from BVH overlap. An independent
    # bounding-box sweep supplies candidates, followed by area clipping.
    boxes = [(tuple(min(points[v][k] for v in triangle) for k in range(3)),
              tuple(max(points[v][k] for v in triangle) for k in range(3)),i)
             for i,triangle in enumerate(triangles)]
    active = []
    for low,high,i in sorted(boxes,key=lambda item:item[0][0]):
        active = [(a,b,j) for a,b,j in active if b[0] >= low[0]-tolerance]
        for a,b,j in active:
            if all(b[k] >= low[k]-tolerance and high[k] >= a[k]-tolerance for k in (1,2)):
                pairs.add(tuple(sorted((i,j))))
        active.append((low,high,i))
    for i,j in sorted(pairs):
        if i >= j:
            continue
        shared = set(triangles[i]).intersection(triangles[j])
        a,b = [points[k] for k in triangles[i]],[points[k] for k in triangles[j]]
        area = _coplanar_overlap_area(a,b,tolerance)
        area_a = (a[1]-a[0]).cross(a[2]-a[0]).length*.5
        area_b = (b[1]-b[0]).cross(b[2]-b[0]).length*.5
        if area > max(tolerance**2*16,min(area_a,area_b)*1e-8):
            coplanar.append((i,j,len(shared),area))
        elif len(shared)==1:
            # For a shared-vertex pair, only the opposite edges can pierce
            # away from that vertex. Testing incident edges magnifies float32
            # endpoint roundoff into false positives. Two noncoplanar triangles
            # sharing an edge intersect only on that edge, by construction.
            opposite_a = [points[k] for k in triangles[i] if k not in shared]
            opposite_b = [points[k] for k in triangles[j] if k not in shared]
            if (_pierces(*opposite_a,b,eps=1e-5) or _pierces(*opposite_b,a,eps=1e-5)):
                adjacent.append((i,j))
    return coplanar,adjacent


def verify_extended_classifier():
    from mathutils import Vector
    def run(coords,triangles):
        return extended_mesh_overlaps([Vector(p) for p in coords],triangles)
    # Legal shared edge and a vertex-only touch.
    assert run([(0,0,0),(1,0,0),(0,1,0),(1,-1,0)],[(0,1,2),(1,0,3)]) == ([],[])
    assert run([(0,0,0),(1,0,0),(0,1,0),(-1,0,0),(0,-1,0)],[(0,1,2),(0,3,4)]) == ([],[])
    # Shared-edge fold onto the same side: positive-area overlap.
    c,a = run([(0,0,0),(1,0,0),(0,1,0),(.2,.5,0)],[(0,1,2),(1,0,3)])
    assert len(c)==1 and c[0][2]==2 and not a
    # Disconnected coplanar overlap, even with opposite winding.
    c,a = run([(0,0,0),(1,0,0),(0,1,0),(.1,.1,0),(.1,.6,0),(.6,.1,0)],[(0,1,2),(3,4,5)])
    assert len(c)==1 and c[0][2]==0 and not a
    # Ordinary noncoplanar hinge is legal.
    assert run([(0,0,0),(1,0,0),(0,1,0),(0,0,1)],[(0,1,2),(1,0,3)]) == ([],[])
    # Shared-vertex pair also crosses away from the shared vertex.
    c,a = run([(-1,-1,0),(1,-1,0),(0,1,0),(0,.2,-1),(0,.2,1)],[(0,1,2),(0,3,4)])
    assert not c and a == [(0,1)]
    print('PASS extended classifier: legal edges/touches/hinges and injected overlaps/fold',flush=True)


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
    coplanar,adjacent = extended_mesh_overlaps(points,triangles)
    old_coplanar,old_adjacent = extended_mesh_overlaps(reference,triangles)
    new_coplanar = {(i,j) for i,j,_,_ in coplanar}-{(i,j) for i,j,_,_ in old_coplanar}
    new_adjacent = set(adjacent)-set(old_adjacent)
    report = dict(collapsed=sum(r < 1e-4 for r in ratios),
                  rotated=rotated,minimum_area_ratio=min(ratios,default=None),
                  strict_crossings=len(hits),baseline_crossings=len(baseline),
                  new_crossings=len(introduced),broad_phase_candidates=candidates,
                  coplanar_overlaps=len(coplanar),adjacent_coplanar_overlaps=sum(s>0 for _,_,s,_ in coplanar),
                  baseline_coplanar_overlaps=len(old_coplanar),new_coplanar_overlaps=len(new_coplanar),
                  adjacent_crossings=len(adjacent),baseline_adjacent_crossings=len(old_adjacent),
                  new_adjacent_crossings=len(new_adjacent))
    print('MESH FOLD PROBE',report,'new pairs',sorted(introduced)[:8],flush=True)
    print('OVERLAP DETAILS coplanar',coplanar[:8],'adjacent crossings',adjacent[:8],flush=True)
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
    verify_extended_classifier()
    diagnose_mesh(reference,points,[(face[0],face[k],face[k+1])
                                  for face in faces for k in range(1,len(face)-1)])
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
