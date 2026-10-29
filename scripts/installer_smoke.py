"""Install/uninstall the real NSIS package, preserving pre-existing shortcuts."""
import ctypes
import json
import subprocess
import sys
import time
import winreg
from pathlib import Path

from streamcut.domain import Clip, Project, Span
from streamcut.storage import ProjectStore


def special_folder(identifier):
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, buffer):
        raise OSError("Cannot resolve user shortcut folder")
    return Path(buffer.value)


def main():
    installer = Path(sys.argv[1]).resolve()
    source = str(Path(sys.argv[2]).resolve())
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\StreamCut\Installer", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            raise RuntimeError("An installed StreamCut already exists; skip this isolated installer test")
    except FileNotFoundError:
        pass
    folder = Path("local/installer-smoke").resolve()
    target = folder/"Программа StreamCut"
    assert target.is_relative_to(folder)
    shortcuts = [special_folder(0x10)/"StreamCut.lnk", special_folder(0x02)/"StreamCut"/"StreamCut.lnk"]
    previous = {path: path.read_bytes() if path.is_file() else None for path in shortcuts}
    store = ProjectStore(folder/"user-data"/"project")
    project = Project(clips=[Clip(title="Правки пользователя", spans=[Span(start=1, end=7)])])
    store.save(project)
    installed = False
    report = {}
    try:
        # NSIS requires /D to be the last, unquoted command-line tail. No shell
        # is involved; target is the fixed test directory above.
        command = subprocess.list2cmdline([str(installer), "/S"])+" /D="+str(target)
        completed = subprocess.run(command, timeout=180)
        assert completed.returncode == 0, completed.returncode
        installed = True
        assert (target/"StreamCut.exe").is_file() and (target/".streamcut-installed").is_file(), target
        assert (target/"_internal"/"assets"/"nvidia-manifest.json").is_file()
        protected = target/"user-content-preserve.txt"
        protected.write_text("User content must survive uninstall", encoding="utf-8")
        completed = subprocess.run([sys.executable, "-m", "scripts.release_smoke", source,
                                    str(target/"StreamCut.exe")], timeout=100)
        assert completed.returncode == 0, "Installed GUI failed"
        report["installed_gui_original_and_shorts"] = True
        report["unicode_install_path"] = str(target)
        completed = subprocess.run([str(target/"Uninstall.exe"), "/S"], timeout=60)
        assert completed.returncode == 0
        started = time.monotonic()
        while (target/"StreamCut.exe").exists() and time.monotonic()-started < 30:
            time.sleep(0.2)
        assert not (target/"StreamCut.exe").exists(), "Uninstall still running"
        assert protected.read_text(encoding="utf-8") == "User content must survive uninstall"
        assert store.load().clips[0].id == project.clips[0].id
        assert store.load().clips[0].spans == project.clips[0].spans
        installed = False
        report.update(uninstall_preserves_project=True, uninstall_preserves_user_content=True, passed=True)
    finally:
        if installed and (target/"Uninstall.exe").is_file():
            subprocess.run([str(target/"Uninstall.exe"), "/S"], timeout=60)
            time.sleep(2)
        for path, content in previous.items():
            if content is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        (folder/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Installer smoke passed:", json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
