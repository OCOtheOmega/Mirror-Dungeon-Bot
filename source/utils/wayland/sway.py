from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .base import BaseCompositorBackend, Snapshot, WaylandBackendError, WindowInfo, rect_from_dict


class SwayBackend(BaseCompositorBackend):
    """Sway backend using the i3/sway Unix IPC protocol directly."""

    name = "sway"

    # i3-compatible messages.
    _GET_OUTPUTS = 3
    _GET_TREE = 4
    # sway extension messages.
    _GET_INPUTS = 100
    _SUBSCRIBE = 2
    _EVENT_MASK = 0x80000000

    def __init__(self):
        super().__init__()
        self._event_thread_started = False
        self._event_thread_lock = threading.Lock()

    @classmethod
    def _socket_path(cls) -> Optional[Path]:
        path = os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK")
        return Path(path) if path else None

    @classmethod
    def available(cls) -> bool:
        sock = cls._socket_path()
        return bool(sock and sock.exists())

    def _connect(self, *, timeout: float = 1.0) -> socket.socket:
        path = self._socket_path()
        if not path:
            raise WaylandBackendError("SWAYSOCK/I3SOCK is not set")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except OSError as exc:
            sock.close()
            raise WaylandBackendError(f"Could not connect to Sway IPC socket {path}: {exc}") from exc
        return sock

    @staticmethod
    def _recv_exact(sock: socket.socket, nbytes: int) -> bytes:
        chunks: list[bytes] = []
        remaining = nbytes
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise WaylandBackendError("Sway IPC connection closed unexpectedly")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send(self, msg_type: int, payload: str | bytes = b"", *, timeout: float = 1.0) -> tuple[int, bytes]:
        payload_b = payload.encode("utf-8") if isinstance(payload, str) else payload
        header = struct.pack("<6sII", b"i3-ipc", len(payload_b), msg_type)
        with self._connect(timeout=timeout) as sock:
            sock.sendall(header + payload_b)
            raw_header = self._recv_exact(sock, 14)
            magic, length, reply_type = struct.unpack("<6sII", raw_header)
            if magic != b"i3-ipc":
                raise WaylandBackendError("Invalid Sway IPC reply magic")
            reply = self._recv_exact(sock, length)
            return reply_type, reply

    def _send_json(self, msg_type: int, payload: str | bytes = b"") -> Any:
        _, raw = self._send(msg_type, payload)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WaylandBackendError(f"Sway IPC returned non-JSON for type {msg_type}: {raw[:200]!r}") from exc

    def _invalidate_cache(self) -> None:
        super()._invalidate_cache()

    def _start_event_listener_once(self) -> None:
        with self._event_thread_lock:
            if self._event_thread_started:
                return
            self._event_thread_started = True

        def run_listener() -> None:
            while True:
                try:
                    with self._connect(timeout=1.0) as sock:
                        payload = json.dumps(["window", "output", "input", "workspace"])
                        sock.sendall(struct.pack("<6sII", b"i3-ipc", len(payload), self._SUBSCRIBE) + payload.encode("utf-8"))
                        # Consume subscribe reply.
                        raw_header = self._recv_exact(sock, 14)
                        magic, length, _reply_type = struct.unpack("<6sII", raw_header)
                        if magic == b"i3-ipc":
                            self._recv_exact(sock, length)
                            sock.settimeout(None)
                            self._invalidate_cache()
                            while True:
                                raw_header = self._recv_exact(sock, 14)
                                magic, length, reply_type = struct.unpack("<6sII", raw_header)
                                if magic != b"i3-ipc":
                                    break
                                self._recv_exact(sock, length)
                                if reply_type & self._EVENT_MASK:
                                    self._invalidate_cache()
                except Exception:
                    pass
                time.sleep(1.0)

        threading.Thread(target=run_listener, name="sway-ipc-events", daemon=True).start()

    def _tree(self) -> dict[str, Any]:
        return self._send_json(self._GET_TREE)

    def _iter_nodes(self, node: dict[str, Any]) -> Iterable[dict[str, Any]]:
        yield node
        for key in ("nodes", "floating_nodes"):
            for child in node.get(key) or []:
                yield from self._iter_nodes(child)

    def _active_title_and_windows(self) -> tuple[str, list[WindowInfo]]:
        active_title = ""
        result: list[WindowInfo] = []
        for node in self._iter_nodes(self._tree()):
            if node.get("focused"):
                active_title = str(node.get("name") or "")
            if node.get("type") not in {"con", "floating_con"}:
                continue
            name = str(node.get("name") or "")
            rect = node.get("rect") or {}
            frame_left, frame_top, frame_width, frame_height = rect_from_dict(rect)
            if not name or frame_width <= 0 or frame_height <= 0:
                continue
            # rect includes borders/titlebar; window_rect is the content area relative to it.
            wx, wy, ww, wh = rect_from_dict(node.get("window_rect") or {})
            if ww > 0 and wh > 0:
                left, top, width, height = frame_left + wx, frame_top + wy, ww, wh
            else:
                left, top, width, height = frame_left, frame_top, frame_width, frame_height
            props = node.get("window_properties") or {}
            result.append(WindowInfo(
                title=name,
                left=left,
                top=top,
                width=width,
                height=height,
                app_id=str(node.get("app_id") or ""),
                wm_class=str(props.get("class") or props.get("instance") or ""),
                backend=self.name,
                frame_left=frame_left,
                frame_top=frame_top,
                frame_width=frame_width,
                frame_height=frame_height,
            ))
        return active_title, result

    def _outputs(self) -> list[tuple[int, int, int, int]]:
        data = self._send_json(self._GET_OUTPUTS)
        result: list[tuple[int, int, int, int]] = []
        for out in data or []:
            if not out.get("active", False):
                continue
            rect = out.get("rect") or {}
            x, y, w, h = rect_from_dict(rect)
            if w > 0 and h > 0:
                result.append((x, y, w, h))
        if not result:
            raise WaylandBackendError("Sway returned no active outputs")
        return result

    def _keyboard_layout(self):
        try:
            data = self._send_json(self._GET_INPUTS)
            keyboards = [item for item in (data or []) if item.get("type") == "keyboard"]
            active = keyboards[0] if keyboards else None
            if active:
                return {
                    "source": "sway-ipc get_inputs",
                    "xkb_layout_names": active.get("xkb_layout_names"),
                    "xkb_active_layout_index": active.get("xkb_active_layout_index"),
                    "identifier": active.get("identifier"),
                    "name": active.get("name"),
                }
        except Exception:
            pass
        return None

    def _raw_snapshot(self) -> Snapshot:
        self._start_event_listener_once()
        active_title, windows = self._active_title_and_windows()
        return Snapshot(
            backend=self.name,
            active_title=active_title,
            windows=windows,
            outputs=self._outputs(),
            pointer=None,
            keyboard_layout=self._keyboard_layout(),
        )