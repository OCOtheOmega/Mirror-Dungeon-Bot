from __future__ import annotations

import json
import os
from typing import Any

from .gnome import BUS_NAME, INTERFACE_NAME, OBJECT_PATH, bridge_available, call_bridge, is_gnome_wayland
from .base import BaseCompositorBackend, Snapshot, WaylandBackendError, WindowInfo, rect_from_dict


class MutterBackend(BaseCompositorBackend):
    """GNOME/Mutter backend; requires the bundled GNOME Shell extension's session-bus Snapshot()."""

    name = "mutter"
    bus_name = BUS_NAME
    object_path = OBJECT_PATH
    interface_name = INTERFACE_NAME

    def __init__(self):
        super().__init__()
        self._extension_revision: int | None = None

    @classmethod
    def available(cls) -> bool:
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
        if "gnome" not in desktop and "mutter" not in desktop:
            return False
        # still True on GNOME Wayland so runtime errors point to extension setup
        return bridge_available(timeout=0.5) or is_gnome_wayland()

    def _call_json(self, method: str) -> dict[str, Any]:
        try:
            return json.loads(call_bridge(method, timeout=2.0))
        except WaylandBackendError:
            raise
        except Exception as exc:
            raise WaylandBackendError(
                "Mutter backend requires the org.cgrinder.Mutter GNOME Shell extension. "
                "Install and enable the bundled GNOME extension."
            ) from exc

    def _raw_snapshot(self) -> Snapshot:
        data = self._call_json("Snapshot")
        try:
            revision = int(data.get("revision", 0))
            if self._extension_revision is None:
                self._extension_revision = revision
            elif revision != self._extension_revision:
                self._extension_revision = revision
                self._bump_revision()
        except Exception:
            pass

        windows: list[WindowInfo] = []
        for item in data.get("windows") or []:
            windows.append(WindowInfo(
                title=str(item.get("title") or ""),
                left=int(item.get("left", 0)),
                top=int(item.get("top", 0)),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
                app_id=str(item.get("app_id") or ""),
                wm_class=str(item.get("wm_class") or ""),
                backend=self.name,
                frame_left=int(item.get("frame_left", 0)),
                frame_top=int(item.get("frame_top", 0)),
                frame_width=int(item.get("frame_width", 0)),
                frame_height=int(item.get("frame_height", 0)),
            ))

        outputs: list[tuple[int, int, int, int]] = []
        for item in data.get("outputs") or []:
            x, y, w, h = rect_from_dict(item)
            if w > 0 and h > 0:
                outputs.append((x, y, w, h))
        if not outputs:
            raise WaylandBackendError("Mutter extension returned no monitor geometry")

        return Snapshot(
            backend=self.name,
            active_title=str(data.get("activeTitle") or ""),
            windows=windows,
            outputs=outputs,
            pointer=None,
            keyboard_layout=data.get("keyboardLayout"),
        )