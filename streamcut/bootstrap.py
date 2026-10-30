"""One-button preparation of local inference on a fresh Windows computer."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .acceleration import CUDA_DLLS, WhisperEngine, hardware_info
from .downloads import download, extract_cuda
from .media import NO_WINDOW, ROOT
from .ollama import Ollama
from .paths import cuda_folder, data_root
from .whisper_models import prepare_whisper


def external_process(args, **kwargs):
    # A PyInstaller DLL directory is inherited by child processes on Windows.
    # Ollama and its installer must resolve their own libraries, not ours.
    frozen_windows = os.name == "nt" and getattr(sys, "frozen", False)
    environment = kwargs.get("env", os.environ).copy()
    blocked = [os.path.normcase(os.path.abspath(p)) for p in (ROOT, cuda_folder())]
    paths = []
    for path in environment.get("PATH", "").split(os.pathsep):
        normalized = os.path.normcase(os.path.abspath(path))
        if not any(normalized == p or normalized.startswith(p+os.sep) for p in blocked):
            paths.append(path)
    environment["PATH"] = os.pathsep.join(paths)
    kwargs["env"] = environment
    if frozen_windows:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(args, **kwargs)
    finally:
        if frozen_windows:
            ctypes.windll.kernel32.SetDllDirectoryW(str(ROOT))


def ollama_executable():
    candidates = [shutil.which("ollama"), str(Path(os.environ.get("LOCALAPPDATA", ""))/"Programs"/"Ollama"/"ollama.exe")]
    return next((Path(p) for p in candidates if p and Path(p).is_file()), None)


def ollama_running(url):
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(url.rstrip("/")+"/api/version")
            return response.is_success and bool(response.json().get("version"))
    except (httpx.HTTPError, ValueError):
        return False


def start_ollama(url):
    if ollama_running(url):
        return
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("Удалённая Ollama недоступна. Проверьте её адрес и соединение")
    executable = ollama_executable()
    if not executable:
        raise RuntimeError("Ollama не установлена. Откройте «Подготовка нейросетей»")
    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = parsed.netloc
    log_folder = data_root()/"logs"
    log_folder.mkdir(parents=True, exist_ok=True)
    with (log_folder/"ollama.log").open("ab") as log:
        # A regular per-user Ollama server, also reused on subsequent launches.
        external_process([str(executable), "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         env=environment, creationflags=NO_WINDOW)
    for _ in range(60):
        if ollama_running(url):
            return
        time.sleep(0.5)
    raise RuntimeError("Ollama не запустилась. Подробности: "+str(log_folder/"ollama.log"))


def prepare(settings, emit):
    folder = data_root()/"downloads"
    folder.mkdir(parents=True, exist_ok=True)
    url = settings.get("ollama_url", "http://127.0.0.1:11434")
    def fetch(asset, label):
        last_emit = 0.0
        def progress(done, total):
            nonlocal last_emit
            now = time.monotonic()
            if done == total or now-last_emit > 0.15:
                last_emit = now
                emit("status", text=f"{label} · {done/1e9:.2f} / {total/1e9:.2f} ГБ", progress=done/max(1, total))
        return download(asset, folder/asset["filename"], progress)

    if not ollama_running(url):
        if not ollama_executable():
            if urlparse(url).hostname not in ("localhost", "127.0.0.1", "::1"):
                raise RuntimeError("Удалённая Ollama недоступна; локальная установка не изменит удалённый сервер")
            manifest = json.loads((ROOT/"assets"/"ollama-installer.json").read_text(encoding="utf-8"))
            installer = fetch(manifest, "Загрузка Ollama")
            emit("status", text="Установка Ollama для текущего пользователя…")
            process = external_process([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=NO_WINDOW)
            code = process.wait(timeout=900)
            if code not in (0, 3010):
                raise RuntimeError(f"Установщик Ollama завершился с кодом {code}; повторите подготовку")
        emit("status", text="Запуск локальной Ollama…")
        start_ollama(url)
    info = hardware_info()
    gpu_requested = settings.get("whisper_device", "auto") != "cpu"
    if settings.get("whisper_device") == "cuda" and not info["cuda_devices"]:
        raise RuntimeError("NVIDIA CUDA не обнаружена. Обновите драйвер либо выберите «Авто / CPU»")
    if gpu_requested and info["cuda_devices"]:
        manifest = json.loads((ROOT/"assets"/"nvidia-manifest.json").read_text(encoding="utf-8"))
        marker = cuda_folder()/"installed.json"
        complete = False
        if marker.is_file():
            try:
                complete = json.loads(marker.read_text(encoding="utf-8")) == manifest and all(
                    (cuda_folder()/name).is_file() for name in (*CUDA_DLLS, "nvrtc64_120_0.dll"))
            except (OSError, ValueError):
                pass
        if not complete:
            for name, asset in manifest.items():
                wheel = fetch(asset, "NVIDIA: "+name)
                emit("status", text="Распаковка библиотек NVIDIA: "+name)
                extract_cuda(wheel, cuda_folder())
            marker.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        emit("status", text="Библиотеки NVIDIA готовы; проверка распознавания…")
    else:
        emit("status", text="Whisper будет использовать CPU; библиотеки NVIDIA не требуются")

    emit("status", text="Whisper small · загрузка недостающих файлов (около 0,5 ГБ)…", progress=0)
    prepare_whisper(settings.get("whisper_model", "small"), settings["models_dir"], emit)
    # Run actual encoder/decoder inference, not just a device-count check.
    import numpy as np
    engine = WhisperEngine(settings, emit)
    try:
        engine.transcribe(np.zeros(16000, dtype=np.float32), language="ru", vad_filter=False, beam_size=1)
        device = engine.device
    finally:
        engine.close()
    client = Ollama(url)
    model = settings.get("text_model", "qwen3.5:4b")
    emit("status", text=f"Проверка {model}…", progress=0)
    if model not in {m["name"] for m in client.models()}:
        with httpx.Client(timeout=httpx.Timeout(1800, connect=20), trust_env=False) as connection:
            with connection.stream("POST", url.rstrip("/")+"/api/pull", json={"model": model}) as response:
                response.raise_for_status()
                last_emit = 0.0
                for line in response.iter_lines():
                    event = json.loads(line)
                    if event.get("error"):
                        raise RuntimeError(event["error"])
                    total, done = event.get("total", 0), event.get("completed", 0)
                    now = time.monotonic()
                    if total and done != total and now-last_emit < 0.15:
                        continue
                    last_emit = now
                    emit("status", text=f"Qwen · {done/1e9:.2f} / {total/1e9:.2f} ГБ" if total else "Qwen · подготовка модели…",
                         progress=done/total if total else 0)
    if "completion" not in client.show(model).get("capabilities", []):
        raise RuntimeError("Выбранная Qwen не поддерживает текстовый анализ")
    emit("status", text="Проверка реального ответа Qwen…")
    with httpx.Client(timeout=httpx.Timeout(300, connect=20), trust_env=False) as connection:
        response = connection.post(url.rstrip("/")+"/api/chat", json={"model": model, "stream": False,
            "think": False, "keep_alive": "1m", "messages": [{"role": "user", "content": "Ответь одним словом: готово"}],
            "options": {"num_ctx": 8192, "num_predict": 16, "num_thread": int(settings.get("threads", 4))}})
        response.raise_for_status()
        if not response.json().get("message", {}).get("content"):
            raise RuntimeError("Qwen не вернула ответ")
        response = connection.get(url.rstrip("/")+"/api/ps")
        response.raise_for_status()
        vram = sum(m.get("size_vram", 0) for m in response.json().get("models", []) if m.get("name") == model)
    client.release(model)
    result = {"whisper_device": device, "qwen_vram_bytes": vram, "cuda_devices": info["cuda_devices"]}
    emit("done", text=f"Готово. Whisper: {'NVIDIA CUDA' if device == 'cuda' else 'CPU'}. "
         f"Qwen: {'GPU' if vram else 'CPU'}. Можно запускать анализ.", result=result)
    return result
