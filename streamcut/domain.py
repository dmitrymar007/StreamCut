from __future__ import annotations

import hashlib
import math
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def uid() -> str:
    return uuid4().hex


def timecode(seconds: float) -> str:
    ms = round(max(0, seconds) * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def parse_time(text: str) -> float:
    parts = text.strip().replace(",", ".").split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Таймкод: ЧЧ:ММ:СС.мс")
    values = [float(p) for p in parts]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Таймкод должен быть положительным")
    result = 0.0
    for value in values:
        result = result * 60 + value
    return result


class Span(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Конец должен быть позже начала")
        return self

    @property
    def duration(self):
        return self.end - self.start


class Segment(Span):
    id: str = Field(default_factory=uid)
    text: str
    words: list[dict] = Field(default_factory=list)
    approximate: bool = True
    edited: bool = False


class Clip(BaseModel):
    id: str = Field(default_factory=uid)
    title: str = "Новый момент"
    spans: list[Span] = Field(min_length=1)
    reason: str = "Добавлен вручную"
    evidence: list[str] = Field(default_factory=list)
    quotes: list[dict] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: ["вручную"])
    score: float | None = None
    status: str = "новый"
    note: str = ""
    edited: bool = False

    @model_validator(mode="after")
    def ordered_spans(self):
        if any(a.end > b.start for a, b in zip(self.spans, self.spans[1:])):
            raise ValueError("Фрагменты должны идти по времени и не пересекаться")
        return self

    @property
    def start(self):
        return self.spans[0].start

    @property
    def end(self):
        return self.spans[-1].end

    @property
    def duration(self):
        return sum(s.duration for s in self.spans)


class ExportOptions(BaseModel):
    mode: str = "точный"
    layout: str = "исходный"
    crf: int = Field(default=20, ge=15, le=35)
    height: int = 1080
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    volume: float = Field(default=1, ge=0, le=3)
    fade: float = Field(default=0.1, ge=0, le=2)
    subtitles: bool = False
    sidecar: bool = True
    webcam_x: float = Field(default=0, ge=0, le=1)
    webcam_y: float = Field(default=0, ge=0, le=1)
    webcam_w: float = Field(default=0.3, ge=0.05, le=1)
    webcam_h: float = Field(default=0.3, ge=0.05, le=1)


class ExportJob(BaseModel):
    id: str = Field(default_factory=uid)
    clip: Clip
    source: str
    fingerprint: str
    audio: int = 0
    options: ExportOptions
    segments: list[Segment] = Field(default_factory=list)
    destination: str
    state: str = "ожидает"
    progress: float = 0
    error: str = ""


class Project(BaseModel):
    schema_version: int = 1
    id: str = Field(default_factory=uid)
    title: str = "Новый проект"
    source: str = ""
    fingerprint: str = ""
    source_start: float = 0
    duration: float = Field(default=0, ge=0, allow_inf_nan=False)
    fps: float = Field(default=25, gt=0, allow_inf_nan=False)
    width: int = 1920
    height: int = 1080
    audio_tracks: list[dict] = Field(default_factory=list)
    audio: int = 0
    clips: list[Clip] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    checkpoints: dict[str, list[str]] = Field(default_factory=dict)
    coverage: list[Span] = Field(default_factory=list)
    visual_evidence: list[dict] = Field(default_factory=list)
    jobs: list[ExportJob] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)


def fingerprint(path: str | Path) -> str:
    path = Path(path)
    stat = path.stat()
    digest = hashlib.sha256(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    with path.open("rb") as f:
        digest.update(f.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            f.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(f.read(1024 * 1024))
    return digest.hexdigest()


def merge_candidate(clips: list[Clip], incoming: Clip) -> Clip:
    # Only merge strong temporal overlap AND a shared evidence identifier.
    for current in clips:
        overlap = max(0, min(current.end, incoming.end) - max(current.start, incoming.start))
        union = max(current.end, incoming.end) - min(current.start, incoming.start)
        if overlap / max(union, 0.001) > 0.7 and set(current.evidence) & set(incoming.evidence):
            current.evidence = list(dict.fromkeys(current.evidence + incoming.evidence))
            current.sources = list(dict.fromkeys(current.sources + incoming.sources))
            existing_quotes = {(q.get("id"), q.get("text")) for q in current.quotes}
            current.quotes.extend(q for q in incoming.quotes if (q.get("id"), q.get("text")) not in existing_quotes)
            if incoming.reason not in current.reason:
                current.reason += "\n" + incoming.reason
            return current
    clips.append(incoming)
    return incoming


def remove_range(spans: list[Span], cut: Span) -> list[Span]:
    result = []
    for span in spans:
        if cut.end <= span.start or cut.start >= span.end:
            result.append(span.model_copy())
            continue
        if span.start < cut.start:
            result.append(Span(start=span.start, end=cut.start))
        if span.end > cut.end:
            result.append(Span(start=cut.end, end=span.end))
    if not result:
        raise ValueError("Нельзя удалить все фрагменты клипа")
    return result
