import os
from types import SimpleNamespace

import pytest

from streamcut.acceleration import WhisperEngine, require_cuda_runtime


class Backend:
    def __init__(self, count):
        self.count = count

    def get_cuda_device_count(self):
        return self.count

    def get_supported_compute_types(self, device):
        assert device == "cuda"
        return {"float16", "float32", "int8_float16"}


def settings(mode="auto"):
    return {"whisper_device": mode, "whisper_model": "small", "models_dir": "models", "threads": 4}


def test_cpu_machine_needs_no_cuda():
    calls = []
    engine = WhisperEngine(settings(), model_factory=lambda *a, **kw: calls.append(kw), backend=Backend(0))
    assert engine.device == "cpu"
    assert calls[0]["device"] == "cpu" and calls[0]["compute_type"] == "int8"
    assert calls[0]["local_files_only"]


def test_cuda_uses_gpu_and_reports_actual_device():
    calls, events = [], []
    engine = WhisperEngine(settings(), lambda kind, **data: events.append((kind, data)),
                           model_factory=lambda *a, **kw: calls.append(kw), backend=Backend(1))
    assert engine.device == "cuda" and calls[0]["compute_type"] == "float16"
    assert any(k == "hardware" and v["device"] == "cuda" for k, v in events)


def test_auto_falls_back_when_cuda_libraries_fail():
    calls, events = [], []
    def load(*args, **kwargs):
        calls.append(kwargs["device"])
        if kwargs["device"] == "cuda":
            raise RuntimeError("Could not load cudnn64_9.dll")
        return object()
    engine = WhisperEngine(settings(), lambda kind, **data: events.append(kind), load, Backend(1))
    assert calls == ["cuda", "cpu"] and engine.device == "cpu" and "warning" in events


def test_lazy_gpu_failure_retries_whole_block_without_partial_results():
    def broken():
        yield "partial GPU result must not be emitted"
        raise RuntimeError("CUDA out of memory")
    def load(*args, **kwargs):
        return SimpleNamespace(transcribe=lambda *a, **kw: (
            broken() if kwargs["device"] == "cuda" else iter(["complete CPU result"]), None))
    engine = WhisperEngine(settings(), model_factory=load, backend=Backend(1))
    assert engine.transcribe("audio.wav") == ["complete CPU result"]
    assert engine.device == "cpu"


def test_forced_cuda_does_not_silently_fall_back():
    with pytest.raises(RuntimeError, match="недоступна"):
        WhisperEngine(settings("cuda"), model_factory=lambda *a, **kw: None, backend=Backend(0))
    def load(*a, **kw):
        raise RuntimeError("CUDA out of memory")
    with pytest.raises(RuntimeError, match="out of memory"):
        WhisperEngine(settings("cuda"), model_factory=load, backend=Backend(1))


def test_cpu_selection_never_probes_gpu_and_missing_weights_are_not_hidden():
    class NoProbe:
        def get_cuda_device_count(self):
            raise AssertionError("CPU must not probe CUDA")
    engine = WhisperEngine(settings("cpu"), model_factory=lambda *a, **kw: object(), backend=NoProbe())
    assert engine.device == "cpu"
    def load(*a, **kw):
        raise OSError("missing model.bin")
    with pytest.raises(OSError, match="model.bin"):
        WhisperEngine(settings(), model_factory=load, backend=Backend(1))


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL loading")
def test_missing_cuda_dependencies_fail_before_native_cudnn_abort(tmp_path, monkeypatch):
    import streamcut.acceleration as module
    monkeypatch.setattr(module, "cuda_folder", lambda: tmp_path)
    monkeypatch.setattr(module, "_cuda_libraries", [])
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError, match="CUDA.*cublas"):
        require_cuda_runtime()
