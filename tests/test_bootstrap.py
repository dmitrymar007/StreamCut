import json
from types import SimpleNamespace

import httpx
import pytest

import streamcut.bootstrap as bootstrap


def test_start_reuses_running_user_ollama_without_spawning(monkeypatch):
    monkeypatch.setattr(bootstrap, "ollama_running", lambda url: True)
    monkeypatch.setattr(bootstrap, "external_process", lambda *a, **k: pytest.fail("must not spawn"))
    bootstrap.start_ollama("http://127.0.0.1:11434")


def test_remote_url_does_not_install_or_spawn_local_server(monkeypatch):
    monkeypatch.setattr(bootstrap, "ollama_running", lambda url: False)
    monkeypatch.setattr(bootstrap, "external_process", lambda *a, **k: pytest.fail("must not spawn"))
    with pytest.raises(RuntimeError, match="Удалённая"):
        bootstrap.start_ollama("https://remote.example")


def test_fresh_cpu_setup_installs_missing_ollama_and_models_without_cuda(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "data_root", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "ollama_running", lambda url: False)
    monkeypatch.setattr(bootstrap, "ollama_executable", lambda: None)
    downloaded, launches, loads = [], [], []
    def fetch(asset, target, progress):
        downloaded.append(asset)
        target.write_bytes(b"verified installer fixture")
        return target
    monkeypatch.setattr(bootstrap, "download", fetch)
    def launch(args, **kwargs):
        launches.append(args)
        return SimpleNamespace(wait=lambda **kw: 0)
    monkeypatch.setattr(bootstrap, "external_process", launch)
    monkeypatch.setattr(bootstrap, "start_ollama", lambda url: None)
    monkeypatch.setattr(bootstrap, "hardware_info", lambda: {"cuda_devices": 0})
    monkeypatch.setattr(bootstrap, "extract_cuda", lambda *a: pytest.fail("CPU must not download CUDA"))
    monkeypatch.setattr(bootstrap, "prepare_whisper", lambda *args: loads.append(args))
    monkeypatch.setattr(bootstrap, "WhisperEngine", lambda *a: SimpleNamespace(
        transcribe=lambda *a, **k: [], device="cpu", close=lambda: None))
    monkeypatch.setattr(bootstrap, "Ollama", lambda url: SimpleNamespace(models=lambda: [],
        show=lambda model: {"capabilities": ["completion", "vision"]}, release=lambda model: None))
    real_client = httpx.Client
    requests = []
    def server(request):
        requests.append(request.url.path)
        if request.url.path == "/api/pull":
            return httpx.Response(200, content=json.dumps({"status": "success"})+"\n")
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "готово"}})
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b", "size_vram": 0}]})
    monkeypatch.setattr(bootstrap.httpx, "Client", lambda **k: real_client(transport=httpx.MockTransport(server)))
    events = []
    result = bootstrap.prepare({"models_dir": str(tmp_path/"models"), "whisper_device": "auto"},
                               lambda kind, **data: events.append((kind, data)))
    assert len(downloaded) == 1 and downloaded[0]["sha256"]
    assert launches[0][1:] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    assert len(loads) == 1 and loads[0][:2] == ("small", str(tmp_path/"models"))
    assert "/api/pull" in requests and "/api/chat" in requests
    assert result["whisper_device"] == "cpu" and events[-1][0] == "done"
