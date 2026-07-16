from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

class WindowError(Exception):
    pass


@dataclass(frozen=True)
class WindowInfo:
    title: str
    left: int
    top: int
    width: int
    height: int
    app_id: str = ""
    wm_class: str = ""
    backend: str = ""
    # Portal-stream frame rect; 0-size means frame == client.
    frame_left: int = 0
    frame_top: int = 0
    frame_width: int = 0
    frame_height: int = 0

    def frame_rect(self) -> tuple[int, int, int, int]:
        if self.frame_width > 0 and self.frame_height > 0:
            return (self.frame_left, self.frame_top, self.frame_width, self.frame_height)
        return (self.left, self.top, self.width, self.height)


@dataclass(frozen=True)
class Snapshot:
    backend: str
    active_title: str
    windows: list[WindowInfo]
    outputs: list[tuple[int, int, int, int]]
    pointer: Optional[tuple[int, int]] = None
    keyboard_layout: Optional[dict[str, Any]] = None


class WaylandBackendError(RuntimeError):
    pass


def which(name: str) -> bool:
    return shutil.which(name) is not None


def first_executable(names: Iterable[str]) -> Optional[str]:
    for name in names:
        if which(name):
            return name
    return None


def run(cmd: list[str], *, timeout: float = 2.0, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise WaylandBackendError(f"Missing required executable: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise WaylandBackendError(f"Command timed out: {' '.join(cmd)}") from exc

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit status {proc.returncode}").strip()
        raise WaylandBackendError(f"Command failed: {' '.join(cmd)}\n{detail}")
    return proc.stdout


def run_json(cmd: list[str], *, timeout: float = 2.0) -> Any:
    out = run(cmd, timeout=timeout)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise WaylandBackendError(f"Command did not return JSON: {' '.join(cmd)}") from exc


def rect_from_dict(rect: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(rect.get("x", 0)),
        int(rect.get("y", 0)),
        int(rect.get("width", 0)),
        int(rect.get("height", 0)),
    )


def matches_name(candidate: str, wanted: str) -> bool:
    candidate_l = (candidate or "").lower()
    wanted_l = (wanted or "").lower()
    return bool(candidate_l and wanted_l) and (candidate_l == wanted_l or wanted_l in candidate_l)


class BaseCompositorBackend:
    name = "base"
    cache_ttl = float(os.environ.get("LINUX_BACKEND_WAYLAND_CACHE_TTL", "0.05"))
    preferred_pipewire_source: Optional[str] = None

    def __init__(self):
        self._cached_snapshot: Optional[Snapshot] = None
        self._cached_at = 0.0
        self._revision = 0

    @classmethod
    def available(cls) -> bool:
        return False

    def _raw_snapshot(self) -> Snapshot:
        raise NotImplementedError

    def _bump_revision(self) -> None:
        self._revision += 1

    def _invalidate_cache(self) -> None:
        self._cached_snapshot = None
        self._cached_at = 0.0
        self._bump_revision()

    def window_state_revision(self) -> int:
        return self._revision

    def snapshot(self) -> Snapshot:
        now = time.monotonic()
        cached = self._cached_snapshot
        if cached is not None and now - self._cached_at <= self.cache_ttl:
            return cached
        snap = self._raw_snapshot()
        if cached is not None and snap != cached:
            self._bump_revision()
        self._cached_snapshot = snap
        self._cached_at = now
        return snap

    def active_title(self) -> str:
        return self.snapshot().active_title

    def windows(self) -> list[WindowInfo]:
        return self.snapshot().windows

    def outputs(self) -> list[tuple[int, int, int, int]]:
        return self.snapshot().outputs

    def pointer_position(self) -> tuple[int, int]:
        raise WaylandBackendError("Wayland pointer position is handled by the shared Linux input backend")

    def keyboard_layout_state(self) -> Optional[dict[str, Any]]:
        return self.snapshot().keyboard_layout

    def find_window(self, name: str) -> Optional[WindowInfo]:
        windows = self.windows()
        for win in windows:
            if win.title == name:
                return win
        for win in windows:
            if (
                matches_name(win.title, name)
                or matches_name(win.app_id, name)
                or matches_name(win.wm_class, name)
            ):
                return win
        return None