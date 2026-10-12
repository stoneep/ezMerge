"""Read reference node assets without registering/executing the addon."""
import bpy
from pathlib import Path
import sys

path = Path(sys.argv[sys.argv.index('--asset')+1]).resolve()
assert path.is_file() and path.suffix.lower()=='.blend'
with bpy.data.libraries.load(str(path),link=False) as (source,target):
    names = [name for name in source.node_groups if 'normal' in name.lower()]
    print('REFERENCE normal-related groups',names,flush=True)
    target.node_groups = names
for group in bpy.data.node_groups:
    relevant = [node for node in group.nodes
                if any(word in node.bl_idname.lower() for word in ('normal','sample','capture','store','blur'))]
    if 'normal' in group.name.lower():
        relevant = list(group.nodes)
    if not relevant:
        continue
    print('REFERENCE GROUP',group.name,flush=True)
    for node in relevant:
        print('NODE',node.name,node.bl_idname,
              {key:getattr(node,key) for key in ('domain','mode','data_type','operation','blend_type','clamp_factor') if hasattr(node,key)},flush=True)
    selected = {node.name for node in relevant}
    for link in group.links:
        if link.from_node.name in selected or link.to_node.name in selected:
            print('LINK',link.from_node.name,link.from_socket.name,'->',
                  link.to_node.name,link.to_socket.name,flush=True)
print('REFERENCE INSPECTION COMPLETE',flush=True)
