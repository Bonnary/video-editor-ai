from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Caption:
    """Represents a single subtitle/caption segment."""

    index: int
    start: float        # seconds (from original transcription)
    end: float          # seconds
    original_text: str  # source-language text (Chinese)
    khmer_text: str = ""
    speed: float = 1.0          # TTS playback speed multiplier (0.5 – 2.0)
    offset: float = 0.0         # extra time offset in seconds (positive = later)
    tts_audio_path: Optional[str] = None  # path to generated .mp3 / .wav
    voice: str = ""             # Edge TTS voice name; "" = use worker default

    # ------------------------------------------------------------------ helpers
    @property
    def effective_start(self) -> float:
        """Actual playback start after offset adjustment."""
        return max(0.0, self.start + self.offset)

    @property
    def duration(self) -> float:
        return self.end - self.start
