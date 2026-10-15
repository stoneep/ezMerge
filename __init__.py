"""ezHardSurface: SketchUp-style hard surface modeling for Blender."""

bl_info = {
    "name": "ezHardSurface",
    "author": "stoneep",
    "version": (0, 28, 1),
    "blender": (4, 2, 0),
    "location": "3D View > Toolbar (Object Mode) / Add > Mesh / Sidebar > EZ",
    "description": "SketchUp-style click-drag-click parametric cylinders, polygons, cones, spheres, quad spheres and boxes with push/pull, boolean cuts and 3D cursor snapping",
    "category": "3D View",
}

from . import gizmos, keymaps, merge_recapture, merge_sphere_experiment, merge_templates, operators, settings, tools, ui  # noqa: E402

_modules = (settings, operators, merge_recapture, merge_sphere_experiment, merge_templates, gizmos, tools, ui, keymaps)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
