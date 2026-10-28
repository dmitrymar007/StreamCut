"""Isolated heavy tasks. Communicates with Qt using newline-delimited JSON."""
import argparse
import gc
import hashlib
import json
import sys
import time
from pathlib import Path

from .domain import Clip, Project, Segment, Span
from .media import extract_audio, extract_frame
from .ollama import Ollama


def emit(kind, **data):
    print(json.dumps({"kind": kind, **data}, ensure_ascii=False), flush=True)


def key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def control(folder):
    while (folder / "pause").exists():
        time.sleep(0.3)


def analyze(snapshot, folder):
    project = Project.model_validate_json(snapshot.read_text(encoding="utf-8"))
    settings = project.settings
    completed = set(project.checkpoints.get("analysis", []))
    client = Ollama(settings.get("ollama_url", "http://127.0.0.1:11434"))
    model_name = settings.get("text_model", "qwen3.5:4b")
    threads = int(settings.get("threads", 4))
    asr_model = settings.get("whisper_model", "small")
    base = [project.fingerprint, project.audio, asr_model, settings.get("language", "ru"),
            settings.get("dictionary", "")]
    segments = list(project.segments)
    if settings.get("transcribe", True):
        from faster_whisper import WhisperModel

        emit("status", text="Загрузка Whisper · CPU INT8")
        # Explicit local-only loading: download models separately from the Models dialog.
        model = WhisperModel(asr_model, device="cpu", compute_type="int8", cpu_threads=threads,
                             download_root=settings["models_dir"], local_files_only=True)
        for start in range(0, int(project.duration + 0.999), 300):
            checkpoint = key(base + [start])
            if checkpoint in completed:
                continue
            control(folder)
            duration = min(300, project.duration - start)
            emit("status", text=f"Речь · {start//60}–{(start+duration)/60:.0f} мин")
            audio = folder / "audio.wav"
            try:
                extract_audio(project.source, project.audio, start, duration, audio)
                result, _ = model.transcribe(str(audio), language=settings.get("language") or None,
                                            task="transcribe", vad_filter=True, word_timestamps=True,
                                            condition_on_previous_text=False, beam_size=3,
                                            initial_prompt=settings.get("dictionary", ""),
                                            no_speech_threshold=0.6)
                batch = []
                for item in result:
                    a, b = max(0, start + item.start), min(project.duration, start + item.end)
                    if b <= a or not item.text.strip():
                        continue
                    batch.append(Segment(id=key([checkpoint, item.start, item.end]), start=a, end=b,
                                         text=item.text.strip(), words=[
                                             {"start": start+w.start, "end": start+w.end, "text": w.word}
                                             for w in (item.words or [])]))
                segments.extend(batch)
                emit("segments", items=[s.model_dump() for s in batch], checkpoint=checkpoint,
                     progress=min(1, (start + duration) / project.duration))
            finally:
                audio.unlink(missing_ok=True)
        del model
        gc.collect()
    if settings.get("text_search", True):
        segments.sort(key=lambda s: s.start)
        # Bound request size by text length; keep a few utterances of overlap.
        windows = []
        position = 0
        while position < len(segments):
            end = position
            chars = 0
            while end < len(segments) and chars + len(segments[end].text) < 14000 and end-position < 100:
                chars += len(segments[end].text) + 100
                end += 1
            end = max(end, position + 1)
            windows.append(segments[position:end])
            position = end if end == len(segments) else max(position + 1, end - 5)
        for index, window in enumerate(windows):
            control(folder)
            checkpoint = key(["text", model_name, settings.get("criteria"),
                              [(s.id, s.text, s.start, s.end) for s in window]])
            if checkpoint in completed:
                continue
            emit("status", text=f"Поиск по речи · окно {index+1}/{len(windows)}")
            records = [{"id": s.id, "text": s.text} for s in window]
            result = client.search(model_name, records, settings["criteria"], threads=threads)
            clips = []
            for proposal in result.moments:
                ids = [s.id for s in window]
                a, b = ids.index(proposal.first_id), ids.index(proposal.last_id)
                clips.append(Clip(title=proposal.title, reason=proposal.reason, score=proposal.score,
                                  spans=[Span(start=max(0, window[a].start-1),
                                              end=min(project.duration, window[b].end+1))],
                                  sources=["речь"], evidence=ids[a:b+1]))
            emit("clips", items=[c.model_dump() for c in clips], checkpoint=checkpoint,
                 progress=(index+1)/max(1, len(windows)))
        client.release(model_name)
    visual = settings.get("visual", "выключен")
    if visual != "выключен":
        visual_model = settings.get("vision_model", model_name)
        if "vision" not in client.show(visual_model).get("capabilities", []):
            raise ValueError("Выбранная модель не поддерживает изображения")
        interval = int(settings.get("frame_interval", 30 if visual == "экономный" else 5))
        start_at = float(settings.get("visual_start", 0))
        end_at = min(project.duration, float(settings.get("visual_end", project.duration)))
        position = start_at
        while position < end_at:
            control(folder)
            times = [position+i*interval for i in range(3) if position+i*interval < end_at]
            checkpoint = key(["vision", project.fingerprint, visual_model, times, settings["criteria"]])
            if checkpoint in completed:
                position += interval * 3
                continue
            emit("status", text=f"Изображение · {position/60:.1f}/{end_at/60:.1f} мин · шаг {interval} с")
            images, records = [], []
            try:
                for n, stamp in enumerate(times):
                    image = folder / f"frame-{n}.jpg"
                    extract_frame(project.source, stamp, image)
                    images.append(image)
                    speech = " ".join(s.text for s in segments if s.start < stamp+interval and s.end > stamp)
                    records.append({"id": f"frame:{stamp:.3f}", "seconds": stamp, "speech": speech[:1500]})
                result = client.search(visual_model, records, settings["criteria"], images, threads)
                clips = []
                for proposal in result.moments:
                    a = float(proposal.first_id.split(":")[1])
                    b = float(proposal.last_id.split(":")[1])
                    clips.append(Clip(title=proposal.title, reason=proposal.reason, score=proposal.score,
                                      spans=[Span(start=max(0, a-interval),
                                                  end=min(project.duration, b+interval))],
                                      sources=["изображение"], evidence=[proposal.first_id, proposal.last_id]))
                emit("visual", items=[c.model_dump() for c in clips], checkpoint=checkpoint,
                     samples=times, interval=interval, progress=min(1, (position+interval*3)/end_at))
            finally:
                for image in images:
                    image.unlink(missing_ok=True)
            position += interval * 3
        client.release(visual_model)
    emit("done", text="Анализ завершён")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["analyze", "pull", "whisper"])
    parser.add_argument("value")
    parser.add_argument("--folder", default=".")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    try:
        if args.mode == "analyze":
            analyze(Path(args.value), Path(args.folder))
        elif args.mode == "pull":
            import httpx
            with httpx.Client(timeout=900, trust_env=False) as c:
                with c.stream("POST", args.url.rstrip("/")+"/api/pull", json={"model": args.value}) as r:
                    r.raise_for_status()
                    last_emit = 0.0
                    for line in r.iter_lines():
                        item = json.loads(line)
                        if item.get("error"):
                            raise RuntimeError(item["error"])
                        if time.monotonic()-last_emit > 0.5 or item.get("status") == "success":
                            emit("status", text=item.get("status", ""),
                                 progress=item.get("completed", 0)/max(1, item.get("total", 1)))
                            last_emit = time.monotonic()
            emit("done", text="Модель Ollama скачана")
        else:
            from faster_whisper.utils import download_model
            emit("status", text="Скачивание Whisper (прогресс в журнале загрузки)…")
            download_model(args.value, cache_dir=args.folder)
            emit("done", text="Whisper готов к офлайн-работе")
    except Exception as exc:
        emit("error", text=f"{type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
