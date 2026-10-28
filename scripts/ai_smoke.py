import json
import subprocess
import sys
import time
from pathlib import Path

from streamcut.domain import Clip, Project, Span, merge_candidate
from streamcut.media import NO_WINDOW, probe
from streamcut.storage import ProjectStore

folder = Path("local/benchmark").resolve()
snapshot = folder / "snapshot.json"
project = Project.model_validate_json(snapshot.read_text(encoding="utf-8"))
project.settings.update(transcribe=False, text_search=True, visual="подробный", visual_start=300,
                        visual_end=315, frame_interval=5)
snapshot.write_text(project.model_dump_json(), encoding="utf-8")
started = time.monotonic()
stage_start = started
report = {}
process = subprocess.Popen([sys.executable, "-u", "-m", "streamcut.worker", "analyze", str(snapshot),
                            "--folder", str(folder)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
with (folder/"search.log").open("w", encoding="utf-8") as log:
    for line in process.stdout:
        log.write(line)
        log.flush()
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if "checkpoint" in event:
            project.checkpoints.setdefault("analysis", []).append(event["checkpoint"])
        if event["kind"] in ("clips", "visual"):
            for clip in event["items"]:
                merge_candidate(project.clips, Clip.model_validate(clip))
            report[event["kind"]] = {"count": len(event["items"]), "seconds": time.monotonic()-stage_start}
            stage_start = time.monotonic()
        if event["kind"] == "visual":
            project.coverage.extend(Span(start=t, end=t+0.001) for t in event["samples"])
        if event["kind"] == "status":
            print(event["text"], flush=True)
        if event["kind"] == "error":
            print(event["text"], flush=True)
code = process.wait()
report["exit_code"] = code
report["seconds_total"] = time.monotonic()-started
project.duration = probe(project.source)["duration"]
project.settings.pop("visual_start", None)
project.settings.pop("visual_end", None)
project.settings["visual"] = "выключен"
project.settings["transcribe"] = True
ProjectStore(folder/"project").save(project)
(folder/"ai-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
sys.exit(code)
