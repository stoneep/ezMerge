"""Diagnostic Smooth profile with geometric endpoint tangent conditions.

Keep the existing normal pull and controls. Change only the radial falloff:
1 - sin(pi*u/2) has a nonzero contact slope and zero outer slope. Combined
with the existing quadratic leading height, this gives a horizontal contact
tangent and an unchanged vertical cylinder tangent at the shoulder.
No production defaults/node groups are mutated.
"""
import inspect
import math


def build_tangent_profile(builder, group):
    original = builder._falloff_weight
    m, switch = builder._math, builder._switch
    def weight(nodes, links, gin, distance, influence):
        existing = original(nodes, links, gin, distance, influence)
        u = m(nodes, links, 'DIVIDE', distance, m(nodes, links, 'MAXIMUM', influence, 1e-6), clamp=True)
        radial = m(nodes, links, 'SUBTRACT', 1.0,
                   m(nodes, links, 'SINE', m(nodes, links, 'MULTIPLY', u, math.pi/2)))
        selected = m(nodes, links, 'MULTIPLY',
                     m(nodes, links, 'COMPARE', gin.outputs[builder.FALLOFF], float(builder.SMOOTH), 0.1),
                     m(nodes, links, 'SUBTRACT', 1.0, gin.outputs[builder.BEAD_CUSTOM_CURVE]))
        return switch(nodes, links, 'FLOAT', selected, existing, radial)
    namespace = dict(vars(builder), _falloff_weight=weight)
    exec(compile(inspect.getsource(builder._build_adhesion), '<diagnostic tangent profile>', 'exec'), namespace)
    namespace['_build_adhesion'](group)
