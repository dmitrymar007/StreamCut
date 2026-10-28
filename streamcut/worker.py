"""Isolated heavy tasks. Communicates with Qt using newline-delimited JSON."""
import argparse
import gc
import hashlib
import json
import shutil
import sys
import time
from collections import deque
from pathlib import Path

from .domain import Clip, Project, Segment, Span, fingerprint
from .media import extract_audio, extract_frame
from .ollama import Ollama


def emit(kind, **data):
    print(json.dumps({"kind": kind, **data}, ensure_ascii=False), flush=True)


def key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def control(folder):
    while (folder / "pause").exists():
        time.sleep(0.3)


def estimate(remaining, completed, started, batches):
    if batches < 2 or completed <= 0:
        return None
    return max(0, round((time.monotonic()-started)/completed*remaining))


def analyze(snapshot, folder):
    project = Project.model_validate_json(snapshot.read_text(encoding="utf-8"))
    if fingerprint(project.source) != project.fingerprint:
        raise ValueError("Исходник изменён. Переподключите исходный файл или создайте новый проект")
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
        try:
            model = WhisperModel(asr_model, device="cpu", compute_type="int8", cpu_threads=threads,
                                 download_root=settings["models_dir"], local_files_only=True)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Whisper {asr_model} не подготовлен. Откройте «Модели» и скачайте его. {exc}") from exc
        starts = range(0, int(project.duration + 0.999), 300)
        pending = sum(min(300, project.duration-s) for s in starts if key(base+[s]) not in completed)
        started, measured, batches, eta = time.monotonic(), 0.0, 0, None
        for start in starts:
            checkpoint = key(base + [start])
            if checkpoint in completed:
                continue
            control(folder)
            duration = min(300, project.duration - start)
            emit("status", text=f"Речь · {start//60}–{(start+duration)/60:.0f} мин", eta_seconds=eta)
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
                edited = [s for s in segments if s.edited and s.start < start+duration and s.end > start]
                batch = [s for s in batch if not any(e.start < s.end and e.end > s.start for e in edited)]
                segments = [s for s in segments if s.edited or not (start <= s.start < start+duration)] + batch
                measured += duration
                batches += 1
                eta = estimate(pending-measured, measured, started, batches)
                emit("segments", items=[s.model_dump() for s in batch], checkpoint=checkpoint,
                     block_start=start, block_end=start+duration,
                     eta_seconds=eta,
                     progress=min(1, (start + duration) / project.duration))
            finally:
                audio.unlink(missing_ok=True)
        del model
        gc.collect()
    if settings.get("text_search", True):
        revision = client.revision(model_name)
        emit("model", name=model_name, revision=revision)
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
        def window_key(window):
            return key(["text-v1", model_name, revision, settings.get("criteria"),
                        [(s.id, s.text, s.start, s.end) for s in window]])
        pending = sum(window_key(w) not in completed for w in windows)
        started, measured, eta = time.monotonic(), 0, None
        for index, window in enumerate(windows):
            control(folder)
            checkpoint = window_key(window)
            if checkpoint in completed:
                continue
            emit("status", text=f"Поиск по речи · окно {index+1}/{len(windows)}", eta_seconds=eta)
            records = [{"id": s.id, "text": s.text} for s in window]
            result = client.search(model_name, records, settings["criteria"], threads=threads)
            clips = []
            for proposal in result.moments:
                ids = [s.id for s in window]
                a, b = ids.index(proposal.first_id), ids.index(proposal.last_id)
                clips.append(Clip(title=proposal.title, reason=proposal.reason, score=proposal.score,
                                  spans=[Span(start=max(0, window[a].start-1),
                                              end=min(project.duration, window[b].end+1))],
                                  sources=["речь"], evidence=ids[a:b+1],
                                  quotes=[s.model_dump(include={"id", "start", "end", "text"}) for s in window[a:b+1]]))
            measured += 1
            eta = estimate(pending-measured, measured, started, measured)
            emit("clips", items=[c.model_dump() for c in clips], checkpoint=checkpoint, eta_seconds=eta,
                 progress=(index+1)/max(1, len(windows)))
        client.release(model_name)
    visual = settings.get("visual", "выключен")
    if visual != "выключен":
        visual_model = settings.get("vision_model", model_name)
        if "vision" not in client.show(visual_model).get("capabilities", []):
            raise ValueError("Выбранная модель не поддерживает изображения")
        revision = client.revision(visual_model)
        emit("model", name=visual_model, revision=revision)
        interval = int(settings.get("frame_interval", 30 if visual == "экономный" else 5))
        start_at = float(settings.get("visual_start", 0))
        end_at = min(project.duration, float(settings.get("visual_end", project.duration)))
        coarse_interval = interval
        ranges = deque([(start_at, end_at, interval, False)])
        scheduled = set()

        def refine(clip):
            a, b = max(start_at, clip.start), min(end_at, clip.end)
            signature = (a, b)
            if visual == "экономный" and coarse_interval > 5 and a < b and signature not in scheduled:
                scheduled.add(signature)
                ranges.append((a, b, 5, True))

        for clip in project.clips:
            if "изображение" in clip.sources:
                refine(clip)
        while ranges:
            begin, finish, interval, refined = ranges.popleft()
            position = begin
            started, measured, batches, eta = time.monotonic(), 0, 0, None
            while position < finish:
                control(folder)
                times = [position+i*interval for i in range(3) if position+i*interval < finish]
                context = [(s.id, s.text) for s in segments if s.start < times[-1]+interval and s.end > times[0]]
                checkpoint = key(["vision-v2", project.fingerprint, visual_model, revision, interval,
                                  times, context, settings["criteria"]])
                if checkpoint in completed:
                    position += interval*3
                    continue
                mode = "Уточнение изображения" if refined else "Изображение"
                emit("status", text=f"{mode} · {position/60:.1f}/{finish/60:.1f} мин · шаг {interval} с", eta_seconds=eta)
                images, records = [], []
                try:
                    for n, stamp in enumerate(times):
                        image = folder/f"frame-{n}.jpg"
                        extract_frame(project.source, stamp, image)
                        images.append(image)
                        speech = " ".join(s.text for s in segments if s.start < stamp+interval and s.end > stamp)
                        records.append({"id": f"frame:{stamp:.3f}", "seconds": stamp, "speech": speech[:1500]})
                    result = client.search(visual_model, records, settings["criteria"], images, threads)
                    clips, evidence = [], []
                    for proposal in result.moments:
                        reason = proposal.observation+"\n"+proposal.reason
                        a, b = float(proposal.first_id.split(":")[1]), float(proposal.last_id.split(":")[1])
                        clip = Clip(title=proposal.title, reason=reason, score=proposal.score,
                                    spans=[Span(start=max(0, a-interval), end=min(project.duration, b+interval))],
                                    sources=["изображение"], evidence=[proposal.first_id, proposal.last_id])
                        clips.append(clip)
                        if not refined:
                            refine(clip)
                        for frame_id in dict.fromkeys(clip.evidence):
                            n = next(i for i, record in enumerate(records) if record["id"] == frame_id)
                            evidence_folder = folder.parent/"evidence"
                            evidence_folder.mkdir(exist_ok=True)
                            saved_frame = evidence_folder/(key([project.fingerprint, frame_id])+".jpg")
                            shutil.copy2(images[n], saved_frame)
                            evidence.append({"id": frame_id, "seconds": records[n]["seconds"],
                                             "path": saved_frame.relative_to(folder.parent).as_posix(),
                                             "description": reason, "model": visual_model, "interval": interval})
                    measured += len(times)
                    batches += 1
                    eta = estimate(max(0, (finish-position-interval*len(times))/interval), measured, started, batches)
                    emit("visual", items=[c.model_dump() for c in clips], checkpoint=checkpoint, eta_seconds=eta,
                         samples=times, interval=interval, evidence=evidence,
                         progress=min(1, (position-begin+interval*3)/max(0.001, finish-begin)))
                    completed.add(checkpoint)
                finally:
                    for image in images:
                        image.unlink(missing_ok=True)
                position += interval*3
        client.release(visual_model)
    emit("done", text="Анализ завершён")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["analyze", "pull", "whisper", "test-text", "test-asr", "test-image"])
    parser.add_argument("value")
    parser.add_argument("--folder", default=".")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:4b")
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
                            status = item.get("status", "")
                            status = {"pulling manifest": "Получение описания модели", "verifying sha256 digest": "Проверка контрольной суммы",
                                      "writing manifest": "Сохранение модели", "success": "Модель готова"}.get(status,
                                      "Загрузка слоя модели" if status.startswith("pulling ") else status)
                            emit("status", text=status,
                                 progress=item.get("completed", 0)/max(1, item.get("total", 1)))
                            last_emit = time.monotonic()
            emit("done", text="Модель Ollama скачана")
        elif args.mode == "whisper":
            from faster_whisper.utils import download_model
            emit("status", text="Скачивание Whisper (прогресс в журнале загрузки)…")
            download_model(args.value, cache_dir=args.folder)
            emit("done", text="Whisper готов к офлайн-работе")
        elif args.mode == "test-text":
            client = Ollama(args.url)
            result = client.search(args.value, [
                {"id": "s1", "text": "Я решил починить компьютер и случайно выключил свет во всём доме."},
                {"id": "s2", "text": "Теперь мой компьютер — самая тихая вещь в доме."}], "Найди забавную законченную историю")
            emit("done", text="Тест текста: "+result.model_dump_json())
            client.release(args.value)
        elif args.mode == "test-image":
            client = Ollama(args.url)
            if "vision" not in client.show(args.model).get("capabilities", []):
                raise ValueError("Модель не поддерживает изображения")
            emit("done", text=client.describe(args.model, Path(args.value)))
            client.release(args.model)
        else:
            import tempfile

            from faster_whisper import WhisperModel
            with tempfile.TemporaryDirectory(prefix="streamcut-test-") as temporary:
                audio = Path(temporary)/"sample.wav"
                extract_audio(args.value, 0, 0, 30, audio)
                model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=4,
                                     download_root=args.folder, local_files_only=True)
                segments, _ = model.transcribe(str(audio), language="ru", vad_filter=True, task="transcribe")
                emit("done", text="Первые 30 секунд: "+" ".join(s.text for s in segments))
    except Exception as exc:
        emit("error", text=f"{type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
