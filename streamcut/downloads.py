"""Resumable HTTPS downloads, pinned hashes, atomic publication and safe extraction."""
import hashlib
import os
import shutil
import zipfile
from pathlib import Path

import httpx


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(asset, destination, progress=lambda done, total: None):
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and checksum(target) == asset["sha256"]:
        progress(target.stat().st_size, target.stat().st_size)
        return target
    size = int(asset["size"])
    if shutil.disk_usage(target.parent).free < size+64*1024**2:
        raise OSError("Недостаточно свободного места для загрузки компонента")
    partial = target.with_name(target.name+".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    if offset < size:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(90, connect=30), trust_env=False) as client:
            with client.stream("GET", asset["url"], headers=headers) as response:
                response.raise_for_status()
                if offset and response.status_code == 206:
                    if not response.headers.get("content-range", "").startswith(f"bytes {offset}-"):
                        raise ValueError("Сервер вернул некорректный диапазон загрузки")
                elif offset:
                    offset = 0
                with partial.open("ab" if offset else "wb") as output:
                    done = offset
                    for block in response.iter_bytes(1024*1024):
                        done += len(block)
                        if done > size:
                            raise ValueError("Размер загрузки отличается от закреплённого манифеста")
                        output.write(block)
                        progress(done, size)
    if partial.stat().st_size != size or checksum(partial) != asset["sha256"]:
        partial.unlink(missing_ok=True)
        raise ValueError("Контрольная сумма загрузки не совпала. Компонент не запущен; повторите установку")
    os.replace(partial, target)
    return target


def extract_cuda(wheel, destination):
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        files = [i for i in archive.infolist() if not i.is_dir() and (
            i.filename.lower().endswith(".dll") or "license" in Path(i.filename).name.lower())]
        if not any(i.filename.lower().endswith(".dll") for i in files):
            raise ValueError("В архиве NVIDIA нет Windows-библиотек")
        if sum(i.file_size for i in files)+64*1024**2 > shutil.disk_usage(folder).free:
            raise OSError("Недостаточно места для библиотек NVIDIA")
        for item in files:
            # Flatten only known binary/notices entries: no archive paths are
            # trusted or used as filesystem targets (including ../ and symlinks).
            name = Path(item.filename.replace("\\", "/")).name
            output = folder/name if name.lower().endswith(".dll") else folder/"licenses"/Path(wheel).stem/name
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name+".partial")
            with archive.open(item) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, 1024*1024)
            temporary.replace(output)
