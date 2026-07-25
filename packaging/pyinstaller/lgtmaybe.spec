# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH).parents[1]
ast_grep = shutil.which("ast-grep")

datas = collect_data_files("lgtmaybe") + collect_data_files("litellm")
binaries = [(ast_grep, ".")] if ast_grep else []
hiddenimports = ["tiktoken_ext", "tiktoken_ext.openai_public"]

analysis = Analysis(
    [str(project_root / "packaging" / "pyinstaller" / "entry.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        str(project_root / "packaging" / "pyinstaller" / "runtime_path.py")
    ],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="lgtmaybe",
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
