# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [("app/static", "app/static")]
binaries = []
hiddenimports = []

for pkg in ["sherpa_onnx"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

# onedir, not onefile: a onefile build re-extracts the whole app to a fresh
# %TEMP%\_MEI... folder on every single launch before any Python here runs.
# Two overlapping launches race that extraction step natively, before our
# own instance lock (or anything else in app_entry.py) gets a chance to run
# -- that's the actual cause of the pyi_rth_pkgres crashes on double-launch.
# onedir removes the extraction step entirely: the exe just runs directly
# from an already-unpacked folder.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ATLiveCaption",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="branding/ico/at_livecaption_blue.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ATLiveCaption",
)
