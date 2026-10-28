"""Local, opt-in real-media measurement. Stores private evidence under ignored local/."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from streamcut.dialogs import default_settings
from streamcut.domain import Project, Segment
from streamcut.media import NO_WINDOW, probe


def main():
    folder = Path("local/benchmark").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    source = sys.argv[1]
    meta = probe(source)
    project = Project(source=source, title="Проверка 10 минут", **meta, settings=default_settings())
    project.duration = min(600, project.duration)  # Only this explicitly scoped benchmark.
    project.settings.update(models_dir=str(Path("models").resolve()), visual="выключен", text_search=False)
    snapshot = folder / "snapshot.json"
    snapshot.write_text(project.model_dump_json(), encoding="utf-8")
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-u", "-m", "streamcut.worker", "analyze", str(snapshot),
                                "--folder", str(folder)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    events = []
    memory_peak = [0]
    stop_monitor = threading.Event()
    def monitor():
        while not stop_monitor.wait(0.1):
            try:
                proc = psutil.Process(process.pid)
                current = proc.memory_info().rss+sum(p.memory_info().rss for p in proc.children(recursive=True))
                memory_peak[0] = max(memory_peak[0], current)
            except psutil.Error:
                pass
    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    with (folder / "asr.log").open("w", encoding="utf-8") as log:
        for line in process.stdout:
            log.write(line)
            log.flush()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
            if "checkpoint" in event:
                project.checkpoints.setdefault("analysis", []).append(event["checkpoint"])
            if event["kind"] == "segments":
                project.segments.extend(Segment.model_validate(s) for s in event["items"])
                print(f"ASR chunk {event['progress']:.0%}: {len(event['items'])} utterances", flush=True)
            if event["kind"] == "error":
                print(event["text"], flush=True)
    code = process.wait()
    stop_monitor.set()
    watcher.join()
    report = {"seconds_media": project.duration, "seconds_asr": time.monotonic()-started,
              "sampled_peak_bytes": memory_peak[0], "utterances": len(project.segments), "exit_code": code,
              "note": "Process tree RSS sampled every 0.1s. Source restricted to first 10 min for benchmark."}
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    snapshot.write_text(project.model_dump_json(), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
