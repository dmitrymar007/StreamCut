"""Run packaged CUDA ASR and text/vision analysis against a caller's test video."""
import json
import subprocess
import sys
import time
from pathlib import Path

from streamcut.dialogs import default_settings
from streamcut.domain import Clip, Project, Segment
from streamcut.media import probe
from streamcut.storage import ProjectStore


def main():
    source = Path(sys.argv[1]).resolve()
    helper = Path(sys.argv[2]).resolve()
    folder = Path("local/gpu-smoke").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    settings = default_settings() | {"whisper_device": "cuda", "visual": "подробный",
        "visual_end": 3, "frame_interval": 1,
        "criteria": "Найди забавную законченную историю. Пропускай обычные статичные кадры."}
    project = Project(source=str(source), **probe(str(source)), settings=settings)
    snapshot = folder/"analysis.json"
    snapshot.write_text(project.model_dump_json(), encoding="utf-8")
    started = time.monotonic()
    completed = subprocess.run([str(helper), "--worker", "analyze", str(snapshot), "--folder", str(folder)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=900)
    (folder/"worker.log").write_text(completed.stdout+completed.stderr, encoding="utf-8")
    events = []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    assert completed.returncode == 0, completed.stdout+completed.stderr
    assert any(e["kind"] == "hardware" and e.get("device") == "cuda" for e in events), events
    assert events[-1]["kind"] == "done", events
    assert any(e["kind"] == "segments" for e in events), events
    assert any(e["kind"] == "clips" for e in events), events
    assert any(e["kind"] == "visual" for e in events), events
    report = {"passed": True, "seconds": round(time.monotonic()-started, 2),
              "hardware": [e for e in events if e["kind"] == "hardware"],
              "segment_batches": sum(e["kind"] == "segments" for e in events),
              "text_candidates": sum(len(e.get("items", [])) for e in events if e["kind"] == "clips"),
              "visual_batches": sum(e["kind"] == "visual" for e in events)}
    for event in events:
        if event["kind"] == "segments":
            project.segments.extend(Segment.model_validate(item) for item in event["items"])
        if event["kind"] in ("clips", "visual"):
            project.clips.extend(Clip.model_validate(item) for item in event["items"])
    ProjectStore(folder/"project").save(project)
    (folder/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
