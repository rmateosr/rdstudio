# PyInstaller build spec for Reaction-Diffusion Studio.
#
# Run with:   pyinstaller --noconfirm --clean rdstudio.spec
# Output:     dist/Reaction-Diffusion Studio/  (folder bundle)
#             Launch by double-clicking the .exe inside that folder.
#
# Cross-platform: same spec works on Windows, macOS, and Linux. The icon
# extension chosen below depends on the host OS.

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# ttkbootstrap loads theme dicts from ttkbootstrap.themes.{standard,user} at
# runtime via attribute lookup, so collect the whole package to be safe.
tb_datas, tb_binaries, tb_hidden = collect_all("ttkbootstrap")

# imageio-ffmpeg ships the ffmpeg binary as a package data file under
# imageio_ffmpeg/binaries/. collect_all pulls it into the bundle so MP4
# export works without a system ffmpeg install.
ff_datas, ff_binaries, ff_hidden = collect_all("imageio_ffmpeg")

# imageio looks up format plugins by string name; collect them as hidden
# imports so PIL/FFMPEG writers aren't stripped.
io_hidden = collect_submodules("imageio.plugins")

# Optional icon. Use .ico on Windows, .icns on macOS, .png on Linux.
# If the file is absent (current state of the repo), the build still works
# and the resulting app falls back to the default OS icon.
if sys.platform == "win32":
    icon_path = os.path.join("assets", "icon.ico")
elif sys.platform == "darwin":
    icon_path = os.path.join("assets", "icon.icns")
else:
    icon_path = os.path.join("assets", "icon.png")
icon_arg = icon_path if os.path.exists(icon_path) else None

a = Analysis(
    ["rdstudio/__main__.py"],
    pathex=[],
    binaries=tb_binaries + ff_binaries,
    datas=tb_datas + ff_datas,
    hiddenimports=tb_hidden + ff_hidden + io_hidden + [
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Reaction-Diffusion Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no console window on Windows
    disable_windowed_traceback=False,
    icon=icon_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Reaction-Diffusion Studio",
)

# macOS .app bundle. Only emitted when building on macOS; PyInstaller
# ignores BUNDLE() on other platforms.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Reaction-Diffusion Studio.app",
        icon=icon_arg,
        bundle_identifier="com.rdstudio.app",
    )
