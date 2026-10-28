# Two executables share the same libraries: windowed GUI and pipe-capable worker.
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['scripts/entrypoint.py'], pathex=['.'],
    binaries=[('vendor/ffmpeg.exe', 'vendor'), ('vendor/ffprobe.exe', 'vendor'), ('vendor/libmpv-2.dll', 'vendor')],
    datas=collect_data_files('faster_whisper'),
    hiddenimports=['streamcut.worker', 'faster_whisper', 'mpv'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'tkinter'],
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='StreamCut', console=False)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='StreamCut-worker', console=True)
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name='StreamCut')
