"""Public merge API and modifier lifecycle.

New BEAD merges use surface adhesion: A's evaluated contact region deforms
onto B; source datablocks and B stay unchanged. Explicit width/height callers
retain the original ribbon path. DEFORM, ROWS and BEVEL are preserved for old
files. Socket names, group tags and versions are serialization contracts.

Node builders live in merge_adhesion, merge_bead and merge_legacy; common
fields and defaults live in merge_nodes and merge_settings. This module
re-exports the historical names used by operators, UI and existing scripts.
"""

import math

import bpy

from . import shapes as cn

# Keep the historical merges module API (including private test helpers).
from .merge_settings import *
from .merge_nodes import (
    _math, _vector, _switch, _smooth_normals, _surface_normal_sampler,
    _reset_bead_mapping,
)
from .merge_legacy import _build, _build_flare, _build_prep, _build_finish, _build_rows_prep
from .merge_bead import _build_bead, _cleanup_bead_boundary
from .merge_adhesion import _build_adhesion
from .merge_patch import create_adhesion_patch


def _cached_group(key, version, name, build, *, exclude=(MERGE_OWN_KEY,), tags=(), geometry_only=True):
    """Reuse compatible shared groups; remove a newly-created group if its build fails.

    Curve-owned copies are excluded unless a legacy non-curve builder opts out.
    Do not bump serialized versions for behavior-preserving code organization.
    """
    for ng in bpy.data.node_groups:
        if (not geometry_only or ng.bl_idname == 'GeometryNodeTree') and ng.get(key) == version:
            if not any(excluded in ng for excluded in exclude):
                return ng
    ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    ng[key] = version
    for tag, value in tags:
        ng[tag] = value
    ng.is_modifier = True
    try:
        build(ng)
    except Exception:
        bpy.data.node_groups.remove(ng)
        raise
    return ng


# -- Shared node groups and merge creation ---------------------------------------------------------------

def get_flare_group():
    return _cached_group(FLARE_KEY, FLARE_VERSION, "EZ Merge Flare", _build_flare)


def get_merge_group():
    return _cached_group(MERGE_KEY, MERGE_VERSION, MERGE_GROUP_NAME, _build)


# -- BEAD: surface adhesion for new merges, contact ribbon for explicit width/height callers --------------

def is_surface_adhesion(mod):
    return mod.node_group is not None and ADHESION_KEY in mod.node_group


def get_adhesion_group():
    # Preserve the historic adhesion cache predicate during this refactor.
    return _cached_group(ADHESION_KEY, ADHESION_VERSION, 'EZ Surface Adhesion', _build_adhesion,
                         tags=((BEAD_KEY, BEAD_VERSION),), geometry_only=False)


def get_bead_group():
    return _cached_group(BEAD_KEY, BEAD_VERSION, BEAD_GROUP_NAME, _build_bead,
                         exclude=(MERGE_OWN_KEY, ADHESION_KEY))


def _add_bead_merge(obj, base, radius, height=None, adhesion=False, strength=None):
    mod = obj.modifiers.new(MERGE_MODIFIER, 'NODES')
    mod.node_group = get_adhesion_group() if adhesion else get_bead_group()
    own_curve(mod)
    cn.set_input(mod, BASE, base)
    if adhesion:
        cn.set_input(mod, INFLUENCE_RADIUS, radius * 2.0)
        cn.set_input(mod, MERGE_RADIUS, radius)
        cn.set_input(mod, PUSH_STRENGTH, radius if strength is None else float(strength))
        cn.set_input(mod, SURFACE_CORNER_REACH, radius * 0.5)
    else:
        cn.set_input(mod, BEAD_WIDTH, radius)
        cn.set_input(mod, BEAD_HEIGHT, radius * 0.42 if height is None else float(height))
        cn.set_input(mod, BEAD_MATERIAL, obj.active_material or base.active_material)
    # 4.5+ applies boundary normals inside the bead group. Blender 4.2-4.4
    # keeps ordinary smooth shading: Geometry Nodes cannot write custom loop
    # normals there, and a generated POINT float attribute is not a deform
    # vertex group usable to mask Data Transfer. Do not transfer B's normals
    # over the whole object or overwrite them again with Weighted Normal.
    return mod


# -- the Bevel method: prepare (union + contact edges) -> Bevel modifier -> finish (drop B, smooth) ------------

def _bevel_group(key, name, build):
    return _cached_group(key, BEVEL_PART_VERSION, name, build, exclude=())


def method_of(mod):
    """DEFORM, ROWS, BEVEL or BEAD, for the primary modifier of a merge."""
    if PREP_KEY in mod.node_group:
        return BEVEL
    if FLARE_KEY in mod.node_group:
        return ROWS
    return BEAD if BEAD_KEY in mod.node_group else DEFORM


def _is_part(mod):
    """A Prep, Rows, Bevel, Finish, Transfer or Normals modifier that belongs to some merge."""
    if mod.type in ('BEVEL', 'WEIGHTED_NORMAL', 'DATA_TRANSFER'):
        return mod.name.startswith(MERGE_MODIFIER)
    return (mod.type == 'NODES' and mod.node_group is not None
            and (FINISH_KEY in mod.node_group or ROWS_PREP_KEY in mod.node_group))


def companions(obj, mod):
    """The modifiers that belong to ``mod``: Prep and Rows before a ROWS merge, Bevel and Finish after a BEVEL one,
    the Transfer and Normals shading smoothers after a BEAD one."""
    method = method_of(mod)
    names = {BEVEL: (" Bevel", " Finish"), ROWS: (" Prep", " Rows"),
             BEAD: (" Transfer", " Normals")}.get(method, ())
    return [m for m in obj.modifiers if m.name in [mod.name + suffix for suffix in names]]


def bevel_modifier(obj, mod):
    found = [m for m in companions(obj, mod) if m.type == 'BEVEL']
    return found[0] if found else None


def remove_modifier(obj, mod):
    for part in companions(obj, mod):
        obj.modifiers.remove(part)
    obj.modifiers.remove(mod)


def _bevel_modifier_for(obj, name, width, segments):
    bevel = obj.modifiers.new(name, 'BEVEL')
    bevel.limit_method = 'WEIGHT'  # only the contact edges (the Prep step gives them a weight)
    bevel.width = width
    bevel.segments = segments
    bevel.miter_outer = 'MITER_SHARP'
    bevel.miter_inner = 'MITER_SHARP'
    bevel.loop_slide = True
    return bevel


def _add_bevel_merge(obj, base, radius):
    mod = obj.modifiers.new(MERGE_MODIFIER, 'NODES')
    mod.node_group = _bevel_group(PREP_KEY, "EZ Merge Prep", _build_prep)
    cn.set_input(mod, BASE, base)
    bevel = _bevel_modifier_for(obj, mod.name + " Bevel", radius, DEFAULT_BEVEL_SEGMENTS)
    bevel.profile = 0.5
    bevel.use_clamp_overlap = True
    finish = obj.modifiers.new(mod.name + " Finish", 'NODES')
    finish.node_group = _bevel_group(FINISH_KEY, "EZ Merge Finish", _build_finish)
    cn.set_input(finish, BASE, base)
    return mod


def _use_corner_profile(bevel):
    """A custom Bevel profile that runs along both original faces and through the corner: it adds rows, it does not round.

    Half of the segments go to each leg; the rows on the other mesh are thrown away.
    """
    profile = bevel.custom_profile
    while len(profile.points) > 2:
        profile.points.remove(profile.points[1])
    profile.points[0].location = (1.0, 0.0)   # on the shape's wall, at the original edge
    profile.points[1].location = (0.0, 1.0)   # on the other mesh
    profile.points.add(1.0, 1.0)              # the original corner: the contact line
    for point in profile.points:
        point.handle_type_1 = point.handle_type_2 = 'VECTOR'
    profile.use_sample_straight_edges = True
    profile.use_sample_even_lengths = False
    profile.update()
    bevel.profile_type = 'CUSTOM'
    bevel.use_clamp_overlap = False  # the other side of the contact is not used: its slivers must not limit the rows


def _input_path(mod, name):
    """Data path of a Geometry Nodes modifier input, for drivers (4.2 keeps it in an ID property, 5.x in a struct)."""
    ident = cn.input_identifier(mod, name)
    if hasattr(getattr(mod, "properties", None), "inputs"):
        return f'modifiers["{mod.name}"].properties.inputs.{ident}.value'
    return f'modifiers["{mod.name}"]["{ident}"]'


def _drive_width(obj, bevel, mod):
    """Bevel width = Original Height x Global Merge: the rows end exactly on the original edge."""
    fcurve = bevel.driver_add("width")
    driver = fcurve.driver
    driver.type = 'SCRIPTED'
    for name, label in ((HEIGHT, "h"), (GLOBAL, "g")):
        var = driver.variables.new()
        var.name = label
        var.type = 'SINGLE_PROP'
        var.targets[0].id = obj
        var.targets[0].data_path = _input_path(mod, name)
    driver.expression = "max(h * g, 1e-5)"


def _add_rows_merge(obj, base, radius, strength):
    clear_rows(obj)  # the Bevel makes the rows
    mod = obj.modifiers.new(MERGE_MODIFIER, 'NODES')
    mod.node_group = get_flare_group()
    cn.set_input(mod, BASE, base)
    cn.set_input(mod, HEIGHT, radius)
    cn.set_input(mod, SPREAD, 2.0 * radius if strength is None else float(strength))
    prep = obj.modifiers.new(mod.name + " Prep", 'NODES')
    prep.node_group = _bevel_group(ROWS_PREP_KEY, "EZ Merge Rows Prep", _build_rows_prep)
    cn.set_input(prep, BASE, base)
    bevel = _bevel_modifier_for(obj, mod.name + " Rows", radius, ROWS_COUNT)
    _use_corner_profile(bevel)
    _drive_width(obj, bevel, mod)
    # the cut and the rows come before the flare
    for part in (prep, bevel):
        obj.modifiers.move(list(obj.modifiers).index(part), list(obj.modifiers).index(mod))
    return mod


def available():
    """True if this Blender has the nodes a merge needs (Geometry Proximity and Sample Nearest Surface, 4.2+)."""
    return hasattr(bpy.types, "GeometryNodeSampleNearestSurface") and hasattr(bpy.types, "GeometryNodeProximity")


def curve_node(mod):
    """The Float Curve node of the group of ``mod`` (None in a group of an older file)."""
    return mod.node_group.nodes.get(CURVE_NODE) if mod.node_group is not None else None


def reset_bead_curve(obj, mod):
    if method_of(mod) != BEAD or curve_node(mod) is None:
        raise ValueError("Recreate this bead to use Custom Curve")
    own_curve(mod)
    _reset_bead_mapping(curve_node(mod).mapping)
    mod.node_group.update_tag()
    obj.update_tag()


def own_curve(mod):
    """Give ``mod`` a group of its own, so that editing its custom curve leaves the other merges alone."""
    ng = mod.node_group
    if MERGE_OWN_KEY in ng and ng.users <= 1:
        return ng
    own = ng.copy()
    own[MERGE_OWN_KEY] = 1
    own.name = ('EZ Surface Adhesion' if ADHESION_KEY in ng else
                BEAD_GROUP_NAME if BEAD_KEY in ng else MERGE_GROUP_NAME) + " (custom)"
    mod.node_group = own
    return own


def set_falloff(obj, mod, value):
    """Choose the profile. Custom gets the modifier its own copy of the group (and so of the curve)."""
    if value == CUSTOM:
        if curve_node(mod) is None:
            raise ValueError("this merge was made by an older version: remove it and merge again to use a custom curve")
        own_curve(mod)
    cn.set_input(mod, FALLOFF, value)
    obj.update_tag()


def is_merge_modifier(mod):
    """The primary modifier of a merge (the one that holds the other mesh)."""
    return (mod.type == 'NODES' and mod.node_group is not None
            and any(key in mod.node_group for key in (MERGE_KEY, PREP_KEY, FLARE_KEY, BEAD_KEY)))


def merge_modifiers(obj):
    return [m for m in obj.modifiers if is_merge_modifier(m)]


def base_of(mod):
    return cn.get_input(mod, BASE)


def default_radius(obj, method=DEFORM):
    """A visible fillet (8% of the smallest dimension), or a larger legacy flare."""
    bpy.context.view_layer.update()  # dimensions of a new object are 0 until it is evaluated
    sizes = [d for d in obj.dimensions if d > 1e-6]
    fraction = 0.08 if method == BEAD else 0.15
    return fraction * (min(sizes) if sizes else 1.0)


def prepare_rows(obj, radius):
    """Give an EZ cylinder, cone or box rows about ``radius`` apart along its height, so the flare has vertices to bend.

    Subdividing alone cuts every face into squares and cannot add rows without adding as many columns.
    Returns True if the shape has a Rings parameter.
    """
    mod = cn.find_modifier(obj)
    if mod is None or "Rings" not in cn.param_names(cn.kind_of(mod)):
        return False
    height = abs(cn.get_params(mod)["Height"])
    rings = max(1, min(MAX_AUTO_RINGS, math.ceil(height / max(radius, 1e-6))))
    if rings > cn.get_params(mod)["Rings"]:
        cn.set_params(obj, mod, rings=rings)
    return True


def clear_rows(obj):
    """Back to one row along the height: the Bevel method wants big faces next to the contact (rows limit its width)."""
    mod = cn.find_modifier(obj)
    if mod is not None and "Rings" in cn.param_names(cn.kind_of(mod)) and cn.get_params(mod)["Rings"] > 1:
        cn.set_params(obj, mod, rings=1)


def add_merge(obj, base, radius=None, strength=None, method=BEAD, *, width=None, height=None, adhesion=None):
    """Make ``obj`` spread over ``base``. Returns the (primary) modifier."""
    if method not in METHODS:
        raise ValueError(method)
    if (width is not None or height is not None) and method != BEAD:
        raise ValueError("width and height are BEAD parameters")
    if width is not None:
        radius = float(width)  # radius remains the compatibility alias for old callers.
    # measure the shape before the modifier exists: the merge itself changes its dimensions
    radius = default_radius(obj, method) if radius is None else float(radius)
    if method == BEVEL:
        clear_rows(obj)
        return _add_bevel_merge(obj, base, radius)
    if method == ROWS:
        return _add_rows_merge(obj, base, radius, strength)
    if method == BEAD:
        # Explicit width/height callers retain the ribbon contract. New UI
        # merges use surface adhesion; existing saved node groups stay intact.
        use_adhesion = (width is None and height is None) if adhesion is None else bool(adhesion)
        return _add_bead_merge(obj, base, radius, height, use_adhesion, strength)
    prepare_rows(obj, radius)
    mod = obj.modifiers.new(MERGE_MODIFIER, 'NODES')
    mod.node_group = get_merge_group()
    cn.set_input(mod, BASE, base)
    cn.set_input(mod, RADIUS, radius)
    cn.set_input(mod, STRENGTH, 2.0 * radius if strength is None else float(strength))
    return mod


def convert(obj, mod, method):
    """Redo the merge ``mod`` with the other ``method`` (same other mesh and size). Returns the new primary modifier."""
    if method_of(mod) == method:
        return mod
    base = base_of(mod)
    old = method_of(mod)
    if old == BEVEL:
        radius, strength = bevel_modifier(obj, mod).width, None
    elif old == BEAD:
        # the bead has no Strength: pass only its radius (times Global) through
        scale = cn.get_input(mod, GLOBAL)
        width_name = (MERGE_RADIUS if is_surface_adhesion(mod) else
                      BEAD_WIDTH if mod.node_group.interface.items_tree.get(BEAD_WIDTH) is not None else RADIUS)
        radius, strength = cn.get_input(mod, width_name) * scale, None
    else:
        names = (HEIGHT, SPREAD) if old == ROWS else (RADIUS, STRENGTH)
        scale = cn.get_input(mod, GLOBAL)
        radius, strength = cn.get_input(mod, names[0]) * scale, cn.get_input(mod, names[1]) * scale
    remove_modifier(obj, mod)
    return add_merge(obj, base, radius, strength, method)


def is_flat(base, limit=12):
    """True if the other mesh has only a few face directions (a box, a slab, a plane): what ROWS is perfect on."""
    ev = base.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        directions = {tuple(round(c, 3) for c in p.normal) for p in mesh.polygons}
    finally:
        ev.to_mesh_clear()
    return 0 < len(directions) <= limit


def suggested_method(base):
    """BEAD is the production merge; the older methods remain for existing files."""
    return BEAD


def remove_merge(obj, base=None):
    """Remove the merges of ``obj`` (only those onto ``base`` if given), with their Bevel and Finish steps. Returns how many."""
    count = 0
    for mod in merge_modifiers(obj):
        if base is None or base_of(mod) == base:
            remove_modifier(obj, mod)
            count += 1
    return count


def apply_merge(context, obj):
    """Bake the modifiers of ``obj`` up to its last merge (the shape's own, then the merges). Returns the count."""
    count = 0
    while merge_modifiers(obj) or any(_is_part(m) for m in obj.modifiers):
        first = obj.modifiers[0]
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=first.name)
        count += 1
    return count
