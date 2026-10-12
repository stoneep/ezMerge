"""Freeze the evaluated adhesion band for a later constrained relaxation pass.

This is a snapshot, not a live Cloth input: moving A/B requires a new capture.
No resampling, simulation or alteration of the source/target happens here.
"""

import bpy

from . import shapes as cn
from .merge_settings import (
    ADHESION_KEY, ADHESION_DISTANCE, ADHESION_SOURCE_POSITION, ADHESION_WEIGHT,
    INFLUENCE_RADIUS, GLOBAL, BASE,
)

PATCH_KEY = 'ezhs_adhesion_patch'
PATCH_START = 'Start Boundary'
PATCH_CONTACT = 'Contact Boundary'
PATCH_INTERIOR = 'Free Interior'
PATCH_PINS = 'Boundary Pins'
PATCH_REST_POSITION = 'ezhs_patch_rest_position'
PATCH_SOURCE_INDEX = 'ezhs_patch_source_index'
PATCH_START_EDGE = 'ezhs_patch_start_edge'
PATCH_CONTACT_EDGE = 'ezhs_patch_contact_edge'


def _contact_tolerance(influence):
    # Exact Boolean still leaves small intersection residuals on faceted,
    # transformed cylinders. Scale cautiously, never accepting broad gaps.
    return max(1e-6, min(influence * 1e-3, 1e-4))


def _extract_band(mesh, influence):
    """Return compact indices and *topological* boundaries, not weight thresholds.

    Keep whole faces crossing the influence limit, including one unaffected
    outer row. Custom curves may flatten weights inside the band, so weights
    cannot reliably identify its boundary. B-contact is an existing open edge
    whose endpoints were on B before deformation, not just a nearby vertex.
    """
    distance = mesh.attributes.get(ADHESION_DISTANCE)
    source = mesh.attributes.get(ADHESION_SOURCE_POSITION)
    weight = mesh.attributes.get(ADHESION_WEIGHT)
    if (distance is None or source is None or weight is None or distance.domain != 'POINT'
            or source.domain != 'POINT' or distance.data_type != 'FLOAT'
            or source.data_type != 'FLOAT_VECTOR' or weight.domain != 'POINT' or weight.data_type != 'FLOAT'):
        raise ValueError('Adhesion data missing. Enable the merge and use an intersecting target.')
    distances = [item.value for item in distance.data]
    weights = [item.value for item in weight.data]
    # After Snap interpolates the coarse deformation into an outer support
    # row beyond the nominal distance limit. Include this actual motion too;
    # otherwise its "start" boundary would be cut through a moving surface.
    selected = {face.index for face in mesh.polygons
                if any(distances[i] < influence - 1e-8 or weights[i] > 1e-8 for i in face.vertices)}
    if not selected:
        raise ValueError('No contact band inside Influence Radius.')
    edge_faces = {}
    faces = []
    for face in mesh.polygons:
        for edge in face.edge_keys:
            edge_faces.setdefault(tuple(sorted(edge)), []).append(face.index)
        if face.index in selected:
            faces.append(tuple(face.vertices))
    start, contact = set(), set()
    for edge, adjacent in edge_faces.items():
        count = sum(i in selected for i in adjacent)
        if not count:
            continue
        if len(adjacent) > 2:
            raise ValueError('Contact band is non-manifold; repair the source before capturing.')
        if count != 1:
            continue
        if len(adjacent) == 2:
            start.add(edge)
        elif all(distances[i] < _contact_tolerance(influence) for i in edge):
            contact.add(edge)
        else:
            gap = max(distances[i] for i in edge)
            raise ValueError(f'Contact band has an unsupported open boundary (source gap {gap:.3g}); close or repair the source.')
    if not start or not contact:
        raise ValueError('Both a start boundary and a cut contact boundary are required. Check the intersection and band width.')
    start_vertices = {i for edge in start for i in edge}
    contact_vertices = {i for edge in contact for i in edge}
    if start_vertices & contact_vertices:
        raise ValueError('Boundaries touch. Increase source resolution or Influence Radius.')
    # All boundary vertices must have two incident boundary edges. Multiple
    # disjoint loops are fine; branching/pinched loops cannot form a safe patch.
    for edges in (start, contact):
        degrees = {}
        for edge in edges:
            for i in edge:
                degrees[i] = degrees.get(i, 0) + 1
        if any(degree != 2 for degree in degrees.values()):
            raise ValueError('Contact boundaries are not closed, unbranched loops.')
    indices = sorted({i for face in faces for i in face})
    # Never silently include an unrelated island/cap in a future solver input.
    neighbors = {i: set() for i in indices}
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            neighbors[a].add(b)
            neighbors[b].add(a)
    unseen = set(indices)
    while unseen:
        pending = [unseen.pop()]
        component = set(pending)
        while pending:
            for i in neighbors[pending.pop()] & unseen:
                unseen.remove(i)
                component.add(i)
                pending.append(i)
        if not component & start_vertices or not component & contact_vertices:
            raise ValueError('Every contact patch island needs both a start and a contact boundary.')
    remap = {old: new for new, old in enumerate(indices)}
    return (indices, [tuple(remap[i] for i in face) for face in faces],
            {tuple(sorted(remap[i] for i in edge)) for edge in start},
            {tuple(sorted(remap[i] for i in edge)) for edge in contact},
            [p.material_index for p in mesh.polygons if p.index in selected])


def create_adhesion_patch(obj, mod, *, depsgraph=None, collection=None):
    """Create a wire-only, render-disabled snapshot in A-local coordinates.

    Require adhesion last in the visible stack: subsequent modifiers could
    alter/erase the source attributes or make them describe a different shape.
    Existing saved adhesion groups already supply the needed attributes.
    """
    if (obj.type != 'MESH' or mod not in list(obj.modifiers) or mod.type != 'NODES'
            or mod.node_group is None or ADHESION_KEY not in mod.node_group):
        raise ValueError('Select a Surface Adhesion modifier on its source mesh.')
    if not mod.show_viewport:
        raise ValueError('Enable Surface Adhesion in the viewport first.')
    stack = list(obj.modifiers)
    if any(item.show_viewport for item in stack[stack.index(mod) + 1:]):
        raise ValueError('Move Surface Adhesion last or disable later viewport modifiers before capturing.')
    influence = cn.get_input(mod, INFLUENCE_RADIUS) * cn.get_input(mod, GLOBAL)
    if influence <= 1e-6 or cn.get_input(mod, BASE) is None:
        raise ValueError('Surface Adhesion needs an active target and nonzero Influence Radius.')
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        indices, faces, start, contact, material_indices = _extract_band(evaluated_mesh, influence)
        positions = [tuple(evaluated_mesh.vertices[i].co) for i in indices]
        originals = [tuple(evaluated_mesh.attributes[ADHESION_SOURCE_POSITION].data[i].vector) for i in indices]
        distances = [evaluated_mesh.attributes[ADHESION_DISTANCE].data[i].value for i in indices]
        materials = list(evaluated_mesh.materials)
    finally:
        evaluated.to_mesh_clear()
    mesh = bpy.data.meshes.new(obj.name + ' Contact Patch')
    patch = None
    try:
        mesh.from_pydata(positions, [], faces)
        mesh.update()
        for material in materials:
            mesh.materials.append(material)
        for polygon, material_index in zip(mesh.polygons, material_indices):
            polygon.material_index = material_index
            polygon.use_smooth = True
        for name, values in ((PATCH_REST_POSITION, positions), (ADHESION_SOURCE_POSITION, originals)):
            attribute = mesh.attributes.new(name, 'FLOAT_VECTOR', 'POINT')
            attribute.data.foreach_set('vector', [c for p in values for c in p])
        mesh.attributes.new(PATCH_SOURCE_INDEX, 'INT', 'POINT').data.foreach_set('value', indices)
        mesh.attributes.new(ADHESION_DISTANCE, 'FLOAT', 'POINT').data.foreach_set('value', distances)
        for name, edges in ((PATCH_START_EDGE, start), (PATCH_CONTACT_EDGE, contact)):
            attribute = mesh.attributes.new(name, 'BOOLEAN', 'EDGE')
            attribute.data.foreach_set('value', [tuple(sorted(e.vertices)) in edges for e in mesh.edges])
        patch = bpy.data.objects.new(mesh.name, mesh)
        start_vertices = {i for edge in start for i in edge}
        contact_vertices = {i for edge in contact for i in edge}
        pins = start_vertices | contact_vertices
        interior = set(range(len(positions))) - pins
        for name, vertices in ((PATCH_START, start_vertices), (PATCH_CONTACT, contact_vertices),
                               (PATCH_INTERIOR, interior), (PATCH_PINS, pins)):
            group = patch.vertex_groups.new(name=name)
            if vertices:
                group.add(sorted(vertices), 1.0, 'REPLACE')
        patch.matrix_world = obj.matrix_world.copy()
        patch.display_type = 'WIRE'
        patch.hide_render = True
        patch[PATCH_KEY] = 1
        patch['source_object'] = obj.name
        patch['source_modifier'] = mod.name
        patch['target_object'] = cn.get_input(mod, BASE).name
        patch['snapshot_frame'] = bpy.context.scene.frame_current
        patch['snapshot_only'] = True
        collection = collection or (obj.users_collection[0] if obj.users_collection else bpy.context.collection)
        collection.objects.link(patch)
        return patch
    except Exception:
        if patch is not None:
            bpy.data.objects.remove(patch, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        raise
