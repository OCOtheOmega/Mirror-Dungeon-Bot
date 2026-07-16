from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .base import BaseCompositorBackend, Snapshot, WaylandBackendError, WindowInfo, first_executable, rect_from_dict, run


class KWinBackend(BaseCompositorBackend):
    """KWin backend: one persistent KWin script pushes snapshots over a private D-Bus callback."""

    name = "kwin"

    def __init__(self):
        super().__init__()
        self._service_name = f"org.cgrinder.KWinBackend_{os.getpid()}_{int(time.time() * 1000)}"
        safe_tail = re.sub(r"[^A-Za-z0-9_]", "_", self._service_name.split(".")[-1])
        self._object_path = f"/org/cgrinder/{safe_tail}"
        self._interface_name = "org.cgrinder.KWinBackend"
        self._plugin_name = f"cgrinder_kwin_backend_{os.getpid()}"
        self._script_path: Optional[str] = None
        self._script_id: Optional[int] = None
        self._receiver: Optional[KWinBackend._DbusReceiverHandle] = None
        self._script_lock = threading.Lock()
        self._latest_payload: Optional[dict[str, Any]] = None
        self._latest_event = threading.Event()
        self._closed = False

    @staticmethod
    def _qdbus() -> Optional[str]:
        return first_executable(("qdbus6", "qdbus-qt6", "qdbus"))

    @classmethod
    def available(cls) -> bool:
        qdbus = cls._qdbus()
        if not qdbus:
            return False

        session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
        looks_like_kwin = (
            "kde" in desktop
            or "plasma" in desktop
            or bool(os.environ.get("KDE_FULL_SESSION"))
            or bool(os.environ.get("KWIN_RUNNING"))
        )
        if session != "wayland" or not looks_like_kwin:
            return False

        try:
            run([qdbus, "org.kde.KWin", "/Scripting"], timeout=1.0, check=True)
            return True
        except Exception:
            return False

    def _load_script(self, script_path: str, plugin_name: str) -> int:
        qdbus = self._qdbus()
        if not qdbus:
            raise WaylandBackendError("Missing qdbus executable. On Fedora KDE, install qt6-qttools.")

        # remove a stale copy from a previous crashed process
        try:
            run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", plugin_name], timeout=1.0, check=False)
        except Exception:
            pass

        attempts = [
            [qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", script_path, plugin_name],
            [qdbus, "org.kde.KWin", "/Scripting", "loadScript", script_path, plugin_name],
            [qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", script_path],
            [qdbus, "org.kde.KWin", "/Scripting", "loadScript", script_path],
        ]
        errors: list[str] = []
        for cmd in attempts:
            try:
                out = run(cmd, timeout=2.0)
                numbers = re.findall(r"\d+", out)
                if numbers:
                    return int(numbers[-1])
                errors.append(f"{' '.join(cmd)} -> no numeric script id in {out!r}")
            except Exception as exc:
                errors.append(str(exc))
        raise WaylandBackendError("Could not load KWin backend script:\n" + "\n".join(errors[-3:]))

    def _call_script_method(self, script_id: int, method: str, *, timeout: float = 2.0, check: bool = True) -> str:
        qdbus = self._qdbus()
        if not qdbus:
            raise WaylandBackendError("Missing qdbus executable")
        object_path = f"/Scripting/Script{script_id}"
        attempts = [
            [qdbus, "org.kde.KWin", object_path, f"org.kde.kwin.Script.{method}"],
            [qdbus, "org.kde.KWin", object_path, method],
        ]
        last_error: Optional[Exception] = None
        for cmd in attempts:
            try:
                return run(cmd, timeout=timeout, check=check)
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return ""

    class _DbusReceiverHandle:
        def __init__(self, loop: Any, thread: threading.Thread, bus_name: Any, receiver_obj: Any):
            self.loop = loop
            self.thread = thread
            self.bus_name = bus_name
            self.receiver_obj = receiver_obj

        def close(self) -> None:
            try:
                self.loop.quit()
            except Exception:
                pass
            try:
                self.thread.join(timeout=0.5)
            except Exception:
                pass
            self.receiver_obj = None
            self.bus_name = None

    def _start_callback_service(self) -> "KWinBackend._DbusReceiverHandle":
        try:
            import dbus
            import dbus.service
            import dbus.mainloop.glib
            from gi.repository import GLib
        except Exception as exc:
            raise WaylandBackendError(
                "KWin backend requires dbus-python and PyGObject. "
                "On Fedora, install: sudo dnf install python3-dbus python3-gobject"
            ) from exc

        owner = self
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        bus_name = dbus.service.BusName(self._service_name, bus, do_not_queue=True)

        class Receiver(dbus.service.Object):
            @dbus.service.method(owner._interface_name, in_signature="s", out_signature="")
            def Result(self, payload: str) -> None:
                try:
                    parsed = json.loads(str(payload))
                    if isinstance(parsed, dict):
                        owner._latest_payload = parsed
                        owner._latest_event.set()
                        owner._invalidate_cache()
                except Exception:
                    pass

        receiver_obj = Receiver(bus, self._object_path)
        loop = GLib.MainLoop()
        thread = threading.Thread(target=loop.run, name="kwin-dbus-callback", daemon=True)
        thread.start()
        return KWinBackend._DbusReceiverHandle(loop, thread, bus_name, receiver_obj)

    def _script_source(self) -> str:
        return f'''
(function() {{
    var SERVICE = {json.dumps(self._service_name)};
    var PATH = {json.dumps(self._object_path)};
    var IFACE = {json.dumps(self._interface_name)};

    function send(payload) {{
        try {{
            callDBus(SERVICE, PATH, IFACE, "Result", JSON.stringify(payload));
        }} catch (e) {{
            print("cgrinder KWin callback failed: " + String(e));
        }}
    }}

    function value(obj, names, fallback) {{
        for (var i = 0; i < names.length; i++) {{
            try {{
                var v = obj[names[i]];
                if (v !== undefined && v !== null) return v;
            }} catch (e) {{}}
        }}
        return fallback;
    }}

    function boolProp(obj, name) {{
        try {{ return !!obj[name]; }} catch (e) {{ return false; }}
    }}

    function num(v) {{
        var n = Number(v);
        if (!isFinite(n)) return 0;
        return Math.round(n);
    }}

    function rectObj(g, w) {{
        if (g) return {{x: num(g.x), y: num(g.y), width: num(g.width), height: num(g.height)}};
        if (w) return {{x: num(w.x), y: num(w.y), width: num(w.width), height: num(w.height)}};
        return {{x: 0, y: 0, width: 0, height: 0}};
    }}

    function titleOf(w) {{
        if (!w) return "";
        return String(value(w, ["caption", "captionNormal", "resourceName", "resourceClass", "desktopFileName"], ""));
    }}

    function shouldIncludeWindow(w) {{
        if (!w) return false;
        if (boolProp(w, "deleted")) return false;
        if (boolProp(w, "desktopWindow") || boolProp(w, "dock") || boolProp(w, "splash") || boolProp(w, "notification")) return false;
        if (boolProp(w, "internal") || boolProp(w, "outline") || boolProp(w, "inputMethod")) return false;
        var g = rectObj(value(w, ["clientGeometry", "geometry", "frameGeometry", "bufferGeometry"], null), w);
        return g.width > 0 && g.height > 0;
    }}

    function windowObj(w) {{
        var client = rectObj(value(w, ["clientGeometry", "geometry", "frameGeometry", "bufferGeometry"], null), w);
        var frame = rectObj(value(w, ["frameGeometry", "geometry", "clientGeometry", "bufferGeometry"], null), w);
        return {{
            title: titleOf(w),
            left: client.x,
            top: client.y,
            width: client.width,
            height: client.height,
            frame_left: frame.x,
            frame_top: frame.y,
            frame_width: frame.width,
            frame_height: frame.height,
            app_id: String(value(w, ["desktopFileName", "resourceName"], "")),
            wm_class: String(value(w, ["resourceClass", "resourceName"], "")),
            active: boolProp(w, "active")
        }};
    }}

    function snapshot() {{
        try {{
            var rawList = [];
            try {{ if (workspace.stackingOrder) rawList = workspace.stackingOrder; }} catch (e) {{}}
            try {{ if ((!rawList || rawList.length === 0) && typeof workspace.windowList === "function") rawList = workspace.windowList(); }} catch (e) {{}}

            var wins = [];
            for (var i = 0; i < rawList.length; i++) {{
                var w = rawList[i];
                if (!shouldIncludeWindow(w)) continue;
                wins.push(windowObj(w));
            }}

            var active = null;
            try {{ active = workspace.activeWindow; }} catch (e) {{}}
            try {{ if (!active) active = workspace.activeClient; }} catch (e) {{}}

            var outputs = [];
            try {{
                var screens = workspace.screens || [];
                for (var s = 0; s < screens.length; s++) {{
                    var sg = rectObj(screens[s].geometry, null);
                    if (sg.width > 0 && sg.height > 0) outputs.push(sg);
                }}
            }} catch (e) {{}}
            if (outputs.length === 0) {{
                var vg = rectObj(value(workspace, ["virtualScreenGeometry"], null), null);
                if (vg.width > 0 && vg.height > 0) outputs.push(vg);
            }}

            send({{
                backend: "kwin",
                activeTitle: titleOf(active),
                windows: wins,
                outputs: outputs,
                rawWindowCount: rawList.length,
                transport: "persistent-kwin-script"
            }});
        }} catch (err) {{
            send({{backend: "kwin", error: String(err), stack: String(err && err.stack ? err.stack : ""), transport: "persistent-kwin-script"}});
        }}
    }}

    // QTimer may be absent in some KWin builds; fall back to immediate send.
    var debounce = null;
    try {{
        debounce = new QTimer();
        debounce.singleShot = true;
        debounce.interval = 50;
        debounce.timeout.connect(function() {{ snapshot(); }});
    }} catch (e) {{
        debounce = null;
    }}

    function scheduleSnapshot() {{
        if (debounce) {{ debounce.start(); return; }}
        snapshot();
    }}

    function connectSignal(obj, name) {{
        try {{
            if (obj && obj[name] && obj[name].connect) obj[name].connect(scheduleSnapshot);
        }} catch (e) {{}}
    }}

    function watchWindow(w) {{
        if (!w) return;
        try {{
            if (w.__cgrinderWatched) return;
            w.__cgrinderWatched = true;
        }} catch (e) {{}}
        connectSignal(w, "captionChanged");
        connectSignal(w, "frameGeometryChanged");
        connectSignal(w, "clientGeometryChanged");
        connectSignal(w, "geometryChanged");
    }}

    function onWindowAdded(w) {{
        watchWindow(w);
        scheduleSnapshot();
    }}

    try {{ if (workspace.windowAdded && workspace.windowAdded.connect) workspace.windowAdded.connect(onWindowAdded); }} catch (e) {{}}
    try {{ if (workspace.clientAdded && workspace.clientAdded.connect) workspace.clientAdded.connect(onWindowAdded); }} catch (e) {{}}
    connectSignal(workspace, "windowRemoved");
    connectSignal(workspace, "clientRemoved");
    connectSignal(workspace, "windowActivated");
    connectSignal(workspace, "clientActivated");
    connectSignal(workspace, "currentDesktopChanged");
    connectSignal(workspace, "currentActivityChanged");
    connectSignal(workspace, "screensChanged");

    try {{
        var list = [];
        try {{ if (workspace.stackingOrder) list = workspace.stackingOrder; }} catch (e) {{}}
        try {{ if ((!list || list.length === 0) && typeof workspace.windowList === "function") list = workspace.windowList(); }} catch (e) {{}}
        for (var i = 0; i < list.length; i++) {{
            watchWindow(list[i]);
        }}
    }} catch (e) {{}}

    snapshot();
}})();
'''

    def _ensure_script_loaded(self) -> None:
        if self._script_id is not None and self._receiver is not None:
            return
        with self._script_lock:
            if self._script_id is not None and self._receiver is not None:
                return

            self._receiver = self._start_callback_service()
            time.sleep(0.05)

            fd, path = tempfile.mkstemp(prefix="cgrinder-kwin-persistent-", suffix=".js")
            os.close(fd)
            self._script_path = path
            Path(path).write_text(self._script_source(), encoding="utf-8")
            self._script_id = self._load_script(path, self._plugin_name)
            self._call_script_method(self._script_id, "run", timeout=2.0)

            if not self._latest_event.wait(timeout=4.0):
                raise WaylandBackendError(
                    "KWin script loaded, but no initial snapshot was received. "
                    "Check: journalctl --user -u plasma-kwin_wayland.service -f"
                )

    def close(self) -> None:
        self._closed = True
        script_id = self._script_id
        if script_id is not None:
            try:
                self._call_script_method(script_id, "stop", timeout=1.0, check=False)
            except Exception:
                pass
        if self._receiver is not None:
            self._receiver.close()
            self._receiver = None
        if self._script_path:
            try:
                os.unlink(self._script_path)
            except OSError:
                pass
            self._script_path = None
        self._script_id = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _payload_to_snapshot(self, data: dict[str, Any]) -> Snapshot:
        if data.get("error"):
            raise WaylandBackendError(f"KWin script error: {data.get('error')}\n{data.get('stack') or ''}")

        windows: list[WindowInfo] = []
        for item in data.get("windows") or []:
            try:
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
            except Exception:
                continue

        outputs: list[tuple[int, int, int, int]] = []
        for item in data.get("outputs") or []:
            x, y, w, h = rect_from_dict(item)
            if w > 0 and h > 0:
                outputs.append((x, y, w, h))
        if not outputs:
            raise WaylandBackendError("KWin script returned no screen geometry")

        return Snapshot(
            backend=self.name,
            active_title=str(data.get("activeTitle") or ""),
            windows=windows,
            outputs=outputs,
            pointer=None,
            keyboard_layout=None,
        )

    def _raw_snapshot(self) -> Snapshot:
        self._ensure_script_loaded()
        data = self._latest_payload
        if not isinstance(data, dict):
            raise WaylandBackendError("No KWin snapshot available")
        return self._payload_to_snapshot(data)