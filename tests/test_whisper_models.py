import hashlib
import json

import httpx
import pytest

import streamcut.whisper_models as module
from streamcut.downloads import download


@pytest.fixture
def model_manifest(monkeypatch):
    payloads = {name: (name+" payload").encode() for name in
                ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")}
    manifest = {"revision": "pinned-revision", "files": {
        name: {"url": "https://example.test/"+name, "size": len(data),
               "sha256": hashlib.sha256(data).hexdigest()} for name, data in payloads.items()}}
    monkeypatch.setattr(module, "manifest_for", lambda name: manifest)
    return manifest, payloads


def test_partial_hf_snapshot_does_not_count_as_installed(tmp_path, monkeypatch, model_manifest):
    manifest, payloads = model_manifest
    old = tmp_path/"models--Systran--faster-whisper-small"/"snapshots"/manifest["revision"]
    old.mkdir(parents=True)
    # Regression: HF local-only lookup returns a snapshot even when model.bin
    # never finished downloading. Reuse good files and fetch the missing one.
    for name, data in payloads.items():
        if name != "model.bin":
            (old/name).write_bytes(data)
    requested = []
    def fetch(asset, target, progress, **kwargs):
        requested.append(target.name)
        target.write_bytes(payloads[target.name])
        progress(asset["size"], asset["size"])
        return target
    monkeypatch.setattr(module, "download", fetch)
    events = []
    folder = module.prepare_whisper("small", tmp_path, lambda kind, **data: events.append(data))
    assert "model.bin" in requested
    assert module.model_path("small", tmp_path) == folder
    assert json.loads((module.Path(folder)/"verified.json").read_text()) == manifest
    assert any(event.get("progress") == 1 for event in events)
    # Re-running setup works offline and does not download files again.
    monkeypatch.setattr(module, "download", lambda *a, **k: pytest.fail("must reuse completed model"))
    assert module.prepare_whisper("small", tmp_path, lambda *a, **k: None) == folder


def test_failure_preserves_partial_and_cannot_publish_model(tmp_path, monkeypatch, model_manifest):
    def fetch(asset, target, progress, **kwargs):
        target.with_name(target.name+".part").write_bytes(b"partial")
        raise httpx.ReadTimeout("connection stalled")
    monkeypatch.setattr(module, "download", fetch)
    with pytest.raises(RuntimeError, match="докачка сохранена"):
        module.prepare_whisper("small", tmp_path, lambda *a, **k: None)
    folder = tmp_path/"streamcut-whisper-small"
    assert list(folder.glob("*.part")) and not (folder/"verified.json").exists()
    assert module.model_path("small", tmp_path) == "small"


def test_interrupted_http_resumes_and_verifies_hash(tmp_path, monkeypatch):
    import streamcut.downloads as downloads

    payload = b"a"*(64*1024)+b"tail"
    requests = []
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[:64*1024]
            raise httpx.ReadError("connection interrupted")
    def serve(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, stream=Broken())
        assert request.headers["range"] == "bytes=65536-"
        return httpx.Response(206, headers={"Content-Range": "bytes 65536-65539/65540"}, content=b"tail")
    real_client = httpx.Client
    monkeypatch.setattr(downloads.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(serve)))
    monkeypatch.setattr(downloads.time, "sleep", lambda seconds: None)
    asset = {"url": "https://example.test/model.bin", "size": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest()}
    retries = []
    target = download(asset, tmp_path/"model.bin", retry_status=lambda attempt, error: retries.append(attempt))
    assert target.read_bytes() == payload and retries == [2]
    assert len(requests) == 2
