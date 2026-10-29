"""Verify the packaged GUI in both formats without changing the user's project."""
import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings

from streamcut import __version__
from streamcut.dialogs import default_settings
from streamcut.domain import Clip, ExportOptions, Project, Span, output_dimensions
from streamcut.media import executable, probe, run
from streamcut.storage import ProjectStore


def main():
    source = str(Path(sys.argv[1]).resolve())
    executable_path = Path(sys.argv[2] if len(sys.argv) > 2 else "dist/StreamCut/StreamCut.exe").resolve()
    assert executable_path.is_file(), executable_path
    metadata = probe(source)
    assert metadata["duration"] > 223, "Use a source longer than four minutes"
    root = Path("local/release-smoke")/__version__
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    preferences = QSettings("StreamCut", "StreamCut")
    keys = ("recent_project", "geometry", "panels")
    original = {key: (preferences.contains(key), preferences.value(key)) for key in keys}
    results = []
    try:
        for name, layout, mode in (("original", "исходный", "быстрый"), ("shorts", "9:16", "точный")):
            options = ExportOptions(layout=layout, mode=mode)
            project = Project(source=source, **metadata, settings=default_settings() | {
                "export_defaults_version": 2, "export_options": options.model_dump()})
            project.clips = [Clip(title="Проверка сборки", spans=[Span(start=211.93, end=222.29)])]
            store = ProjectStore(root/name/"project")
            store.save(project)
            output = root/name/"diagnostics"
            output.mkdir(parents=True, exist_ok=True)
            (output/"gui.json").unlink(missing_ok=True)
            environment = os.environ.copy()
            if os.name == "nt":
                windows = Path(os.environ["SystemRoot"])
                environment["PATH"] = str(windows/"System32")+os.pathsep+str(windows)
                environment.pop("PYTHONPATH", None)
                environment.pop("PYTHONHOME", None)
            completed = subprocess.run([str(executable_path), "--project", str(store.folder),
                                        "--smoke-output", str(output)], timeout=40, env=environment)
            assert completed.returncode == 0, (name, completed.returncode)
            result = json.loads((output/"gui.json").read_text(encoding="utf-8"))
            assert result["passed"] and not result["errors"], result
            assert result["timeline_frames"] and result["wave_chunks"], result
            assert result["output_dimensions"] == list(output_dimensions(options, project.width, project.height))
            image = json.loads(run([executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json",
                                    str(output/"frame.jpg")]))["streams"][0]
            if name == "original":
                assert (image["width"], image["height"]) == (project.width, project.height), image
            else:
                assert image["height"] > image["width"], image
            result.update(format=name, frame_size=[image["width"], image["height"]])
            results.append(result)
    finally:
        for key, (existed, value) in original.items():
            if existed:
                preferences.setValue(key, value)
            else:
                preferences.remove(key)
        preferences.sync()
        (root/"report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
