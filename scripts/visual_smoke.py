import json
import subprocess
import sys
import time
from pathlib import Path

from streamcut.dialogs import default_settings
from streamcut.domain import Clip, Project, Span, uid
from streamcut.media import NO_WINDOW, probe
from streamcut.storage import ProjectStore

source = sys.argv[1]
folder = Path("local/visual-check")/uid()[:8]
store = ProjectStore(folder.resolve())
project = Project(source=source, title="Визуальная проверка без речи", **probe(source), settings=default_settings())
project.settings.update(transcribe=False, text_search=False, visual="подробный", visual_start=3600,
    visual_end=3615, frame_interval=5,
    criteria="Заметные визуальные изменения, необычные движения и реакции персонажей. Для обычной статичной сцены верни пустой список.")
snapshot = store.folder/"cache"/"analysis.json"
snapshot.write_text(project.model_dump_json(), encoding="utf-8")
started = time.monotonic()
process = subprocess.Popen([sys.executable, "-u", "-m", "streamcut.worker", "analyze", str(snapshot),
    "--folder", str(snapshot.parent)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
with (store.folder/"events.jsonl").open("w", encoding="utf-8") as log:
    for line in process.stdout:
        log.write(line)
        log.flush()
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event["kind"] == "visual":
            project.clips.extend(Clip.model_validate(c) for c in event["items"])
            project.visual_evidence.extend(event["evidence"])
            project.coverage.extend(Span(start=t, end=t+0.001) for t in event["samples"])
            project.checkpoints.setdefault("analysis", []).append(event["checkpoint"])
            store.save(project)
        if "text" in event:
            print(event["text"], flush=True)
code = process.wait()
report = {"exit_code": code, "seconds": time.monotonic()-started, "clips": len(project.clips),
          "evidence": len(project.visual_evidence), "samples": len(project.coverage), "project": str(store.folder)}
(store.folder/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
sys.exit(code)
