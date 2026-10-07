# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for building standalone unical executable."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect all resources from dependencies with non-code assets or dynamic imports
textual_datas, textual_binaries, textual_hiddenimports = collect_all("textual")
google_datas, google_binaries, google_hiddenimports = collect_all("googleapiclient")
tzdata_datas, tzdata_binaries, tzdata_hiddenimports = collect_all("tzdata")
platformdirs_datas, platformdirs_binaries, platformdirs_hiddenimports = collect_all(
    "platformdirs"
)

datas = textual_datas + google_datas + tzdata_datas + platformdirs_datas
binaries = textual_binaries + google_binaries + tzdata_binaries + platformdirs_binaries
hiddenimports = (
    textual_hiddenimports
    + google_hiddenimports
    + tzdata_hiddenimports
    + platformdirs_hiddenimports
    + [
        "google_auth_oauthlib",
        "google_auth_oauthlib.flow",
        "google.auth.transport.requests",
        "googleapiclient.discovery",
        "googleapiclient.http",
    ]
)

spec_dir = Path(SPECPATH).resolve()
repo_root = spec_dir.parent

a = Analysis(
    [str(repo_root / "src" / "unical" / "__main__.py")],
    pathex=[str(repo_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="unical",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
