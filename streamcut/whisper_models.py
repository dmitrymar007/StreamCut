"""Verified, resumable Whisper downloads without the native Xet transport."""
import json
import shutil
import time
from pathlib import Path

from filelock import FileLock, Timeout

from .downloads import checksum, download
from .media import ROOT


def manifest_for(name):
    manifest = json.loads((ROOT/"assets"/"whisper-manifest.json").read_text(encoding="utf-8"))
    if name not in manifest:
        raise ValueError("Для автоматической загрузки Whisper выберите small или medium")
    return manifest[name]


def model_path(name, cache):
    """Use a completed managed model; retain compatibility with the old HF cache."""
    folder = Path(cache)/("streamcut-whisper-"+name)
    marker = folder/"verified.json"
    if marker.is_file():
        try:
            manifest = manifest_for(name)
            if json.loads(marker.read_text(encoding="utf-8")) == manifest and all(
                (folder/file).is_file() and (folder/file).stat().st_size == asset["size"]
                for file, asset in manifest["files"].items()
            ):
                return str(folder)
        except (OSError, ValueError):
            pass
    return name


def prepare_whisper(name, cache, emit):
    manifest = manifest_for(name)
    folder = Path(cache)/("streamcut-whisper-"+name)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(folder/"download.lock"), timeout=1):
            return _prepare(name, cache, manifest, folder, emit)
    except Timeout as error:
        raise RuntimeError("Whisper уже загружается другим окном StreamCut. Дождитесь завершения") from error


def _prepare(name, cache, manifest, folder, emit):
    if model_path(name, cache) == str(folder):
        emit("status", text=f"Whisper {name} уже подготовлен", progress=1)
        return str(folder)
    # Existing complete HF files can be reused, but a partial snapshot must not
    # count as an installed model. Xet .incomplete files may contain sparse chunks.
    old = Path(cache)/f"models--Systran--faster-whisper-{name}"/"snapshots"/manifest["revision"]
    total = sum(asset["size"] for asset in manifest["files"].values())
    completed = 0
    for filename, asset in manifest["files"].items():
        target = folder/filename
        cached = old/filename
        if not target.exists() and cached.is_file() and cached.stat().st_size == asset["size"]:
            if checksum(cached) == asset["sha256"]:
                shutil.copy2(cached, target)
        emit("status", text=f"Whisper {name} · {filename} · соединение с Hugging Face…",
             progress=completed/total)
        last_emit = 0.0

        def progress(done, size):
            nonlocal last_emit
            now = time.monotonic()
            if done == size or now-last_emit >= 0.25:
                last_emit = now
                emit("status", text=f"Whisper {name} · {filename} · {done/1e6:.1f} / {size/1e6:.1f} МБ",
                     progress=(completed+done)/total)

        def retry(attempt, error):
            emit("warning", text=f"Whisper · соединение прервано ({type(error).__name__}); "
                 f"попытка {attempt}/3, продолжаем с сохранённого места")

        try:
            download(asset, target, progress, retry_status=retry)
        except Exception as error:
            raise RuntimeError(f"Не удалось скачать Whisper ({filename}): {error}. "
                               "Проверьте доступ к huggingface.co и повторите; докачка сохранена") from error
        completed += asset["size"]
    temporary = folder/"verified.json.part"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(folder/"verified.json")
    emit("status", text=f"Whisper {name} · файлы проверены по SHA256", progress=1)
    return str(folder)
