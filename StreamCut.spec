# Two executables share the same libraries: windowed GUI and pipe-capable worker.
from PyInstaller.utils.hooks import collect_data_files
from pathlib import Path
import os
import sys

# Build in a predictable DLL search path. Bundled tool runtimes can expose a
# different ICU ABI than the Windows ICU used by Qt 6.12.
windows = Path(os.environ['SystemRoot'])
os.environ['PATH'] = os.pathsep.join([str(Path(sys.base_prefix)), str(Path(sys.base_prefix)/'DLLs'),
                                    str(windows/'System32'), str(windows)])

notices = [(str(p), 'vendor') for p in Path('vendor').iterdir()
           if p.is_file() and any(s in p.name.lower() for s in ('license', 'copying'))]

a = Analysis(
    ['scripts/entrypoint.py'], pathex=['.'],
    binaries=[('vendor/ffmpeg.exe', 'vendor'), ('vendor/ffprobe.exe', 'vendor'), ('vendor/libmpv-2.dll', 'vendor')],
    datas=collect_data_files('faster_whisper') + [('assets/streamcut.ico', 'assets'),
        ('assets/nvidia-manifest.json', 'assets'), ('assets/ollama-installer.json', 'assets'),
        ('THIRD_PARTY.md', '.'), ('LICENSE', '.'), ('vendor/notices', 'third-party-notices')] + notices,
    hiddenimports=['streamcut.worker', 'faster_whisper', 'mpv'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'tkinter'],
)
# Qt on the supported Windows 11 target uses the OS ICU, not a conda/poppler ICU.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll'}]
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='StreamCut', console=False, icon='assets/streamcut.ico')
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='StreamCut-worker', console=True)
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name='StreamCut')
