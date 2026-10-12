import bpy
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty,
)
from mathutils import Euler, Matrix, Vector

from . import cuts, joins, merges
from . import shapes as cn
from .controllers import BoxCreate, ConeCreate, CylinderCreate, QuadSphereCreate, ShapePushPull, ShapeRotate, SphereCreate
from .geometry import mouse_ray, scene_raycast
from .handles import DIALS, handle_pose, upright_matrix
from .settings import HANDLE_MODES, ORIGIN_MODES

CYLINDER_TOOL_ID = "ezhs.cylinder_tool"
POLYGON_TOOL_ID = "ezhs.polygon_tool"
CONE_TOOL_ID = "ezhs.cone_tool"
SPHERE_TOOL_ID = "ezhs.sphere_tool"
QUADSPHERE_TOOL_ID = "ezhs.quadsphere_tool"
BOX_TOOL_ID = "ezhs.box_tool"
PUSHPULL_TOOL_ID = "ezhs.pushpull_tool"


def _in_view3d(context):
    return (context.area is not None and context.area.type == 'VIEW_3D'
            and context.region is not None and context.region.type == 'WINDOW'
            and context.region_data is not None)


def _run_modal(op, context, event):
    result = op._ctl.handle(context, event)
    if result == 'PASS':
        return {'PASS_THROUGH'}
    if result == 'RUNNING':
        return {'RUNNING_MODAL'}
    if result == 'CANCELLED':
        op._ctl.cancel(context)
    op._ctl.end(context)
    return {result}


class _CreateOperator:
    """Shared logic of the three create operators (cylinder, polygon, box).

    Subclasses are registered operators that also declare their own size
    properties and implement ``make_controller``, ``build`` and ``record``.
    """

    bl_options = {'REGISTER', 'UNDO'}
    tool_id = ""

    location: FloatVectorProperty(name="Location", subtype='TRANSLATION', options={'SKIP_SAVE'})
    rotation: FloatVectorProperty(name="Rotation", subtype='EULER', options={'SKIP_SAVE'})
    cut: BoolProperty(name="Cut", description="Subtract the shape from the mesh it was drawn on",
                      default=False, options={'SKIP_SAVE'})
    cut_operation: EnumProperty(
        name="Operation", description="What the shape does to the mesh it was drawn on",
        items=(('DIFFERENCE', "Cut", "Cut a hole"), ('UNION', "Add", "Merge the shape into the mesh"),
               ('INTERSECT', "Intersect", "Keep only where the shape and the mesh overlap")),
        default='DIFFERENCE', options={'SKIP_SAVE'})
    cut_target: StringProperty(name="Target", description="Mesh object to cut into",
                               options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def make_controller(self, context, mouse):
        raise NotImplementedError

    def build(self, context, matrix):
        raise NotImplementedError

    def record(self, ctl):
        """Copy the finished shape's values into the operator properties (for redo)."""
        raise NotImplementedError

    def execute(self, context):
        # Called without a place (a script, no viewport): like Blender's own Add menu, the shape goes to the 3D cursor.
        cursor = context.scene.cursor
        location = Vector(self.location) if self.properties.is_property_set("location") else cursor.location.copy()
        rotation = Euler(self.rotation) if self.properties.is_property_set("rotation") else cursor.matrix.to_euler()
        matrix = Matrix.LocRotScale(location, rotation, None)
        obj = self.build(context, matrix)
        target = bpy.data.objects.get(self.cut_target)
        if self.cut and cuts.can_cut(target):
            cuts.add_cut(target, obj, self.cut_operation)
        return {'FINISHED'}

    def invoke(self, context, event):
        if not _in_view3d(context):
            return self.execute(context)
        self._ctl = self.make_controller(context, (event.mouse_region_x, event.mouse_region_y))
        self._ctl.start(context)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # Started from the tool: this click already places the anchor.
            self._ctl.handle(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        result = _run_modal(self, context, event)
        if result == {'FINISHED'}:
            ctl = self._ctl
            self.record(ctl)
            self.location = ctl.center
            self.rotation = ctl.rotation.to_euler()
            self.cut = ctl.cut
            self.cut_operation = ctl.cut_op
            self.cut_target = ctl.target.name if ctl.cut else ""
            self._remember_segments(context)
        return result

    def _remember_segments(self, context):
        """The tool keeps the last number of segments/sides."""
        if not hasattr(self, "segments"):
            return
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        if tool is not None and tool.idname == self.tool_id:
            tool.operator_properties(self.bl_idname).segments = self.segments


def _origin_property():
    return EnumProperty(
        name="Origin", description="Where the origin of the shape is: the center of its base, or (Center (3D)) "
                                   "halfway up. A round shape has no corner: Corner is the center of the base too",
        items=ORIGIN_MODES, default='CORNER', options={'SKIP_SAVE'})


def _lift(matrix, origin, height):
    """``matrix`` (at the center of the base) moved up to the middle of the shape for Center (3D)."""
    return matrix @ Matrix.Translation((0.0, 0.0, 0.5 * height)) if origin == 'CENTER3D' else matrix


class EZHS_OT_cylinder_create(_CreateOperator, bpy.types.Operator):
    """Draw a parametric cylinder: click the center, click the radius, click the height"""

    bl_idname = "ezhs.cylinder_create"
    bl_label = "EZ Cylinder"
    tool_id = CYLINDER_TOOL_ID

    radius: FloatProperty(name="Radius", default=1.0, min=0.0, subtype='DISTANCE',
                          options={'SKIP_SAVE'})
    height: FloatProperty(name="Height", default=2.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    segments: IntProperty(name="Segments", default=32, min=cn.MIN_SEGMENTS, max=cn.MAX_SEGMENTS)
    origin: _origin_property()

    def make_controller(self, context, mouse):
        return CylinderCreate(context, mouse, self.segments)

    def build(self, context, matrix):
        return cn.create_cylinder(context, _lift(matrix, self.origin, self.height), self.radius, self.height,
                                  self.segments, centered_z=self.origin == 'CENTER3D')

    def record(self, ctl):
        self.radius, self.height, self.segments = ctl.radius, ctl.height, ctl.segments
        self.origin = 'CENTER3D' if ctl.origin_3d else 'CORNER'


class EZHS_OT_polygon_create(_CreateOperator, bpy.types.Operator):
    """Draw a polygon prism: click the center, click a vertex (sets size and rotation), click the height"""

    bl_idname = "ezhs.polygon_create"
    bl_label = "EZ Polygon"
    tool_id = POLYGON_TOOL_ID

    radius: FloatProperty(name="Radius", description="Distance from the center to a vertex",
                          default=1.0, min=0.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    height: FloatProperty(name="Height", default=2.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    segments: IntProperty(name="Sides", default=6, min=cn.MIN_SEGMENTS, max=cn.MAX_SEGMENTS)
    origin: _origin_property()

    def make_controller(self, context, mouse):
        return CylinderCreate(context, mouse, self.segments, polygon=True)

    def build(self, context, matrix):
        return cn.create_cylinder(context, _lift(matrix, self.origin, self.height), self.radius, self.height,
                                  self.segments, name="EZ Polygon", smooth=False,
                                  centered_z=self.origin == 'CENTER3D')

    def record(self, ctl):
        self.radius, self.height, self.segments = ctl.radius, ctl.height, ctl.segments
        self.origin = 'CENTER3D' if ctl.origin_3d else 'CORNER'


class EZHS_OT_cone_create(_CreateOperator, bpy.types.Operator):
    """Draw a cone or frustum: click the center, the base radius, the height, then the top radius"""

    bl_idname = "ezhs.cone_create"
    bl_label = "EZ Cone"
    tool_id = CONE_TOOL_ID

    radius: FloatProperty(name="Radius", description="Radius of the base", default=1.0, min=0.0,
                          subtype='DISTANCE', options={'SKIP_SAVE'})
    top_radius: FloatProperty(name="Top Radius", description="Radius of the top (0 = pointed)", default=0.0,
                              min=0.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    height: FloatProperty(name="Height", default=2.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    segments: IntProperty(name="Segments", default=32, min=cn.MIN_SEGMENTS, max=cn.MAX_SEGMENTS)
    origin: _origin_property()

    def make_controller(self, context, mouse):
        return ConeCreate(context, mouse, self.segments)

    def build(self, context, matrix):
        return cn.create_cone(context, _lift(matrix, self.origin, self.height), self.radius, self.top_radius,
                              self.height, self.segments, centered_z=self.origin == 'CENTER3D')

    def record(self, ctl):
        self.radius, self.top_radius, self.height, self.segments = (
            ctl.radius, ctl.top_radius, ctl.height, ctl.segments)
        self.origin = 'CENTER3D' if ctl.origin_3d else 'CORNER'


class EZHS_OT_sphere_create(_CreateOperator, bpy.types.Operator):
    """Draw a sphere: click the center, then click the radius"""

    bl_idname = "ezhs.sphere_create"
    bl_label = "EZ Sphere"
    tool_id = SPHERE_TOOL_ID

    radius: FloatProperty(name="Radius", default=1.0, min=0.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    segments: IntProperty(name="Segments", default=32, min=cn.MIN_SEGMENTS, max=cn.MAX_SEGMENTS)

    def make_controller(self, context, mouse):
        return SphereCreate(context, mouse, self.segments)

    def build(self, context, matrix):
        return cn.create_sphere(context, matrix, self.radius, self.segments)

    def record(self, ctl):
        self.radius, self.segments = ctl.radius, ctl.segments


class EZHS_OT_quadsphere_create(_CreateOperator, bpy.types.Operator):
    """Draw a quad sphere (all quads, no poles): click the center, then click the radius"""

    bl_idname = "ezhs.quadsphere_create"
    bl_label = "EZ Quad Sphere"
    tool_id = QUADSPHERE_TOOL_ID

    radius: FloatProperty(name="Radius", default=1.0, min=0.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    segments: IntProperty(name="Segments", description="Quads around a great circle (4 per grid edge)",
                          default=32, min=cn.MIN_SEGMENTS, max=cn.MAX_SEGMENTS)

    def make_controller(self, context, mouse):
        return QuadSphereCreate(context, mouse, self.segments)

    def build(self, context, matrix):
        return cn.create_quadsphere(context, matrix, self.radius, self.segments)

    def record(self, ctl):
        self.radius, self.segments = ctl.radius, ctl.segments


class EZHS_OT_box_create(_CreateOperator, bpy.types.Operator):
    """Draw a box: click a corner (or the center, with Draw start Center), size the base, click the height"""

    bl_idname = "ezhs.box_create"
    bl_label = "EZ Box"
    tool_id = BOX_TOOL_ID

    width: FloatProperty(name="Width", default=1.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    depth: FloatProperty(name="Depth", default=1.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    height: FloatProperty(name="Height", default=1.0, subtype='DISTANCE', options={'SKIP_SAVE'})
    from_center: BoolProperty(
        name="From Center", description="The location is the center of the base (otherwise a corner of it)",
        default=False, options={'SKIP_SAVE'})
    origin: EnumProperty(
        name="Origin", description="Where the origin of the box is", items=ORIGIN_MODES, default='CORNER',
        options={'SKIP_SAVE'})
    origin_center: BoolProperty(
        name="Origin at Center", description="Same as Origin = Center (Plane) (kept for scripts)",
        default=False, options={'SKIP_SAVE'})

    def make_controller(self, context, mouse):
        return BoxCreate(context, mouse)

    def build(self, context, matrix):
        # ``location`` is the center or a corner of the base; the object's origin is a corner, the center of the
        # base or the center of the whole box.
        origin = self.origin if self.origin != 'CORNER' else ('PLANE' if self.origin_center else 'CORNER')
        shift = (-0.5 if self.from_center else 0.0) + (0.5 if origin != 'CORNER' else 0.0)
        shift_z = 0.5 * self.height if origin == 'CENTER3D' else 0.0
        matrix = matrix @ Matrix.Translation((shift * self.width, shift * self.depth, shift_z))
        return cn.create_box(context, matrix, self.width, self.depth, self.height, centered=origin != 'CORNER',
                             centered_z=origin == 'CENTER3D')

    def record(self, ctl):
        self.width, self.depth, self.height = ctl.width, ctl.depth, ctl.height
        self.from_center = ctl.centered
        self.origin, self.origin_center = ctl.origin_mode, False


class EZHS_OT_pushpull(bpy.types.Operator):
    """Push/pull an EZ shape: drag a face to change its size, a cylinder's side to change the radius"""

    bl_idname = "ezhs.pushpull"
    bl_label = "EZ Push/Pull"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def invoke(self, context, event):
        if not _in_view3d(context):
            return {'CANCELLED'}
        mouse = (event.mouse_region_x, event.mouse_region_y)
        hit = scene_raycast(context, *mouse_ray(context.region, context.region_data, mouse))
        obj = hit[2] if hit else None
        mod = cn.find_modifier(obj)

        if mod is None or obj.library is not None:
            # Not an EZ shape: behave like a plain selection click.
            if not event.shift:
                for other in context.selected_objects:
                    other.select_set(False)
            if obj is not None and obj.visible_get():
                obj.select_set(True)
                context.view_layer.objects.active = obj
            return {'FINISHED'}

        obj.select_set(True)
        context.view_layer.objects.active = obj
        self._ctl = ShapePushPull(context, mouse, obj, mod, hit[0], hit[1])
        self._ctl.press_mouse = self._ctl.mouse.copy()
        self._ctl.start(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return _run_modal(self, context, event)


class EZHS_OT_shape_handle(bpy.types.Operator):
    """Drag a handle of the active EZ shape (started by the viewport gizmos)"""

    bl_idname = "ezhs.shape_handle"
    bl_label = "EZ Shape Handle"
    bl_options = {'UNDO', 'INTERNAL'}

    part: EnumProperty(
        name="Handle",
        items=(('TOP', "Top", "Change the height, base stays"),
               ('BOTTOM', "Bottom", "Change the height, top stays"),
               ('SIDE', "Side", "Change the radius (the base radius of a cone)"),
               ('TOPR', "Top radius", "Change the radius of the top of a cone"),
               ('X1', "Right", "Change the width, the left face stays"),
               ('X0', "Left", "Change the width, the right face stays"),
               ('Y1', "Back", "Change the depth, the front face stays"),
               ('Y0', "Front", "Change the depth, the back face stays"),
               ('ROTATE', "Rotate", "Turn the shape about its axis"),
               ('ROTATE_X', "Tilt X", "Tilt the shape about its own X axis"),
               ('ROTATE_Y', "Tilt Y", "Tilt the shape about its own Y axis")),
        options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and cn.find_modifier(context.active_object) is not None

    def invoke(self, context, event):
        if not _in_view3d(context):
            return {'CANCELLED'}
        obj = context.active_object
        mod = cn.find_modifier(obj)
        location, direction = handle_pose(obj, mod, self.part)
        mouse = (event.mouse_region_x, event.mouse_region_y)
        if self.part in DIALS:
            self._ctl = ShapeRotate(context, mouse, obj, mod, axis_index=DIALS[self.part])
        else:
            self._ctl = ShapePushPull(context, mouse, obj, mod, location, direction, part=self.part)
        # Press-drag-release confirms; a plain click switches to click-click mode.
        self._ctl.press_mouse = self._ctl.mouse.copy()
        self._ctl.start(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return _run_modal(self, context, event)


class EZHS_OT_shape_apply(bpy.types.Operator):
    """Turn the selected EZ shapes into regular meshes (the parameters are lost)"""

    bl_idname = "ezhs.shape_apply"
    bl_label = "Convert to Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(
            cn.find_modifier(o) for o in context.selected_objects)

    def execute(self, context):
        count = 0
        for obj in context.selected_objects:
            mod = cn.find_modifier(obj)
            if mod is None:
                continue
            with context.temp_override(object=obj, active_object=obj):
                bpy.ops.object.modifier_apply(modifier=mod.name)
            count += 1
        self.report({'INFO'}, f"Converted {count} shape(s) to mesh")
        return {'FINISHED'}


class EZHS_OT_handle_mode(bpy.types.Operator):
    """Switch which handles the selected EZ shape shows: all, only the resize arrows, or only the rotation dial"""

    bl_idname = "ezhs.handle_mode"
    bl_label = "EZ Handle Mode"
    bl_options = {'INTERNAL'}

    mode: EnumProperty(name="Mode", items=(('NEXT', "Next", "The next mode"),) + HANDLE_MODES, default='NEXT',
                       options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (context.mode == 'OBJECT' and context.active_object is not None
                and cn.find_modifier(context.active_object) is not None)

    def execute(self, context):
        order = [item[0] for item in HANDLE_MODES]
        wm = context.window_manager
        wm.ezhs_handles = (order[(order.index(wm.ezhs_handles) + 1) % len(order)]
                           if self.mode == 'NEXT' else self.mode)
        self.report({'INFO'}, f"Handles: {dict((i[0], i[1]) for i in HANDLE_MODES)[wm.ezhs_handles]}")
        return {'FINISHED'}


class EZHS_OT_shape_upright(bpy.types.Operator):
    """Stand the selected EZ shapes upright: their Z axis points straight up again (turning about their origin)"""

    bl_idname = "ezhs.shape_upright"
    bl_label = "Upright"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _tilted(context):
        return [o for o in context.selected_objects
                if cn.find_modifier(o) is not None and upright_matrix(o.matrix_world) is not None]

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(cls._tilted(context))

    def execute(self, context):
        shapes = self._tilted(context)
        for obj in shapes:
            obj.matrix_world = upright_matrix(obj.matrix_world)
        self.report({'INFO'}, f"Stood {len(shapes)} shape(s) upright")
        return {'FINISHED'}


class EZHS_OT_cut_apply(bpy.types.Operator):
    """Bake the EZ cuts of the selected meshes and delete their cutter cylinders"""

    bl_idname = "ezhs.cut_apply"
    bl_label = "Apply Cuts"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(
            cuts.cut_modifiers(o) for o in context.selected_objects)

    def execute(self, context):
        done = 0
        for obj in list(context.selected_objects):
            if not cuts.cut_modifiers(obj):
                continue
            count, problem = cuts.apply_cuts(context, obj)
            done += count
            if problem:
                self.report({'WARNING'}, f"{obj.name}: {problem}")
        self.report({'INFO'}, f"Applied {done} modifier(s)")
        return {'FINISHED'} if done else {'CANCELLED'}


class EZHS_OT_cut_release(bpy.types.Operator):
    """Stop cutting: the shape becomes a normal object again and the hole is removed"""

    bl_idname = "ezhs.cut_release"
    bl_label = "Release Cut"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and cuts.is_cutter(context.active_object)

    def execute(self, context):
        cuts.remove_cut(context.active_object)
        return {'FINISHED'}


class EZHS_OT_cut_operation(bpy.types.Operator):
    """Change what the active cutter does to its mesh: cut, add, intersect or join with a fillet"""

    bl_idname = "ezhs.cut_operation"
    bl_label = "Cutter Operation"
    bl_options = {'REGISTER', 'UNDO'}

    operation: EnumProperty(name="Operation", items=[
        (cuts.DIFFERENCE, "Cut", "Cut a hole into the mesh"),
        (cuts.UNION, "Add", "Add the shape to the mesh"),
        (cuts.INTERSECT, "Intersect", "Keep only where the shape and the mesh overlap"),
        (cuts.JOIN, "Join", "Add the shape with a rounded fillet where they meet"),
    ], default=cuts.DIFFERENCE)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and cuts.is_cutter(context.active_object)

    def execute(self, context):
        if self.operation == cuts.JOIN and not joins.available():
            self.report({'ERROR'}, "Joining needs the SDF grid nodes of Blender 4.2 or later")
            return {'CANCELLED'}
        cuts.set_operation(context.active_object, self.operation)
        return {'FINISHED'}


class EZHS_OT_join_shapes(bpy.types.Operator):
    """Join the selected shapes to the active mesh with a soldered / silicone-like fillet"""

    bl_idname = "ezhs.join_shapes"
    bl_label = "Join Selected"
    bl_options = {'REGISTER', 'UNDO'}

    fillet: FloatProperty(name="Fillet", description="Bead radius (0: automatic, from the size of the shapes)",
                          default=0.0, min=0.0, subtype='DISTANCE')

    @staticmethod
    def _others(context):
        active = context.active_object
        return [o for o in context.selected_objects
                if o != active and o.type == 'MESH' and not cuts.is_cutter(o) and o.library is None]

    @classmethod
    def poll(cls, context):
        return (context.mode == 'OBJECT' and cuts.can_cut(context.active_object)
                and bool(cls._others(context)))

    def execute(self, context):
        if not joins.available():
            self.report({'ERROR'}, "Joining needs the SDF grid nodes of Blender 4.2 or later")
            return {'CANCELLED'}
        target, count = context.active_object, 0
        for other in self._others(context):
            mod = cuts.add_cut(target, other, cuts.JOIN)
            if self.fillet > 0.0:
                cn.set_input(mod, joins.FILLET, self.fillet)
            count += 1
        target.update_tag()
        self.report({'INFO'}, f"Joined {count} shape(s) to {target.name}")
        return {'FINISHED'}


class EZHS_OT_merge_shapes(bpy.types.Operator):
    """Merge the active mesh onto the other selected mesh: it keeps its form and spreads over that surface"""

    bl_idname = "ezhs.merge_shapes"
    bl_label = "Merge Selected"
    bl_options = {'REGISTER', 'UNDO'}

    radius: FloatProperty(name="Radius", description="Surface merge radius or legacy flare height (0: automatic, from mesh size)",
                          default=0.0, min=0.0, subtype='DISTANCE')
    strength: FloatProperty(name="Strength", description="Tangential push for surface adhesion (0: default from radius)",
                            default=0.0, min=0.0, subtype='DISTANCE')
    method: EnumProperty(name="Method", items=[
        ('AUTO', "Auto", "Surface adhesion with independent influence, merge and push controls"),
        (merges.BEAD, "Bead", "Deform the exposed contact patch of A onto B; source mesh data stays intact"),
        (merges.ROWS, "Rows", "Spread the shape over the other surface; Blender's Bevel adds rows along the contact line first (small and clean)"),
        (merges.DEFORM, "Deform", "Spread the shape over the other surface; the shape is only subdivided (heavier, needs a dense mesh)"),
        (merges.BEVEL, "Bevel", "Unite both meshes and bevel the contact line (a plain fillet; needs flat or smooth surfaces)"),
    ], default='AUTO')

    @staticmethod
    def _bases(context):
        active = context.active_object
        return [o for o in context.selected_objects
                if o != active and o.type == 'MESH' and o.library is None]

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return (context.mode == 'OBJECT' and active is not None and active.type == 'MESH'
                and active.library is None and bool(cls._bases(context)))

    def execute(self, context):
        if not merges.available():
            self.report({'ERROR'}, "Merging needs Blender 4.2 or later")
            return {'CANCELLED'}
        obj, count = context.active_object, 0
        for base in self._bases(context):
            method = merges.suggested_method(base) if self.method == 'AUTO' else self.method
            merges.add_merge(obj, base, self.radius or None, self.strength or None, method)
            count += 1
        obj.update_tag()
        self.report({'INFO'}, f"Merged {obj.name} onto {count} mesh(es)")
        return {'FINISHED'}


class EZHS_OT_merge_remove(bpy.types.Operator):
    """Remove the merge: the mesh returns to its own form"""

    bl_idname = "ezhs.merge_remove"
    bl_label = "Remove Merge"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier", description="Name of the merge to remove (empty: all of them)")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None and bool(
            merges.merge_modifiers(context.active_object))

    def execute(self, context):
        obj = context.active_object
        for mod in merges.merge_modifiers(obj):
            if not self.modifier or mod.name == self.modifier:
                merges.remove_modifier(obj, mod)
        return {'FINISHED'}


class EZHS_OT_merge_apply(bpy.types.Operator):
    """Bake the merge into the mesh (the other mesh is not touched)"""

    bl_idname = "ezhs.merge_apply"
    bl_label = "Apply Merge"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None and bool(
            merges.merge_modifiers(context.active_object))

    def execute(self, context):
        count = merges.apply_merge(context, context.active_object)
        self.report({'INFO'}, f"Applied {count} modifier(s)")
        return {'FINISHED'} if count else {'CANCELLED'}


class EZHS_OT_merge_method(bpy.types.Operator):
    """Redo this merge with the other method (Deform or Bevel), keeping the other mesh and the size"""

    bl_idname = "ezhs.merge_method"
    bl_label = "Merge Method"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier")
    method: EnumProperty(name="Method", items=[(merges.ROWS, "Rows", ""), (merges.DEFORM, "Deform", ""),
                                               (merges.BEVEL, "Bevel", ""), (merges.BEAD, "Bead", "")],
                         default=merges.ROWS)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(self.modifier)
        if mod is None or not merges.is_merge_modifier(mod):
            return {'CANCELLED'}
        merges.convert(obj, mod, self.method)
        obj.update_tag()
        return {'FINISHED'}


class EZHS_OT_adhesion_patch_create(bpy.types.Operator):
    """Freeze Surface Spread's contact band; this snapshot does not follow later edits"""

    bl_idname = "ezhs.adhesion_patch_create"
    bl_label = "Create Contact Patch"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(self.modifier)
        if mod is None:
            return {'CANCELLED'}
        try:
            patch = merges.create_adhesion_patch(obj, mod)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'{patch.name}: fixed snapshot, {len(patch.data.polygons)} faces; no simulation yet')
        return {'FINISHED'}


class EZHS_OT_bead_subdivision_order(bpy.types.Operator):
    """Choose whether to subdivide the bead before fitting or smooth it afterwards"""

    bl_idname = "ezhs.bead_subdivision_order"
    bl_label = "Subdivision Order"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier")
    order: IntProperty(name="Order", default=merges.AFTER_SNAP, min=0, max=1)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(self.modifier)
        if (mod is None or not merges.is_merge_modifier(mod) or merges.method_of(mod) != merges.BEAD
                or mod.node_group.interface.items_tree.get(merges.BEAD_SUBDIVISION_ORDER) is None):
            return {'CANCELLED'}
        merges.cn.set_input(mod, merges.BEAD_SUBDIVISION_ORDER, self.order)
        obj.update_tag()
        return {'FINISHED'}


class EZHS_OT_bead_curve_own(bpy.types.Operator):
    """Give a duplicated object's bead an independently editable curve"""

    bl_idname = "ezhs.bead_curve_own"
    bl_label = "Make Curve Independent"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(self.modifier)
        if (mod is None or not merges.is_merge_modifier(mod)
                or merges.method_of(mod) != merges.BEAD or merges.curve_node(mod) is None):
            return {'CANCELLED'}
        merges.own_curve(mod)
        obj.update_tag()
        return {'FINISHED'}


class EZHS_OT_bead_curve_reset(bpy.types.Operator):
    """Reset the shoulder curve without changing contact width or height"""

    bl_idname = "ezhs.bead_curve_reset"
    bl_label = "Reset Curve"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(self.modifier)
        if mod is None or not merges.is_merge_modifier(mod):
            return {'CANCELLED'}
        try:
            merges.reset_bead_curve(obj, mod)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class EZHS_OT_merge_falloff(bpy.types.Operator):
    """Choose the profile of the flare"""

    bl_idname = "ezhs.merge_falloff"
    bl_label = "Falloff"
    bl_options = {'REGISTER', 'UNDO'}

    modifier: StringProperty(name="Modifier")
    falloff: IntProperty(name="Falloff", default=merges.SMOOTH, min=merges.SMOOTH, max=merges.SPHERE)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(self.modifier)
        if (mod is None or not merges.is_merge_modifier(mod) or merges.method_of(mod) == merges.BEVEL
                or (merges.method_of(mod) == merges.BEAD and not merges.is_surface_adhesion(mod))):
            return {'CANCELLED'}
        try:
            merges.set_falloff(obj, mod, self.falloff)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (
    EZHS_OT_cylinder_create,
    EZHS_OT_polygon_create,
    EZHS_OT_cone_create,
    EZHS_OT_sphere_create,
    EZHS_OT_quadsphere_create,
    EZHS_OT_box_create,
    EZHS_OT_pushpull,
    EZHS_OT_shape_handle,
    EZHS_OT_shape_apply,
    EZHS_OT_shape_upright,
    EZHS_OT_handle_mode,
    EZHS_OT_cut_apply,
    EZHS_OT_cut_release,
    EZHS_OT_cut_operation,
    EZHS_OT_join_shapes,
    EZHS_OT_merge_shapes,
    EZHS_OT_merge_remove,
    EZHS_OT_merge_apply,
    EZHS_OT_merge_falloff,
    EZHS_OT_merge_method,
    EZHS_OT_bead_curve_reset,
    EZHS_OT_bead_curve_own,
    EZHS_OT_bead_subdivision_order,
    EZHS_OT_adhesion_patch_create,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
