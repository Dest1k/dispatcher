# PyInstaller spec — build a single Windows .exe.
#
#   pip install pyinstaller
#   pyinstaller build.spec
#
# Result: dist/MultiAIControlCenter/MultiAIControlCenter.exe
# (run on Windows to produce a Windows binary; PyInstaller is not a
#  cross-compiler).

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["PySide6.QtSvg"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MultiAIControlCenter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI app, no console window
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MultiAIControlCenter",
)
