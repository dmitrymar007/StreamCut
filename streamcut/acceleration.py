"""Select and report the actual Whisper device; auto mode can recover on CPU."""
import gc
import os
from pathlib import Path

from .paths import cuda_folder

_dll_handles = []
_cuda_libraries = []
CUDA_DLLS = ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn_graph64_9.dll", "cudnn64_9.dll",
             "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll", "cudnn_adv64_9.dll",
             "cudnn_engines_precompiled64_9.dll", "cudnn_engines_runtime_compiled64_9.dll", "cudnn_heuristic64_9.dll")


def configure_cuda():
    folder = cuda_folder()
    if os.name == "nt" and folder.is_dir() and not _dll_handles:
        _dll_handles.append(os.add_dll_directory(str(folder)))
        os.environ["PATH"] = str(folder)+os.pathsep+os.environ.get("PATH", "")


def gpu_error(error):
    text = str(error).lower()
    return any(word in text for word in ("cuda", "cudnn", "cublas", "gpu", "out of memory", "dll",
                                         "no kernel image", "compute type", "nvidia", "driver version"))


def require_cuda_runtime():
    if os.name != "nt" or _cuda_libraries:
        return
    import ctypes
    # cuDNN's small dispatcher can abort the process when its graph DLL is
    # missing. Check/load dependencies explicitly before starting inference.
    folders = [cuda_folder()]+[Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    loaded = []
    for name in CUDA_DLLS:
        path = next((folder/name for folder in folders if (folder/name).is_file()), None)
        if path is None:
            raise RuntimeError(f"CUDA: отсутствует {name}. Откройте «Подготовка нейросетей»")
        try:
            loaded.append(ctypes.WinDLL(str(path)))
        except OSError as error:
            raise RuntimeError(f"CUDA: не удалось загрузить {name}: {error}") from error
    _cuda_libraries.extend(loaded)


class WhisperEngine:
    def __init__(self, settings, emit=lambda *args, **kwargs: None, model_factory=None, backend=None):
        configure_cuda()
        self.native_factory = model_factory is None
        if backend is None:
            import ctranslate2 as backend
        if model_factory is None:
            from faster_whisper import WhisperModel
            model_factory = WhisperModel
        self.settings, self.emit = settings, emit
        self.factory, self.backend = model_factory, backend
        self.mode = settings.get("whisper_device", "auto")
        if self.mode not in ("auto", "cpu", "cuda"):
            raise ValueError("Неизвестное устройство Whisper")
        self.model = None
        self.device = "cpu"
        try:
            count = backend.get_cuda_device_count() if self.mode != "cpu" else 0
            if self.mode == "cuda" and not count:
                raise RuntimeError("NVIDIA CUDA недоступна. Проверьте драйвер или выберите «Авто / CPU»")
            self.device = "cuda" if count else "cpu"
            self._load()
        except (RuntimeError, OSError) as error:
            if self.mode != "auto" or not gpu_error(error):
                raise
            self._fallback(error)

    def _load(self):
        compute = "int8"
        if self.device == "cuda":
            if self.native_factory:
                require_cuda_runtime()
            types = self.backend.get_supported_compute_types("cuda")
            compute = next((t for t in ("float16", "int8_float16", "float32") if t in types), "float32")
        self.emit("status", text=f"Whisper · {'NVIDIA CUDA' if self.device == 'cuda' else 'CPU'} · {compute}")
        self.model = self.factory(self.settings.get("whisper_model", "small"), device=self.device,
            compute_type=compute, cpu_threads=int(self.settings.get("threads", 4)),
            download_root=self.settings["models_dir"], local_files_only=True)
        self.emit("hardware", device=self.device, compute_type=compute,
                  text=f"Распознавание: {'NVIDIA CUDA' if self.device == 'cuda' else 'CPU'} · {compute}")

    def _fallback(self, error):
        self.model = None
        gc.collect()
        self.device = "cpu"
        self.emit("warning", text=f"Ускорение NVIDIA недоступно ({error}). Продолжаем на CPU; результаты сохранены.")
        self._load()

    def transcribe(self, audio, **options):
        # Inference is lazy. Materialize the entire block before emitting it so
        # a GPU failure halfway through cannot save duplicated partial results.
        try:
            segments, _ = self.model.transcribe(audio, **options)
            return list(segments)
        except (RuntimeError, OSError) as error:
            if self.device != "cuda" or self.mode != "auto" or not gpu_error(error):
                raise
            self._fallback(error)
            segments, _ = self.model.transcribe(audio, **options)
            return list(segments)

    def close(self):
        self.model = None
        gc.collect()


def hardware_info():
    configure_cuda()
    import ctranslate2
    count = 0
    try:
        count = ctranslate2.get_cuda_device_count()
        return {"cuda_devices": count, "compute_types": [], "cuda_libraries": (cuda_folder()/"cudnn64_9.dll").is_file()}
    except (RuntimeError, OSError) as error:
        return {"cuda_devices": count, "compute_types": [], "error": str(error), "cuda_libraries": False}
