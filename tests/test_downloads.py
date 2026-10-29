import hashlib
import zipfile

import httpx
import pytest

import streamcut.downloads as module
from streamcut.downloads import download, extract_cuda


def asset(data):
    return {"url": "https://example.test/binary", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def connection(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))


def test_resume_verified_download_and_reuse_without_network(tmp_path, monkeypatch):
    payload = b"verified binary contents"
    target = tmp_path/"setup.exe"
    target.with_name(target.name+".part").write_bytes(payload[:8])
    def serve(request):
        assert request.headers["range"] == "bytes=8-"
        return httpx.Response(206, headers={"Content-Range": f"bytes 8-{len(payload)-1}/{len(payload)}"}, content=payload[8:])
    connection(monkeypatch, serve)
    assert download(asset(payload), target).read_bytes() == payload
    connection(monkeypatch, lambda request: (_ for _ in ()).throw(AssertionError("cache hit must not connect")))
    assert download(asset(payload), target) == target


def test_server_without_range_restarts_instead_of_appending(tmp_path, monkeypatch):
    payload = b"complete component"
    target = tmp_path/"wheel.whl"
    target.with_name(target.name+".part").write_bytes(b"old partial")
    connection(monkeypatch, lambda request: httpx.Response(200, content=payload))
    assert download(asset(payload), target).read_bytes() == payload


def test_bad_hash_never_publishes_or_executes_component(tmp_path, monkeypatch):
    target = tmp_path/"installer.exe"
    connection(monkeypatch, lambda request: httpx.Response(200, content=b"wrong"))
    with pytest.raises(ValueError, match="Контрольная сумма"):
        download(asset(b"right"), target)
    assert not target.exists() and not target.with_name(target.name+".part").exists()


def test_archive_cannot_write_outside_runtime(tmp_path):
    wheel = tmp_path/"cuda.whl"
    with zipfile.ZipFile(wheel, "w") as output:
        output.writestr("../../outside.dll", b"library")
        output.writestr("nvidia/cuda/bin/cublas64_12.dll", b"cuda")
        output.writestr("../../arbitrary.exe", b"must not extract")
        output.writestr("dist-info/LICENSE.txt", b"notice")
    folder = tmp_path/"runtime"
    extract_cuda(wheel, folder)
    assert (folder/"outside.dll").read_bytes() == b"library"
    assert not (tmp_path/"outside.dll").exists() and not (tmp_path/"arbitrary.exe").exists()
    assert (folder/"licenses"/"cuda"/"LICENSE.txt").exists()
