import os
import subprocess
import sys


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENTRY = os.path.join(ROOT_DIR, "App.py")
VERSION_FILE = os.path.join(ROOT_DIR, "version")


def _read_app_version(default: str = "0.0.0") -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip() or default
    except OSError:
        return default


def _as_windows_file_version(version: str) -> str:
    import re
    parts = re.findall(r"\d+", version)
    normalized = [str(int(p)) for p in parts[:4]]
    while len(normalized) < 4:
        normalized.append("0")
    return ".".join(normalized)


def _cmd(output_name: str, console_mode: str):
    app_version = _read_app_version()
    file_version = _as_windows_file_version(app_version)
    company_name = "app"
    product_name = "app"
    file_description = "desktop app"
    onefile_tempdir = "{CACHE_DIR}/{COMPANY}/{PRODUCT}/{VERSION}"

    return [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--remove-output",
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        f"--company-name={company_name}",
        f"--product-name={product_name}",
        f"--file-description={file_description}",
        f"--file-version={file_version}",
        f"--product-version={app_version}",
        f"--onefile-tempdir-spec={onefile_tempdir}",
        f"--output-dir={os.path.join(ROOT_DIR, 'build')}",
        f"--output-filename={output_name}",
        f"--windows-console-mode={console_mode}",
        f"--windows-icon-from-ico={os.path.join(ROOT_DIR, 'ImageAssets', 'app_icon.ico')}",
        f"--include-data-dir={os.path.join(ROOT_DIR, 'ImageAssets', 'UI')}=ImageAssets/UI",
        f"--include-data-dir={os.path.join(ROOT_DIR, 'ImageAssets', 'AppUI')}=ImageAssets/AppUI",
        f"--include-data-files={os.path.join(ROOT_DIR, 'ImageAssets', 'app_icon.ico')}=ImageAssets/app_icon.ico",
        f"--include-data-files={VERSION_FILE}=version",
        f"--include-data-files={os.path.join(ROOT_DIR, 'source', 'utils', 'bridge', 'bridge.dll')}=move_assets/bridge.dll",
        f"--include-data-files={os.path.join(ROOT_DIR, 'source', 'utils', 'movement', 'model.npz')}=move_assets/model.npz",
        "--nofollow-import-to=source.utils.os_x11_backend",
        "--nofollow-import-to=source.utils.os_wayland_backend",
        "--nofollow-import-to=source.utils.wayland",
        "--nofollow-import-to=source.utils.wayland.*",
        ENTRY,
    ]


def build_one(output_name: str, console_mode: str):
    cmd = _cmd(output_name, console_mode)
    print("Building with:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT_DIR)


def main():
    # app.exe: windowed, app_debug.exe: console
    build_one("app", "disable")
    build_one("app_debug", "force")


if __name__ == "__main__":
    main()
