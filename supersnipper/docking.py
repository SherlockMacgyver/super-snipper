"""Pure geometry for docking, sliding and magnetic snapping of the capture panel."""

from __future__ import annotations

from typing import Optional, Tuple


from . import config, winapi


def panel_dock_geometry(
    edge: str,
    panel_width: int,
    panel_height: int,
    screen: Optional[Tuple[int, int, int, int]] = None,
    edge_inset: int = config.PANEL_DOCK_MARGIN,
) -> str:
    # `screen` overrides the physical virtual_screen() with an explicit bounds
    # tuple (vx, vy, w, h). The Qt HUD passes its logical availableGeometry so
    # placement math matches Qt's DPI-scaled coordinate space; capture-engine
    # callers keep the physical default.
    vx, vy, screen_width, screen_height = screen if screen is not None else winapi.virtual_screen()
    panel_width = max(1, int(panel_width))
    panel_height = max(1, int(panel_height))
    edge_inset = int(edge_inset)
    left = vx + edge_inset
    top = vy + edge_inset
    center_x = vx + max(edge_inset, (screen_width - panel_width) // 2)
    center_y = vy + max(edge_inset, (screen_height - panel_height) // 2)
    right = vx + max(edge_inset, screen_width - panel_width - edge_inset)
    bottom = vy + max(edge_inset, screen_height - panel_height - edge_inset)

    positions = {
        "top": (center_x, top),
        "right": (right, center_y),
        "bottom": (center_x, bottom),
        "left": (left, center_y),
    }
    try:
        x, y = positions[edge]
    except KeyError:
        raise ValueError(f"Unknown dock edge: {edge}") from None
    return f"+{int(x)}+{int(y)}"


def magnetic_panel_position(
    x: int,
    y: int,
    panel_width: int,
    panel_height: int,
    screen: Optional[Tuple[int, int, int, int]] = None,
    edge_inset: int = config.PANEL_DOCK_MARGIN,
) -> Tuple[int, int, Optional[str]]:
    vx, vy, screen_width, screen_height = screen if screen is not None else winapi.virtual_screen()
    panel_width = max(1, int(panel_width))
    panel_height = max(1, int(panel_height))
    edge_inset = int(edge_inset)
    min_x = vx + edge_inset
    min_y = vy + edge_inset
    max_x = vx + max(edge_inset, screen_width - panel_width - edge_inset)
    max_y = vy + max(edge_inset, screen_height - panel_height - edge_inset)
    clamped_x = min(max(int(x), min_x), max_x)
    clamped_y = min(max(int(y), min_y), max_y)

    distances = {
        "left": abs(clamped_x - min_x),
        "right": abs(clamped_x - max_x),
        "top": abs(clamped_y - min_y),
        "bottom": abs(clamped_y - max_y),
    }
    edge, distance = min(distances.items(), key=lambda item: item[1])
    if distance > config.PANEL_SNAP_THRESHOLD:
        return clamped_x, clamped_y, None
    if edge == "left":
        return min_x, clamped_y, edge
    if edge == "right":
        return max_x, clamped_y, edge
    if edge == "top":
        return clamped_x, min_y, edge
    return clamped_x, max_y, edge


def slide_panel_on_edge(
    edge: str,
    x: int,
    y: int,
    panel_width: int,
    panel_height: int,
    screen: Optional[Tuple[int, int, int, int]] = None,
    edge_inset: int = config.PANEL_DOCK_MARGIN,
) -> Tuple[int, int]:
    vx, vy, screen_width, screen_height = screen if screen is not None else winapi.virtual_screen()
    panel_width = max(1, int(panel_width))
    panel_height = max(1, int(panel_height))
    edge_inset = int(edge_inset)
    min_x = vx + edge_inset
    min_y = vy + edge_inset
    max_x = vx + max(edge_inset, screen_width - panel_width - edge_inset)
    max_y = vy + max(edge_inset, screen_height - panel_height - edge_inset)
    if edge == "left":
        return min_x, min(max(int(y), min_y), max_y)
    if edge == "right":
        return max_x, min(max(int(y), min_y), max_y)
    if edge == "top":
        return min(max(int(x), min_x), max_x), min_y
    if edge == "bottom":
        return min(max(int(x), min_x), max_x), max_y
    return min(max(int(x), min_x), max_x), min(max(int(y), min_y), max_y)


def hud_orientation_for_edge(edge: Optional[str]) -> str:
    """Vertical rail when docked to a side, horizontal bar otherwise."""
    return "vertical" if edge in ("left", "right") else "horizontal"


class HudDragController:
    """Pure drag / magnetic-snap math for the capture HUD, GUI-toolkit free.

    The HUD widget owns one of these and just forwards cursor positions; all
    of the snapping, sliding, and orientation decisions live here so they can
    be unit-tested without spinning up a real window.
    """

    def __init__(
        self,
        width: int,
        height: int,
        snapped_edge: Optional[str] = None,
        screen: Optional[Tuple[int, int, int, int]] = None,
        edge_inset: int = config.PANEL_DOCK_MARGIN,
    ) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.snapped_edge = snapped_edge
        self.screen = screen
        self.edge_inset = int(edge_inset)
        self._cursor_origin: Optional[Tuple[int, int]] = None
        self._window_origin: Optional[Tuple[int, int]] = None

    def resize(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))

    def set_screen(self, screen: Optional[Tuple[int, int, int, int]]) -> None:
        self.screen = screen

    def set_edge_inset(self, edge_inset: int) -> None:
        self.edge_inset = int(edge_inset)

    def begin(self, cursor_x: int, cursor_y: int, window_x: int, window_y: int) -> None:
        self._cursor_origin = (int(cursor_x), int(cursor_y))
        self._window_origin = (int(window_x), int(window_y))

    def move_to(self, cursor_x: int, cursor_y: int) -> Tuple[int, int]:
        if self._cursor_origin is None or self._window_origin is None:
            return int(cursor_x), int(cursor_y)
        dx = int(cursor_x) - self._cursor_origin[0]
        dy = int(cursor_y) - self._cursor_origin[1]
        free_x = self._window_origin[0] + dx
        free_y = self._window_origin[1] + dy
        # Live magnetic: the window follows the cursor freely and only sticks to
        # an edge while it is within the snap threshold of one. This lets the
        # user pull the HUD off an edge instead of being locked sliding along it.
        x, y, edge = magnetic_panel_position(
            free_x,
            free_y,
            self.width,
            self.height,
            screen=self.screen,
            edge_inset=self.edge_inset,
        )
        self.snapped_edge = edge
        return int(x), int(y)

    def release(self, window_x: int, window_y: int) -> Tuple[int, int, Optional[str], str]:
        x, y, edge = magnetic_panel_position(
            window_x,
            window_y,
            self.width,
            self.height,
            screen=self.screen,
            edge_inset=self.edge_inset,
        )
        self.snapped_edge = edge
        self._cursor_origin = None
        self._window_origin = None
        return int(x), int(y), edge, hud_orientation_for_edge(edge)
