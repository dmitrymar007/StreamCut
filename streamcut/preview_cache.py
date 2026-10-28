"""Bounded, cancellable media extraction and persistent timeline summaries."""
from __future__ import annotations

import json
import math
import subprocess
import threading
import wave
from pathlib import Path

from .media import NO_WINDOW, executable

WAVE_SECONDS = 30
WAVE_STEP = 0.05


def thumbnail_step(seconds: float, pixels: int) -> float:
    desired = max(0.1, seconds / max(1, pixels / 104))
    power = 10 ** math.floor(math.log10(desired))
    return next(n * power for n in (1, 2, 5, 10) if n * power >= desired)


def preview_plan(start: float, end: float, duration: float, pixels: int, position: float):
    step = thumbnail_step(end-start, pixels)
    padding = (end-start)*0.35
    first, last = max(0, start-padding), min(duration, end+padding)
    stamps = [round(i*step, 3) for i in range(max(0, math.floor(first/step)), math.ceil(last/step))
              if i*step < duration]
    # At wide zoom, wave chunks remain bounded at 30 seconds each. They arrive
    # progressively; no whole-file PCM array is ever created.
    chunks = list(range(max(0, int(first//WAVE_SECONDS)), math.ceil(last/WAVE_SECONDS)))
    visible = lambda t: 0 if start <= t < end else 1
    stamps.sort(key=lambda t: (visible(t), abs(t-position)))
    chunks.sort(key=lambda n: (visible(n*WAVE_SECONDS+WAVE_SECONDS/2), abs(n*WAVE_SECONDS+15-position)))
    return step, stamps, chunks


def cancellable_run(args, cancel: threading.Event):
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               creationflags=NO_WINDOW)
    try:
        while True:
            if cancel.is_set():
                process.terminate()
                process.communicate(timeout=3)
                raise InterruptedError("Подгрузка заменена новым диапазоном")
            try:
                _, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError(stderr.decode("utf-8", "replace")[-1000:])
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


class PreviewCache:
    def __init__(self, folder, source, identity, audio, duration):
        self.folder = Path(folder)/"timeline-v2"/identity
        self.folder.mkdir(parents=True, exist_ok=True)
        self.source, self.audio, self.duration = source, audio, duration

    def frame(self, stamp, cancel):
        path = self.folder/f"frame-{round(stamp*1000):012}.jpg"
        if path.exists():
            data = path.read_bytes()
            if data.startswith(b"\xff\xd8"):
                return data
        temporary = path.with_name(path.stem+".partial.jpg")
        try:
            # Filmstrip thumbnails are representative keyframes. Exact seeking
            # belongs to the player on drag release, not to dozens of previews.
            cancellable_run([executable("ffmpeg"), "-nostdin", "-v", "error", "-y", "-threads", "2",
                "-skip_frame", "nokey", "-ss", str(stamp), "-noaccurate_seek", "-i", self.source,
                "-frames:v", "1", "-an", "-sn", "-fps_mode", "passthrough",
                "-filter_threads", "1", "-vf", "scale=320:-2", "-update", "1", str(temporary)], cancel)
            temporary.replace(path)
            return path.read_bytes()
        finally:
            temporary.unlink(missing_ok=True)

    def waveform(self, chunk, cancel):
        import numpy as np
        path = self.folder/f"wave-a{self.audio}-{chunk:08}.json"
        if path.exists():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(result, list) and all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in result):
                    return result
            except (OSError, ValueError):
                pass
        audio = path.with_suffix(".wav")
        start = chunk*WAVE_SECONDS
        duration = min(WAVE_SECONDS, self.duration-start)
        try:
            cancellable_run([executable("ffmpeg"), "-nostdin", "-v", "error", "-y", "-threads", "1",
                "-ss", str(start), "-i", self.source, "-t", str(duration), "-map", f"0:a:{self.audio}",
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)], cancel)
            with wave.open(str(audio)) as wav:
                samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32)
            size = round(16000*WAVE_STEP)
            result = [float(np.max(np.abs(samples[i:i+size])))/32768 for i in range(0, len(samples), size)]
            temporary = path.with_suffix(".partial.json")
            temporary.write_text(json.dumps(result), encoding="utf-8")
            temporary.replace(path)
            return result
        finally:
            audio.unlink(missing_ok=True)
