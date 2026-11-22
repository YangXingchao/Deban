# Finder window with the app on the left and Applications on the right.
import os.path
app = defines.get("app", "dist/Deban.app")            # noqa: F821 (injected by dmgbuild)
format = "UDZO"
size = None
files = [app]
symlinks = {"Applications": "/Applications"}
badge_icon = None
icon_locations = {os.path.basename(app): (180, 190), "Applications": (480, 190)}
background = defines.get("bg", "native/mac/dmg-background.png")   # noqa: F821
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
sidebar_width = 0
window_rect = ((200, 160), (660, 400))
default_view = "icon-view"
show_icon_preview = False
arrange_by = None
grid_offset = (0, 0)
grid_spacing = 100
scroll_position = (0, 0)
label_pos = "bottom"
text_size = 13
icon_size = 128
