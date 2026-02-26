"""SRT subtitle file read/write utilities."""
from __future__ import annotations

import re
from typing import List

from app.models.caption import Caption


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _ts_to_seconds(ts: str) -> float:
    """Parse an SRT timestamp (HH:MM:SS,mmm) into seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s


def seconds_to_ts(seconds: float) -> str:
    """Convert seconds to SRT timestamp HH:MM:SS,mmm."""
    assert seconds >= 0
    ms = round(seconds * 1000)
    h  = ms // 3_600_000;  ms -= h * 3_600_000
    m  = ms //    60_000;  ms -= m *    60_000
    s  = ms //     1_000;  ms -= s *     1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# --------------------------------------------------------------------------- #
#  Parse
# --------------------------------------------------------------------------- #

_BLOCK_RE = re.compile(
    r"(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"
    r"([\s\S]*?)(?=\n\n|\Z)",
    re.MULTILINE,
)


def parse_srt(path: str) -> List[Caption]:
    """Read an SRT file and return a list of Caption objects."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        content = fh.read()

    captions: List[Caption] = []
    for m in _BLOCK_RE.finditer(content):
        idx   = int(m.group(1))
        start = _ts_to_seconds(m.group(2))
        end   = _ts_to_seconds(m.group(3))
        text  = m.group(4).strip()
        captions.append(Caption(index=idx, start=start, end=end, original_text=text))

    return captions


# --------------------------------------------------------------------------- #
#  Write
# --------------------------------------------------------------------------- #

def write_srt(captions: List[Caption], path: str, use_khmer: bool = True) -> None:
    """Write captions to an SRT file.

    Args:
        captions:  list of Caption objects.
        path:      output file path.
        use_khmer: if True, write ``khmer_text`` (falls back to ``original_text``
                   when the Khmer text is empty).
    """
    with open(path, "w", encoding="utf-8") as fh:
        for i, cap in enumerate(captions, start=1):
            text = (cap.khmer_text or cap.original_text) if use_khmer else cap.original_text
            fh.write(
                f"{i}\n"
                f"{seconds_to_ts(cap.start)} --> {seconds_to_ts(cap.end)}\n"
                f"{text}\n\n"
            )
