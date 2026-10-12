"""Keyboard shortcut of the add-on: E switches which handles the selected EZ shape shows."""

import bpy

_items = []


def register():
    keyconfigs = bpy.context.window_manager.keyconfigs
    if keyconfigs is None or keyconfigs.addon is None:  # background mode: no keymaps
        return
    keymap = keyconfigs.addon.keymaps.new(name='Object Mode', space_type='EMPTY')
    # The operator only applies to an EZ shape: anywhere else the key goes on to Blender.
    _items.append((keymap, keymap.keymap_items.new("ezhs.handle_mode", 'E', 'PRESS')))


def unregister():
    for keymap, item in _items:
        keymap.keymap_items.remove(item)
    _items.clear()
