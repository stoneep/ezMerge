"""Measure actual target separation independently of shading normals."""
import statistics


def measure_contact(obj,base,points,source_distances,*,label,faces=()):
    from mathutils.bvhtree import BVHTree
    relative = obj.matrix_world.inverted() @ base.matrix_world
    tree = BVHTree.FromPolygons([relative @ v.co for v in base.data.vertices],
                               [tuple(p.vertices) for p in base.data.polygons])
    gaps = []
    signed = []
    for point in points:
        nearest,normal,_,distance = tree.find_nearest(point)
        gaps.append(distance)
        signed.append((point-nearest).dot(normal))
    print('CONTACT MEASURE',label,'point count',len(points),
          'within 1e-5 of target',sum(g<1e-5 for g in gaps),flush=True)
    edges = {tuple(sorted((face[k],face[(k+1)%len(face)])))
             for face in faces for k in range(len(face))}
    crossing = 0
    for a,b in edges:
        if min(source_distances[a],source_distances[b]) >= .1:
            continue
        delta = points[b]-points[a]
        if delta.length < 1e-8:
            continue
        hit,_,_,travel = tree.ray_cast(points[a],delta.normalized(),delta.length)
        crossing += hit is not None and 1e-6 < travel < delta.length-1e-6
    print('CONTACT actual mesh edges crossing B in near-source band',crossing,
          '(zero on-surface vertices does not mean zero surface contact)',flush=True)
    for lo,hi in ((0,.05),(.05,.1),(.1,.25)):
        ids = [i for i,d in enumerate(source_distances) if lo<=d<hi]
        values = sorted(gaps[i] for i in ids)
        print('SOURCE DISTANCE BAND',lo,hi,'samples',len(ids),
              'median actual gap',statistics.median(values) if values else None,
              'max actual gap',max(values,default=None),
              'on target',sum(gaps[i]<1e-5 for i in ids),
              'negative/positive side',sum(signed[i]<-1e-5 for i in ids),
              sum(signed[i]>1e-5 for i in ids),flush=True)
    return gaps
