"""Low-level node field helpers shared by merge builders."""

import math

from .merge_settings import *


def _math(nodes, links, operation, a=None, b=None, c=None, clamp=False):
    """A Math node; ``a``/``b``/``c`` are sockets (linked) or numbers (set)."""
    node = nodes.new('ShaderNodeMath')
    node.operation = operation
    node.use_clamp = clamp
    for index, value in enumerate((a, b, c)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            node.inputs[index].default_value = value
        else:
            links.new(value, node.inputs[index])
    return node.outputs[0]


def _vector(nodes, links, operation, a=None, b=None, scale=None):
    node = nodes.new('ShaderNodeVectorMath')
    node.operation = operation
    for index, value in ((0, a), (1, b)):
        if value is not None:
            links.new(value, node.inputs[index])
    if isinstance(scale, (int, float)):
        node.inputs[3].default_value = scale  # the Scale socket of SCALE
    elif scale is not None:
        links.new(scale, node.inputs[3])
    return node.outputs['Value'] if operation in ('DOT_PRODUCT', 'LENGTH') else node.outputs['Vector']


def _switch(nodes, links, input_type, condition, false, true):
    node = nodes.new('GeometryNodeSwitch')
    node.input_type = input_type
    links.new(condition, node.inputs['Switch'])
    for name, value in (('False', false), ('True', true)):
        if isinstance(value, (int, float, bool)):
            node.inputs[name].default_value = value
        else:
            links.new(value, node.inputs[name])
    return node.outputs[0]


def _smooth_normals(nodes, links, geometry, explicit_faces=False):
    """``geometry`` with a corner attribute ``SURFACE_NORMAL``: its normals smoothed across facets, sharp edges kept.

    The Normal node of Geometry Nodes ignores shading (the face's normal on every corner), so sampling it on a
    faceted curved mesh gives the same normal over a whole facet and a jump at every seam. Here each corner gets the
    average of the face normals around its vertex that are within ``NORMAL_ANGLE`` of its own face (what Shade Auto
    Smooth does); Sample Nearest Surface interpolates it, so the normal changes continuously across a smooth surface
    while the faces of a box stay exactly flat.
    """
    face_normal = nodes.new('GeometryNodeInputNormal').outputs[0]
    if explicit_faces:
        # Sample on FACE explicitly: since Blender 4.4, Normal on CORNER can
        # already be a shading normal. Averaging those again would blur cap edges.
        face = nodes.new('GeometryNodeFaceOfCorner')
        links.new(nodes.new('GeometryNodeInputIndex').outputs[0], face.inputs['Corner Index'])
        geometric = nodes.new('GeometryNodeSampleIndex')
        geometric.data_type = 'FLOAT_VECTOR'
        geometric.domain = 'FACE'
        links.new(geometry, geometric.inputs['Geometry'])
        links.new(face_normal, geometric.inputs['Value'])
        links.new(face.outputs['Face Index'], geometric.inputs['Index'])
        face_normal = geometric.outputs['Value']
    vertex = nodes.new('GeometryNodeVertexOfCorner')
    total = None
    for rank in range(NORMAL_NEIGHBOURS):
        around = nodes.new('GeometryNodeCornersOfVertex')
        links.new(vertex.outputs['Vertex Index'], around.inputs['Vertex Index'])
        around.inputs['Sort Index'].default_value = rank
        neighbour = nodes.new('GeometryNodeSampleIndex')
        neighbour.data_type = 'FLOAT_VECTOR'
        neighbour.domain = 'CORNER'
        links.new(geometry, neighbour.inputs['Geometry'])
        links.new(face_normal, neighbour.inputs['Value'])
        links.new(around.outputs['Corner Index'], neighbour.inputs['Index'])
        alike = _math(nodes, links, 'GREATER_THAN', _vector(nodes, links, 'DOT_PRODUCT', neighbour.outputs['Value'], face_normal),
                      math.cos(NORMAL_ANGLE))
        exists = _math(nodes, links, 'LESS_THAN', float(rank), around.outputs['Total'])
        weight = _math(nodes, links, 'MULTIPLY', alike, exists)
        part = _vector(nodes, links, 'SCALE', neighbour.outputs['Value'], scale=weight)
        total = part if total is None else _vector(nodes, links, 'ADD', total, part)
    # no neighbour at all (an isolated corner): the face's own normal
    smooth = _vector(nodes, links, 'NORMALIZE', total)
    length = _vector(nodes, links, 'LENGTH', total)
    store = nodes.new('GeometryNodeStoreNamedAttribute')
    store.data_type = 'FLOAT_VECTOR'
    store.domain = 'CORNER'
    store.inputs['Name'].default_value = SURFACE_NORMAL
    links.new(geometry, store.inputs['Geometry'])
    links.new(_switch(nodes, links, 'VECTOR', _math(nodes, links, 'GREATER_THAN', length, 1e-6), face_normal, smooth),
              store.inputs['Value'])
    return store.outputs[0]


def _surface_normal_sampler(nodes, links, mesh):
    """A Sample Nearest Surface node reading ``SURFACE_NORMAL`` of ``mesh`` (made by ``_smooth_normals``)."""
    sampler = nodes.new('GeometryNodeSampleNearestSurface')
    sampler.data_type = 'FLOAT_VECTOR'
    links.new(mesh, sampler.inputs['Mesh'])
    attribute = nodes.new('GeometryNodeInputNamedAttribute')
    attribute.data_type = 'FLOAT_VECTOR'
    attribute.inputs['Name'].default_value = SURFACE_NORMAL
    links.new(attribute.outputs['Attribute'], sampler.inputs['Value'])
    return sampler


def _reset_bead_mapping(mapping):
    mapping.initialize()
    points = mapping.curves[0].points
    while len(points) > 2:
        points.remove(points[1])
    for point, location in zip(points, ((0.0, 0.0), (1.0, 1.0))):
        point.location = location
        point.handle_type = 'VECTOR'
    mapping.use_clip = True
    mapping.clip_min_x = mapping.clip_min_y = 0.0
    mapping.clip_max_x = mapping.clip_max_y = 1.0
    mapping.update()
