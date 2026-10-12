"""Stable socket names, serialized tags and defaults for all merge paths."""

import math

MERGE_KEY = "ezhs_merge"
MERGE_VERSION = 10
MERGE_OWN_KEY = "ezhs_merge_own"  # marks a group copied for one modifier (it has its own custom curve)
CURVE_NODE = "Custom Curve"
MERGE_GROUP_NAME = "EZ Merge"
MERGE_MODIFIER = "EZ Merge"

BASE = "Base"
GLOBAL = "Global Merge"
RADIUS = "Radius"
STRENGTH = "Strength"
FALLOFF = "Falloff"
SUBDIVISION = "Subdivision"
CONTACT = "Contact Rows"
OWN_NORMAL = "ezhs_own_normal"
HEIGHT = "Original Height"
SPREAD = "Spread"
PROFILE = "Profile"
LIFT = "Spread Lift"
SMOOTH_ANGLE = "Smooth Angle"
FLARE_KEY = "ezhs_merge_flare"
FLARE_VERSION = 2

DEFORM, ROWS, BEVEL, BEAD = 'DEFORM', 'ROWS', 'BEVEL', 'BEAD'
METHODS = (DEFORM, ROWS, BEVEL, BEAD)
BEAD_KEY = "ezhs_merge_bead"
BEAD_VERSION = 13
BEAD_GROUP_NAME = "EZ Merge Bead"
ADHESION_KEY = "ezhs_surface_adhesion"
ADHESION_VERSION = 7
# Builder policy values; changing their behavior requires an adhesion version bump.
ADHESION_DEFAULT_LEVELS = 4
ADHESION_SPREAD_STEPS = 4
ADHESION_SPREAD_MAX_STEPS = 8
ADHESION_SPREAD_TURN_RADIANS = math.radians(12.0)
ADHESION_MAX_TURN_COS = 0.7071
ADHESION_MIN_TANGENT_LENGTH = 0.1
ADHESION_NORMAL_BLEND_FRACTION = 0.03
INFLUENCE_RADIUS = "Influence Radius"
MERGE_RADIUS = "Merge Radius"
PUSH_STRENGTH = "Push Strength"
SURFACE_SPREAD = "Surface Spread"
SURFACE_CLEARANCE = "Surface Clearance"
SURFACE_CORNER_REACH = "Corner Reach"
SURFACE_RELAX = "Spread Relax"
SURFACE_RELAX_PASSES = "Relax Passes"
ADHESION_LEVELS = "Adhesion Levels"
ADHESION_LOCAL_SUBDIVISION = "Local Subdivision"
ADHESION_ADAPTIVE_SPREAD = "Adaptive Spread"
ADHESION_WEIGHT = "ezhs_adhesion_weight"
ADHESION_DISTANCE = "ezhs_adhesion_distance"
ADHESION_SOURCE_POSITION = "ezhs_adhesion_source_position"
ADHESION_SPREAD_PLANNED = "ezhs_spread_planned_steps"
ADHESION_SPREAD_TAKEN = "ezhs_spread_taken_steps"
RESOLUTION = "Resolution"
BEAD_PROFILE_RESOLUTION = "Profile Resolution"
DEFAULT_BEAD_RADIUS = 0.08
DEFAULT_BEAD_RESOLUTION = 12
BEAD_BULGE = "Bulge"
BEAD_WIDTH = "Contact Width"
BEAD_HEIGHT = "Contact Height"
BEAD_CUSTOM_CURVE = "Custom Curve"
BEAD_SYNC_NORMALS = "Sync Normals"
BEAD_MATERIAL = "Material"
BEAD_CONTACT_CUTS = "Contact Subdivisions"
BEAD_SEAM_SAMPLES = "Seam Samples"
BEAD_SURFACE_SUBDIVISION = "Surface Subdivision"
BEAD_SURFACE_LEVELS = "Surface Levels"
BEAD_SUBDIVISION_ORDER = "Subdivision Order"
AFTER_SNAP, BEFORE_SNAP = 0, 1
BEAD_GEOMETRIC_NORMAL = "ezhs_bead_geometric_normal"
BEAD_FACE_ESCAPE = "ezhs_bead_face_escape"
BEAD_CONTACT_ALIGNMENT = "ezhs_bead_contact_alignment"
BEAD_POSITIVE_IS_B = "ezhs_bead_positive_is_b"
BEAD_SEAM_TANGENT = "ezhs_bead_seam_tangent"
MIN_BEAD_RESOLUTION, MAX_BEAD_RESOLUTION = 3, 32
# per-point attributes passed from the contact curve + sweep profile to the final Set Position
BEAD_BISECTOR = "ezhs_bead_bisector"
BEAD_BINORMAL = "ezhs_bead_binormal"
BEAD_NORMAL_A = "ezhs_bead_normal_a"
BEAD_CURVE_POS = "ezhs_bead_curve_pos"
BEAD_SIN_BETA = "ezhs_bead_sin_beta"
BEAD_COS_BETA = "ezhs_bead_cos_beta"
BEAD_ARC_T = "ezhs_bead_arc_t"
BEAD_NORMAL = "ezhs_bead_shading_normal"
BEAD_A_WEIGHT = "ezhs_bead_a_normal_weight"
BEAD_B_WEIGHT = "ezhs_bead_b_normal_weight"
ROWS_PREP_KEY = "ezhs_merge_rows_prep"
ROWS_COUNT = 16           # Bevel segments: half of them are the rows between the original and the spread edge
ROWS_SUBDIVISION = 0      # the rows already follow the contact; raise it for shapes with few columns
PREP_KEY = "ezhs_merge_prep"
FINISH_KEY = "ezhs_merge_finish"
BEVEL_PART_VERSION = 2
DEFAULT_BEVEL_SEGMENTS = 8

SMOOTH, SOFT, LINEAR, CUSTOM, SHARP, SPHERE = 0, 1, 2, 3, 4, 5
FALLOFFS = (("Smooth", SMOOTH), ("Soft", SOFT), ("Linear", LINEAR), ("Sharp", SHARP), ("Sphere", SPHERE),
            ("Custom", CUSTOM))
# the custom curve starts as the Smooth profile: spread fraction (y) against the distance from the surface (x)
DEFAULT_CURVE = tuple((u, 1.0 - math.sin(u * math.pi / 2.0)) for u in (0.0, 0.12, 0.3, 0.5, 0.72, 1.0))
DEFAULT_SUBDIVISION = 3
MIN_SUBDIVISION, MAX_SUBDIVISION = 0, 7
MAX_AUTO_RINGS = 128


SURFACE_NORMAL = "ezhs_surface_normal"
NORMAL_NEIGHBOURS = 6           # faces around a vertex that are looked at when smoothing a normal
NORMAL_ANGLE = math.radians(35.0)  # faces meeting at a sharper angle are not averaged (the same rule as shading by angle)


# Explicitly exclude imported modules from the compatibility re-export.
__all__ = tuple(name for name in globals() if name.isupper())
