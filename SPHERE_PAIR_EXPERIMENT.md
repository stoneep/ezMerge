# Sphere Pair experiment

Two entry points in the EZ sidebar:

1. Select two overlapping sphere Mesh objects in Object Mode, then press
   **Merge Two Spheres (Experimental)** in Merge Template.
2. Select one sphere, choose another with the Target eyedropper, set both
   template types to Sphere, and press **Merge Spheres (Experimental)**.

This now runs the measured-profile sphere experiment, NOT Bead Preview.
Selection alone never modifies a scene.

The operation captures both evaluated meshes in their current world placement,
performs Exact Boolean Union on copies, measures its radial profile around the
line joining the sphere centers, and builds a Geometry Nodes Hermite band.
It does not generate two replacement ideal spheres or depend on external scripts.

Originals retain their data and modifiers and are hidden only after success.
The new selected result exposes Strength, Region Center Z, Lower Reach,
Upper Reach, Edge Fade and Show Region. Z is local to the sphere-pair axis.
Strength zero shows the captured Boolean union, not the two original meshes.
Restore Two Original Spheres removes this result/helper and restores the source
visibility. Blender Undo is also available. Restore does not delete unrelated data.

Limitations: prototype for closed, near-spherical meshes (3.5% radial deviation
tolerance), with actual partial overlap. No separated, tangent, fully contained,
ellipsoidal or arbitrary sculpted shapes. Uniformly scaled and arbitrarily oriented
pairs are supported. No live recapture when sources move; restore and run again.
The Boolean's existing resolution is retained; no remesh or additional subdivision
is performed. Coarse inputs can remain faceted. Radial averaging and cubic fitting
do not guarantee no folds, tiny faces, perfect curvature or production topology.
Input materials/UV semantics are not validated. Other eight template pairs remain
the existing Bead preview. Installed extension copies are not updated automatically.

Verified in Steam Blender 5.1.2 with actual selected 1:1, 3:1 and 4:1 spheres,
an oblique world axis, source-data preservation, coordinate-changing controls,
save/reload, restore, separated-pair rollback and eyedropper template dispatch.
These are integration checks, not proof of visual/topological quality for all inputs.
