"""Image overlay data model."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImageOverlay:
    """Describes an image to be rendered on top of the video.

    All position/size values are fractions of the video dimensions (0.0–1.0),
    so they scale correctly regardless of the output resolution.
    """

    image_path: str
    x_pct: float = 0.05   # left edge as fraction of video width
    y_pct: float = 0.05   # top  edge as fraction of video height
    w_pct: float = 0.20   # width  as fraction of video width
    h_pct: float = 0.15   # height as fraction of video height
