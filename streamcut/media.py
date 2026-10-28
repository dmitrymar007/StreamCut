from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .domain import ExportJob, Segment, fingerprint, timecode

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
VENDOR = ROOT / "vendor"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def executable(name: str) -> str:
    local = VENDOR / (name + (".exe" if os.name == "nt" else ""))
    found = str(local) if local.exists() else shutil.which(name)
    if not found:
        raise RuntimeError(f"Не найден {name}. Запустите scripts/setup_media.py")
    return found


def run(args: list[str], timeout=120) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:])
    return result.stdout


def probe(path: str) -> dict:
    data = json.loads(run([executable("ffprobe"), "-v", "error", "-show_format", "-show_streams",
                           "-of", "json", path]))
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ValueError("В файле не обнаружена видеодорожка")
    num, den = video.get("avg_frame_rate", "25/1").split("/")
    fps = float(num) / max(float(den), 1)
    return {"duration": float(data["format"].get("duration", video.get("duration", 0))),
            "width": video["width"], "height": video["height"], "fps": fps or 25,
            "audio_tracks": [{"index": s["index"], "label": s.get("tags", {}).get("title", ""),
                              "language": s.get("tags", {}).get("language", "und")}
                             for s in data["streams"] if s["codec_type"] == "audio"],
            "fingerprint": fingerprint(path)}


def extract_audio(source, audio, start, duration, destination):
    run([executable("ffmpeg"), "-nostdin", "-y", "-ss", str(start), "-i", source,
         "-t", str(duration), "-map", f"0:a:{audio}", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(destination)], timeout=max(120, duration * 2))


def extract_frame(source, position, destination):
    run([executable("ffmpeg"), "-nostdin", "-y", "-ss", str(position), "-i", source,
         "-frames:v", "1", "-vf", "scale=640:-2", "-update", "1", str(destination)])


def subtitles(segments: list[Segment], start: float, end: float) -> str:
    lines = []
    for seg in segments:
        if seg.end <= start or seg.start >= end:
            continue
        a, b = max(0, seg.start - start), min(end - start, seg.end - start)
        text = seg.text.replace("\r", " ").replace("\n\n", "\n")
        lines.append(f"{len(lines)+1}\n{timecode(a).replace('.', ',')} --> "
                     f"{timecode(b).replace('.', ',')}\n{text}\n")
    return "\n".join(lines)


def video_filter(options) -> str:
    h = options.height
    if options.layout == "9:16":
        w = round(h * 9 / 16 / 2) * 2
    elif options.layout == "1:1":
        w = h
    else:
        w = round(h * 16 / 9 / 2) * 2
    if options.layout == "исходный":
        return f"scale=-2:'min({h},ih)'"
    if options.layout == "размытый фон":
        return (f"split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},boxblur=20:2[back];[fg]scale={w}:{h}:"
                f"force_original_aspect_ratio=decrease[front];[back][front]overlay=(W-w)/2:(H-h)/2")
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:(iw-ow)*{options.crop_x}:(ih-oh)*{options.crop_y},setsar=1")


def export_clip(job: ExportJob, cancel: threading.Event, progress):
    if fingerprint(job.source) != job.fingerprint:
        raise ValueError("Исходник изменился. Переподключите его перед экспортом")
    target = Path(job.destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError("Файл уже существует: " + str(target))
    needed = max(32 * 1024 * 1024, job.clip.duration * 1_000_000)
    if shutil.disk_usage(target.parent).free < needed:
        raise OSError("Недостаточно свободного места для экспорта")
    temporary = target.with_name(f".{target.stem}.{job.id}.partial.mp4")
    srt = target.with_name(f".{job.id}.srt")
    span = job.clip.spans[0]
    srt_text = subtitles(job.segments, span.start, span.end)
    try:
        args = [executable("ffmpeg"), "-nostdin", "-y", "-ss", str(span.start), "-i", job.source,
                "-t", str(span.duration), "-map", "0:v:0", "-map", f"0:a:{job.audio}?",
                "-sn", "-dn"]
        if job.options.mode == "быстрый":
            if job.options.layout != "исходный" or job.options.subtitles:
                raise ValueError("Быстрый режим не поддерживает кадрирование и прожиг субтитров")
            args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        else:
            filters = video_filter(job.options)
            if job.options.subtitles and srt_text:
                srt.write_text(srt_text, encoding="utf-8")
                escaped = srt.as_posix().replace(":", "\\:").replace("'", "\\'")
                filters += f",subtitles=filename='{escaped}'"
            fade = min(job.options.fade, span.duration / 2)
            audio_filter = (f"volume={job.options.volume},afade=t=in:d={fade},"
                            f"afade=t=out:st={span.duration-fade}:d={fade}")
            args += ["-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", str(job.options.crf),
                     "-pix_fmt", "yuv420p", "-threads", "4", "-af", audio_filter,
                     "-c:a", "aac", "-b:a", "192k"]
        args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(temporary)]
        log = target.with_name(f".{job.id}.log")
        with log.open("w+", encoding="utf-8") as stderr:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stderr, text=True,
                                       encoding="utf-8", creationflags=NO_WINDOW)
            def stop():
                while process.poll() is None:
                    if cancel.wait(0.2):
                        process.terminate()
                        return
            watcher = threading.Thread(target=stop, daemon=True)
            watcher.start()
            for line in process.stdout:
                if line.startswith("out_time_us="):
                    try:
                        progress(min(0.99, int(line.split("=")[1]) / 1e6 / span.duration))
                    except ValueError:
                        pass
            code = process.wait()
            watcher.join(timeout=1)
            if cancel.is_set():
                raise InterruptedError("Экспорт отменён")
            if code:
                stderr.seek(0)
                raise RuntimeError(stderr.read()[-2500:])
        if target.exists():
            raise FileExistsError("Имя занято во время экспорта")
        # Windows rename refuses an existing destination (no overwrite).
        temporary.rename(target)
        if job.options.sidecar and srt_text:
            sidecar = target.with_suffix(".srt")
            if not sidecar.exists():
                with sidecar.open("x", encoding="utf-8") as out:
                    out.write(srt_text)
        progress(1)
    finally:
        temporary.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
        if "log" in locals():
            log.unlink(missing_ok=True)
