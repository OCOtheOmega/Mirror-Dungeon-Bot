from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from ..base import WaylandBackendError, run, which


EXTENSION_UUID = "cgrinder@local"
REQUIRED_BRIDGE_VERSION = 4
BUS_NAME = os.environ.get("LINUX_BACKEND_MUTTER_BUS", "org.cgrinder.Mutter")
OBJECT_PATH = os.environ.get("LINUX_BACKEND_MUTTER_PATH", "/org/cgrinder/Mutter")
INTERFACE_NAME = os.environ.get("LINUX_BACKEND_MUTTER_IFACE", "org.cgrinder.Mutter")
EXTENSION_SOURCE_DIR = Path(__file__).resolve().parent


def is_gnome_wayland() -> bool:
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    return session == "wayland" and ("gnome" in desktop or "mutter" in desktop)


_gio_bus: Optional[Any] = None


def _session_bus() -> Any:
    global _gio_bus
    if _gio_bus is None:
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except Exception as exc:
            raise WaylandBackendError(
                "GNOME bridge requires PyGObject/GIO. On Fedora: "
                "sudo dnf install python3-gobject"
            ) from exc
        _gio_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return _gio_bus


def call_bridge(method: str, timeout: float = 2.0) -> str:
    bus = _session_bus()
    from gi.repository import Gio, GLib

    out = bus.call_sync(
        BUS_NAME,
        OBJECT_PATH,
        INTERFACE_NAME,
        method,
        None,
        GLib.VariantType.new("(s)"),
        Gio.DBusCallFlags.NONE,
        int(timeout * 1000),
        None,
    )
    return str(out.unpack()[0])


def bridge_available(timeout: float = 0.5) -> bool:
    try:
        call_bridge("Ping", timeout=timeout)
        return True
    except Exception:
        return False


def bridge_version(timeout: float = 0.5) -> int:
    try:
        return int(json.loads(call_bridge("Snapshot", timeout=timeout)).get("bridgeVersion", 1))
    except Exception:
        return 0


def bridge_current(timeout: float = 0.5) -> bool:
    return bridge_version(timeout=timeout) >= REQUIRED_BRIDGE_VERSION


def extension_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local/share/gnome-shell/extensions" / EXTENSION_UUID


def install_extension(home: Path | None = None) -> Path:
    target = extension_dir(home)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("metadata.json", "extension.js"):
        src = EXTENSION_SOURCE_DIR / name
        if not src.exists():
            raise WaylandBackendError(f"Bundled GNOME extension file is missing: {src}")
        shutil.copy2(src, target / name)
    return target


def enable_extension() -> bool:
    if not which("gnome-extensions"):
        raise WaylandBackendError("Missing required executable: gnome-extensions")
    try:
        run(["gnome-extensions", "enable", EXTENSION_UUID], timeout=5.0)
        return True
    except WaylandBackendError as exc:
        detail = str(exc).lower()
        if (
            "does not exist" in detail
            or "not found" in detail
            or "not installed" in detail
            or "not currently installed" in detail
        ):
            return False
        raise


def install_and_enable() -> bool:
    install_extension()
    if not enable_extension():
        return False
    return bridge_current(timeout=2.0)