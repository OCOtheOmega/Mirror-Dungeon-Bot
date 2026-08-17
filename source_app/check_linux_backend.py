from __future__ import annotations

import os
import platform

from PySide6.QtWidgets import QMessageBox


def check_linux(app_parent=None) -> bool:
    if platform.system() != "Linux" or not _is_gnome_wayland():
        return True

    from source.utils.wayland import gnome

    if gnome.bridge_available() and gnome.bridge_current():
        return True

    msg = QMessageBox(app_parent)
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle("GNOME Wayland Setup")
    msg.setText("GNOME Wayland requires the current ChargeGrinder GNOME Shell extension.")
    msg.setInformativeText("Install or update it for this user?")
    msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    if msg.exec() != QMessageBox.StandardButton.Yes:
        return False

    try:
        loaded = gnome.install_and_enable()
    except Exception as exc:
        QMessageBox.critical(app_parent, "GNOME Extension Setup Failed", str(exc))
        return False

    if loaded:
        return True

    QMessageBox.warning(
        app_parent,
        "GNOME Extension Installed",
        "The extension was installed or updated, but GNOME Shell has not loaded it yet.\n"
        "Log out and back in to enable it, then relaunch ChargeGrinder.",
    )
    return False


def _is_gnome_wayland() -> bool:
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    return session == "wayland" and ("gnome" in desktop or "mutter" in desktop)
