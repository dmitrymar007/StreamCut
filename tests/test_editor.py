import json
import sys
import threading
import time

import pytest

from streamcut.domain import (
    Clip,
    ExportJob,
    ExportOptions,
    Project,
    Span,
    candidate_was_deleted,
    fingerprint,
    output_dimensions,
)
from streamcut.editing import apply_edit
from streamcut.media import executable, export_clip, probe, run
from streamcut.preview_cache import PreviewCache, cancellable_run, preview_plan
from streamcut.storage import ProjectStore


def test_delete_undo_preserves_queue_and_files(tmp_path):
    clip = Clip(title="Ручная правка", spans=[Span(start=12, end=31)], evidence=["s1"], edited=True)
    exported = tmp_path/"Уже готовый клип.mp4"
    exported.write_bytes(b"user export must survive")
    job = ExportJob(clip=clip.model_copy(deep=True), source="input.mp4", fingerprint="x",
                    destination=str(exported), options=ExportOptions(), state="экспортирован")
    project = Project(clips=[clip], jobs=[job])
    inverse, _ = apply_edit(project, {"operation": "remove", "id": clip.id})
    assert not project.clips
    assert candidate_was_deleted(project, clip)
    assert len(project.jobs) == 1 and project.jobs[0].clip.start == 12
    assert exported.read_bytes() == b"user export must survive"
    store = ProjectStore(tmp_path/"project")
    store.save(project)
    project = store.load()
    assert candidate_was_deleted(project, clip)
    redo, restored = apply_edit(project, inverse)
    assert restored == clip.id and project.clips[0].model_dump() == clip.model_dump()
    assert not candidate_was_deleted(project, clip)
    apply_edit(project, redo)
    assert not project.clips and exported.exists()


def test_split_undo_is_one_operation():
    original = Clip(spans=[Span(start=0, end=20)])
    second = Clip(spans=[Span(start=10, end=20)])
    first = original.model_copy(deep=True)
    first.spans = [Span(start=0, end=10)]
    project = Project(clips=[first, second])
    redo, _ = apply_edit(project, {"operation": "batch", "items": [
        {"operation": "remove", "id": second.id}, original.model_dump()]})
    assert len(project.clips) == 1 and project.clips[0].end == 20
    apply_edit(project, redo)
    assert [(c.start, c.end) for c in project.clips] == [(0, 10), (10, 20)]


@pytest.mark.parametrize("source,layout,expected", [
    ((1920, 1080), "исходный", (1920, 1080)),
    ((3840, 2160), "исходный", (3840, 2160)),
    ((1280, 720), "исходный", (1280, 720)),
    ((1920, 1080), "9:16", (1080, 1920)),
    ((1280, 720), "9:16", (720, 1280)),
    ((1080, 1920), "9:16", (1080, 1920)),
])
def test_default_resolution(source, layout, expected):
    assert output_dimensions(ExportOptions(layout=layout), *source) == expected


@pytest.fixture(scope="module")
def hd_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("full-hd")/"Full HD источник.mp4"
    run([executable("ffmpeg"), "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=500:duration=2", "-c:v", "libx264", "-threads", "2",
         "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)])
    return path


def packet_hashes(path):
    data = json.loads(run([executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_packets",
        "-show_data_hash", "sha256", "-show_entries", "packet=data_hash", "-of", "json", str(path)]))
    return [p["data_hash"] for p in data["packets"]]


def test_original_copy_keeps_full_hd_and_encoded_packets(hd_video, tmp_path):
    target = tmp_path/"Половина без сжатия.mp4"
    job = ExportJob(clip=Clip(spans=[Span(start=0, end=1)]), source=str(hd_video),
                    fingerprint=fingerprint(hd_video), destination=str(target), options=ExportOptions(mode="быстрый"))
    export_clip(job, threading.Event(), lambda p: None)
    info = probe(str(target))
    assert (info["width"], info["height"]) == (1920, 1080)
    original, copied = packet_hashes(hd_video), packet_hashes(target)
    assert copied and copied == original[:len(copied)]


def test_default_shorts_full_hd(hd_video, tmp_path):
    target = tmp_path/"Shorts Full HD.mp4"
    job = ExportJob(clip=Clip(spans=[Span(start=0, end=1)]), source=str(hd_video),
                    fingerprint=fingerprint(hd_video), destination=str(target), options=ExportOptions(layout="9:16"))
    export_clip(job, threading.Event(), lambda p: None)
    info = probe(str(target))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"]-1) < 0.1


def test_preview_cache_reuses_frames_and_wave(hd_video, tmp_path, monkeypatch):
    import streamcut.preview_cache as module
    cache = PreviewCache(tmp_path, str(hd_video), "source-a", 0, 2)
    image = cache.frame(0.5, threading.Event())
    wave = cache.waveform(0, threading.Event())
    assert image.startswith(b"\xff\xd8") and wave and max(wave) > 0
    def no_decode(*args):
        raise AssertionError("Повторный просмотр не должен декодироваться заново")
    monkeypatch.setattr(module, "cancellable_run", no_decode)
    assert cache.frame(0.5, threading.Event()) == image
    assert cache.waveform(0, threading.Event()) == wave
    with pytest.raises(AssertionError):
        PreviewCache(tmp_path, str(hd_video), "source-b", 0, 2).frame(0.5, threading.Event())
    with pytest.raises(AssertionError):
        PreviewCache(tmp_path, str(hd_video), "source-a", 1, 2).waveform(0, threading.Event())


def test_obsolete_preview_process_cancels():
    cancel = threading.Event()
    timer = threading.Timer(0.15, cancel.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        cancellable_run([sys.executable, "-c", "import time; time.sleep(30)"], cancel)
    timer.join()
    assert time.monotonic()-started < 3


def test_preview_plan_covers_both_sides_without_whole_video_pcm():
    step, frames, chunks = preview_plan(1000, 1120, 21600, 1200, 1060)
    assert 0 < step <= 20
    assert len(frames) < 50
    assert min(frames) < 1000 and max(frames) > 1120
    assert all(0 <= c*30 < 21600 for c in chunks)
    assert abs(frames[0]-1060) <= step


def test_legacy_copy_queue_preserves_original_semantics(tmp_path):
    job = ExportJob(clip=Clip(spans=[Span(start=0, end=5)]), source="video.mp4", fingerprint="x",
                    destination="clip.mp4", options=ExportOptions(mode="быстрый", height=1080, fade=0.1))
    store = ProjectStore(tmp_path)
    store.save(Project(jobs=[job]))
    resumed = store.load().jobs[0]
    assert resumed.options.mode == "быстрый"
    assert resumed.options.height == 0 and resumed.options.fade == 0
