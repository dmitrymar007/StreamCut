"""Download local Windows media runtimes from upstream release assets."""
import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "StreamCut"}), timeout=90)


def install():
    VENDOR.mkdir(exist_ok=True)
    manifest = []
    releases = [
        ("BtbN/FFmpeg-Builds", lambda n: n == "ffmpeg-master-latest-win64-gpl.zip"),
        ("shinchiro/mpv-winbuild-cmake", lambda n: n.startswith("mpv-dev-x86_64-") and n.endswith(".7z")),
    ]
    for repo, predicate in releases:
        release = json.load(get(f"https://api.github.com/repos/{repo}/releases/latest"))
        asset = next(a for a in release["assets"] if predicate(a["name"]))
        archive = VENDOR / asset["name"]
        if not archive.exists():
            print("Download", asset["name"], asset["size"], flush=True)
            with get(asset["browser_download_url"]) as src, archive.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
        expected = asset.get("digest")
        if expected and expected != "sha256:" + digest:
            raise RuntimeError("Checksum mismatch: " + asset["name"])
        unpack = VENDOR / "unpack"
        unpack.mkdir(exist_ok=True)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(unpack)
        else:
            # Upstream mpv uses BCJ2, which Python's py7zr cannot decompress.
            seven = VENDOR / "7zr.exe"
            if not seven.exists():
                with get("https://www.7-zip.org/a/7zr.exe") as src, seven.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            subprocess.run([str(seven), "x", "-y", f"-o{unpack}", str(archive)], check=True,
                           stdout=subprocess.DEVNULL)
        for pattern in ("ffmpeg.exe", "ffprobe.exe", "libmpv-2.dll", "mpv-2.dll"):
            for file in unpack.rglob(pattern):
                shutil.copy2(file, VENDOR / file.name)
        # Keep upstream license material alongside the redistributable binaries.
        for file in unpack.rglob("*"):
            if file.is_file() and any(s in file.name.lower() for s in ("license", "copying")):
                shutil.copy2(file, VENDOR / (repo.split("/")[0] + "-" + file.name))
        manifest.append({"url": asset["browser_download_url"], "sha256": digest})
        print("Installed", repo, flush=True)
    (VENDOR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    install()
