import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from streamcut.captions import read_captions
from streamcut.domain import (
    Clip,
    ExportJob,
    ExportOptions,
    Project,
    Segment,
    Span,
    fingerprint,
    merge_candidate,
    parse_time,
    timecode,
)
from streamcut.media import executable, export_clip, probe, run, subtitles
from streamcut.ollama import Ollama
from streamcut.storage import ProjectStore


def test_time_coordinates():
    for stamp in [0, 0.001, 59.999, 3600.5, 23737.077]:
        assert abs(parse_time(timecode(stamp))-stamp) < 0.0006
    for bad in ["nan", "inf", "-1", "1:2:3:4"]:
        with pytest.raises(ValueError):
            parse_time(bad)
    with pytest.raises(ValidationError):
        Span(start=10, end=9)


def test_project_atomic_recovery_and_queue_snapshot(tmp_path):
    clip = Clip(spans=[Span(start=10, end=40)])
    job = ExportJob(clip=clip.model_copy(deep=True), source="source.mp4", fingerprint="abc",
                    options=ExportOptions(), destination="out.mp4", state="выполняется")
    project = Project(clips=[clip], jobs=[job])
    clip.spans[0].start = 12
    assert job.clip.start == 10
    store = ProjectStore(tmp_path / "Проект с пробелами")
    store.save(project)
    loaded = store.load()
    assert loaded.clips[0].start == 12
    assert loaded.jobs[0].clip.start == 10
    assert loaded.jobs[0].state == "ожидает"
    loaded.clips[0].note = "Правка"
    store.save(loaded)
    assert store.load().clips[0].note == "Правка"


def test_merge_preserves_edits_and_distinct_events():
    original = Clip(spans=[Span(start=10, end=30)], evidence=["s1"], edited=True, status="оставлен")
    incoming = Clip(spans=[Span(start=11, end=31)], evidence=["s1", "s2"], sources=["речь"])
    clips = [original]
    merge_candidate(clips, incoming)
    assert len(clips) == 1 and original.start == 10 and original.status == "оставлен"
    assert original.evidence == ["s1", "s2"]
    merge_candidate(clips, Clip(spans=[Span(start=11, end=31)], evidence=["different"]))
    assert len(clips) == 2


def test_captions_clipped_to_selection(tmp_path):
    path = tmp_path / "Русский текст.srt"
    path.write_text("1\n00:00:09,500 --> 00:00:12,000\nПривет мир\n\n2\n00:00:19,000 --> 00:00:22,000\nКонец\n", encoding="utf-8")
    segments = read_captions(path)
    result = subtitles(segments, 10, 20)
    assert "00:00:00,000 --> 00:00:02,000" in result
    assert "00:00:09,000 --> 00:00:10,000" in result
    assert "Привет мир" in result


@pytest.fixture(scope="module")
def fixture_video(tmp_path_factory):
    folder = tmp_path_factory.mktemp("media") / "Папка с пробелами"
    folder.mkdir()
    path = folder / "Исходник.mp4"
    run([executable("ffmpeg"), "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)])
    return path


@pytest.mark.parametrize("layout", ["исходный", "9:16", "1:1", "размытый фон"])
def test_real_export(fixture_video, tmp_path, layout):
    output = tmp_path / f"Клип {layout.replace(':','-')}.mp4"
    job = ExportJob(clip=Clip(spans=[Span(start=1.24, end=3.64)]), source=str(fixture_video),
                    fingerprint=fingerprint(fixture_video), destination=str(output),
                    options=ExportOptions(layout=layout, height=480),
                    segments=[Segment(start=1.3, end=3.3, text="Русская речь")])
    export_clip(job, threading.Event(), lambda p: None)
    meta = probe(str(output))
    assert abs(meta["duration"]-2.4) < 0.12
    assert output.with_suffix(".srt").exists()
    if layout == "9:16":
        assert meta["width"] == 270 and meta["height"] == 480
    with pytest.raises(FileExistsError):
        export_clip(job, threading.Event(), lambda p: None)
    streams = json.loads(run([executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(output)]))["streams"]
    a, v = next(s for s in streams if s["codec_type"] == "audio"), next(s for s in streams if s["codec_type"] == "video")
    assert abs(float(a["duration"])-float(v["duration"])) < 0.12


def test_cancel_and_changed_source(fixture_video, tmp_path):
    job = ExportJob(clip=Clip(spans=[Span(start=0, end=6)]), source=str(fixture_video),
                    fingerprint="wrong", destination=str(tmp_path / "result.mp4"), options=ExportOptions())
    with pytest.raises(ValueError, match="изменился"):
        export_clip(job, threading.Event(), lambda p: None)
    job.fingerprint = fingerprint(fixture_video)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        export_clip(job, cancel, lambda p: None)
    assert not Path(job.destination).exists()
    assert not list(tmp_path.glob("*.partial.mp4"))


def test_ollama_invalid_ids_retry(monkeypatch):
    import httpx
    count = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            count.append(1)
            return {"message": {"content": json.dumps({"moments": [{"first_id": "fake", "last_id": "fake",
                     "title": "bad", "reason": "bad", "score": 50}]})}}
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, *args, **kwargs):
            return Response()
    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(ValueError, match="после повтора"):
        Ollama().search("model", [{"id": "valid", "text": "hello"}], "criteria")
    assert len(count) == 2
