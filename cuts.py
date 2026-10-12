"""Non-destructive boolean cuts made with EZ shapes.

A *cutter* is an ordinary EZ shape (so it keeps its handles and parameters) that is parented to
the *target* mesh and referenced by a modifier on it. The operation says what the cutter does:
``DIFFERENCE`` cuts a hole, ``UNION`` adds the shape to the mesh, ``INTERSECT`` keeps only where
both overlap and ``JOIN`` unites them with a rounded fillet where they meet (see ``joins.py``; its
modifier is a Geometry Nodes one, the others are Boolean). Moving the target moves the cutter;
dragging the cutter's handles reshapes the result live.
"""

import bpy

from . import joins
from . import shapes as cn

CUT_MODIFIER = "EZ Cut"
CUTTER_KEY = "ezhs_cutter"

DIFFERENCE, UNION, INTERSECT, JOIN = 'DIFFERENCE', 'UNION', 'INTERSECT', 'JOIN'
BOOLEAN_OPERATIONS = (DIFFERENCE, UNION, INTERSECT)
OPERATIONS = BOOLEAN_OPERATIONS + (JOIN,)
VERBS = {DIFFERENCE: "Cut into", UNION: "Add to", INTERSECT: "Intersect with", JOIN: "Join with"}


def is_cutter(obj):
    return obj is not None and CUTTER_KEY in obj


def can_cut(obj):
    """Meshes that can receive a cut (not linked data, not cutters themselves)."""
    return (obj is not None and obj.type == 'MESH' and obj.library is None
            and obj.override_library is None and not is_cutter(obj))


def _is_cut_modifier(mod):
    if not mod.name.startswith(CUT_MODIFIER):
        return False
    return mod.type == 'BOOLEAN' or joins.is_join_modifier(mod)


def _cutter_of(mod):
    """The shape a cut modifier works with."""
    return mod.object if mod.type == 'BOOLEAN' else joins.other_of(mod)


def cut_modifiers(target):
    """The EZ Cut modifiers of ``target`` in stack order."""
    return [m for m in target.modifiers if _is_cut_modifier(m)]


def find_target(cutter):
    """The object whose Boolean modifier uses ``cutter``, or None."""
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if _is_cut_modifier(mod) and _cutter_of(mod) == cutter:
                return obj
    return None


def find_cut_modifier(cutter):
    """The Boolean modifier that uses ``cutter``, or None."""
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if _is_cut_modifier(mod) and _cutter_of(mod) == cutter:
                return mod
    return None


def operation_of(cutter):
    mod = find_cut_modifier(cutter)
    if mod is None:
        return None
    return JOIN if mod.type != 'BOOLEAN' else mod.operation


def _new_modifier(target, cutter, operation):
    if operation == JOIN:
        return joins.add_join_modifier(target, cutter, CUT_MODIFIER + " Join")
    mod = target.modifiers.new(CUT_MODIFIER, 'BOOLEAN')
    mod.operation = operation
    mod.operand_type = 'OBJECT'
    mod.object = cutter
    mod.solver = 'EXACT'
    return mod


def set_operation(cutter, operation):
    """Switch a cutter between cutting, adding, intersecting and joining. Returns False if it is not a cutter."""
    if operation not in OPERATIONS:
        raise ValueError(operation)
    mod = find_cut_modifier(cutter)
    if mod is None:
        return False
    if operation == operation_of(cutter):
        return True
    target = find_target(cutter)
    if operation != JOIN and mod.type == 'BOOLEAN':
        mod.operation = operation
        return True
    # Boolean <-> join are different modifier types: swap it, keeping its place in the stack
    index = list(target.modifiers).index(mod)
    _new_modifier(target, cutter, operation)
    target.modifiers.move(len(target.modifiers) - 1, index)
    target.modifiers.remove(mod)
    return True


def add_cut(target, cutter, operation=DIFFERENCE):
    """Make ``cutter`` act on ``target`` (cut a hole by default). Returns the modifier."""
    if operation not in OPERATIONS:
        raise ValueError(operation)
    mod = _new_modifier(target, cutter, operation)

    cutter[CUTTER_KEY] = 1
    cutter.display_type = 'WIRE'
    cutter.hide_render = True
    world = cutter.matrix_world.copy()
    cutter.parent = target
    cutter.matrix_parent_inverse = target.matrix_world.inverted_safe()
    cutter.matrix_world = world
    return mod


def remove_cut(cutter):
    """Undo ``add_cut``: the cutter becomes a normal cylinder again. No-op otherwise."""
    if not is_cutter(cutter):
        return False
    for obj in bpy.data.objects:
        for mod in list(obj.modifiers):
            if _is_cut_modifier(mod) and _cutter_of(mod) == cutter:
                obj.modifiers.remove(mod)
    world = cutter.matrix_world.copy()
    cutter.parent = None
    cutter.matrix_world = world
    cutter.display_type = 'TEXTURED'
    cutter.hide_render = False
    del cutter[CUTTER_KEY]
    return True


def inside_cutter(cutter, point, margin=1e-4):
    """True if a world-space point lies inside or on the closed cutter solid.

    Used to ignore the geometry the boolean itself creates (the hole's rim,
    walls and floor) when snapping, so values do not stick to the cut.
    """
    mod = cn.find_modifier(cutter)
    if mod is None:
        return False
    kind, params = cn.kind_of(mod), cn.get_params(mod)
    local = cutter.matrix_world.inverted_safe() @ point
    return cn.contains_local(kind, params, local, margin * cn.characteristic_size(kind, params))


def apply_cuts(context, target):
    """Bake the EZ modifiers at the top of the stack and delete their cutters.

    Returns (modifiers applied, reason it stopped early or "").
    """
    applied, cutters, stopped = 0, [], ""
    while cut_modifiers(target):
        first = target.modifiers[0]
        if _is_cut_modifier(first):
            if _cutter_of(first) is not None:
                cutters.append(_cutter_of(first))
        elif first != cn.find_modifier(target):  # RNA wrappers are new objects: compare by ==
            stopped = f'"{first.name}" is above the EZ modifiers; move it down first'
            break
        with context.temp_override(object=target, active_object=target):
            bpy.ops.object.modifier_apply(modifier=first.name)
        applied += 1
    for cutter in cutters:
        mesh = cutter.data
        bpy.data.objects.remove(cutter)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return applied, stopped
