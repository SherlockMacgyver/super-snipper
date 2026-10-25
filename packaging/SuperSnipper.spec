# One-folder distribution: Qt DLLs remain replaceable, no runtime extraction.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
datas = collect_data_files('soundcard')
for package in ('PySide6-Essentials', 'shiboken6', 'Pillow', 'numpy', 'psutil', 'soundcard', 'cffi', 'pycparser'):
    datas += copy_metadata(package)
a = Analysis([str(root / 'super_snipper.py')], pathex=[str(root)],
             binaries=[], datas=datas, hiddenimports=['_cffi_backend', 'soundcard.mediafoundation'],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['matplotlib', 'IPython', 'pytest'], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SuperSnipper',
          console=False, disable_windowed_traceback=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SuperSnipperCLI',
          console=True, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name='SuperSnipper', upx=False)
