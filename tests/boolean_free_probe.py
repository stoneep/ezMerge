"""Diagnostic only: bypass the pre-deformation cut in a private node group.

No new user-facing mode/default is introduced until the result is evaluated.
"""


def bypass_adhesion_boolean(mod):
    group = mod.node_group
    cuts = [n for n in group.nodes if n.bl_idname == 'GeometryNodeMeshBoolean' and n.operation == 'DIFFERENCE']
    if len(cuts) != 1 or group.users != 1:
        raise ValueError('Probe requires a private adhesion group with one Difference node.')
    cut = cuts[0]
    source = cut.inputs[0].links[0].from_socket
    drops = [link.to_node for link in cut.outputs['Mesh'].links
             if link.to_node.bl_idname == 'GeometryNodeDeleteGeometry']
    if len(drops) != 1:
        raise ValueError('Cannot identify the cut-face removal stage.')
    output = drops[0].outputs['Geometry']
    consumers = [link.to_socket for link in output.links]
    if not consumers:
        raise ValueError('No downstream adhesion consumer found.')
    for destination in consumers:
        group.links.new(source, destination)
    # Remove the unused cut/removal nodes so the probe contains no Boolean
    # operation at all, rather than relying only on evaluation laziness.
    group.nodes.remove(drops[0])
    group.nodes.remove(cut)
    group.update_tag()
    return group
