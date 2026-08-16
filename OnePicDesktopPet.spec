# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path


datas = [("assets", "assets"), ("config", "config"), ("resources", "resources")]
hiddenimports = ["winrt.windows.media.control", "uiautomation"] if sys.platform == "win32" else []
private_assets = Path("user_assets")
if os.environ.get("ONEPIC_INCLUDE_USER_ASSETS") == "1" and private_assets.exists():
    workflow = private_assets / "workflow.json"
    pet_assets = private_assets / "pet"
    if workflow.is_file():
        datas.append((str(workflow), "user_assets"))
    if pet_assets.is_dir():
        datas.append((str(pet_assets), "user_assets/pet"))
    for selfie_name in ("selfie.png", "selfie.jpg", "selfie.jpeg", "image.png"):
        selfie = private_assets / selfie_name
        if selfie.is_file():
            datas.append((str(selfie), "user_assets"))

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lili",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets\\icons\\pet.png"] if sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Lili",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Lili.app",
        icon="build/macos/pet.icns",
        bundle_identifier="io.github.leungjunchiang.lili",
        info_plist={
            "CFBundleDisplayName": "Lili",
            "CFBundleShortVersionString": "0.22.50",
            "CFBundleVersion": "0.22.50",
            "CFBundlePackageType": "APPL",
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "Lili 需要在您点击音乐控制后操作 Apple Music 或 Spotify 的播放、暂停与切歌。",
        },
    )
