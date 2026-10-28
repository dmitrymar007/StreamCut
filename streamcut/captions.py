import re
from pathlib import Path

from .domain import Segment, parse_time


def read_captions(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    pattern = r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)"
    result = []
    for match in re.finditer(pattern, text, re.S):
        a, b = parse_time(match[1]), parse_time(match[2])
        value = re.sub(r"<[^>]+>", "", match[3]).strip()
        if b > a and value:
            result.append(Segment(start=a, end=b, text=value))
    if not result:
        raise ValueError("Не найдены реплики SRT/VTT с временными метками")
    return result
