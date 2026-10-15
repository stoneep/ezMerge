# Experimental Recapture

This is an additive experimental mode. Existing Bead behavior is unchanged.

## Workflow

1. Select an object with an existing surface-adhesion Bead. In its Bead panel,
   choose **Recapture (Experimental)**.
2. A new object contains a snapshot of the currently evaluated shape and a
   separate adjustable Geometry Nodes modifier. The original object and its
   settings remain unchanged and are hidden, not deleted. Target B is unchanged.
3. Adjust Global Merge, Influence Radius, additional Pull (Merge Radius),
   Falloff/Custom Curve, and Sync Normals. The captured input is not overwritten
   while adjusting. Only its recorded contact region can move.
4. Choose **Save New Base** to absorb the current evaluated adjustment into the
   next baseline. Additional Pull and normal transfer are reset so the saved
   change is not applied twice. The adjustment modifier remains available.
5. **Recapture from Original** re-evaluates the original Bead stack at the
   current result object's pose; it replaces the experimental baseline and
   discards unsaved experimental adjustments. This does not modify the original.
6. **Restore Original** removes the generated experimental object and reveals
   the original, restoring its previous viewport/render visibility. The UI asks
   for confirmation. Save a .blend first if the experimental result is needed.

## Important limits

- **Capturing is not automatic unfolding, topology repair, or the rounded
  cylinder-patch reconstruction from the research scenes.** A folded current
  Bead result is captured as-is. No generic cleanup solver is included here.
- Push, Spread, Contact Core, extra subdivision and their dependent settings
  are disabled at node level, not just hidden. The existing formulas produced
  new intersections after capture and are not yet appropriate for this mode.
- Pull and normal transfer are experimental, not safe for every mesh/value.
  Inspect the result before saving. Large values can still damage geometry.
- The contact mask is frozen at capture. Moving A/B or changing the original
  setup requires an explicit recapture; this is not continuous re-detection.
- The snapshot includes the evaluated source stack. Additional modifiers added
  to the generated experimental object must be removed before saving or
  recapturing, to prevent unintended double application.
- Captures above 400,000 vertices are rejected. Lower adhesion levels first.
- Do not rename/delete the original object's Bead setup while relying on
  Recapture from Original. If the original disappears, Restore Original refuses
  to delete the experimental result.
- This mode preserves evaluated data layers during capture/save; complete UV,
  material, animation and arbitrary modifier-stack compatibility is not certified.
- In a multi-view-layer workflow, local hide/selection behavior needs additional
  testing. Standard operator Undo is enabled; save a file before experimentation.

## Integration and testing

`merge_recapture.py` owns the operators, private per-result node groups and UI.
`merge_adhesion._build_adhesion(..., recaptured=True)` bypasses only the repeated
cut stage; ordinary callers retain the default path. Private groups expose
only the currently supported controls and freeze positions outside the mask.

Blender 5.1.2 background tests cover full add-on registration/re-registration,
capture on a realistic contact-geometry fixture, neutral equality, Pull edits
and exact return, normal transfer, Save New Base, .blend reload, refresh from
the original, original restore, and rejection of unsupported inputs. These do
not certify geometric validity of arbitrary captures or interactive UI drawing.

Reload the add-on after updating these Python files. Updating a development
folder does not change which folder the installed extension junction points to.
