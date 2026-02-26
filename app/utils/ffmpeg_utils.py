"""FFmpeg-based audio mixing and export helpers."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from typing import List

import ffmpeg

from app.models.caption import Caption

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Probe helpers
# --------------------------------------------------------------------------- #

def get_video_duration(video_path: str) -> float:
    """Return duration of a media file in seconds."""
    info = ffmpeg.probe(video_path)
    return float(info["format"].get("duration", 0))


def get_video_info(video_path: str) -> dict:
    """Return basic metadata dict: duration, width, height, fps."""
    info  = ffmpeg.probe(video_path)
    vstream = next(
        (s for s in info["streams"] if s["codec_type"] == "video"), {}
    )
    duration = float(info["format"].get("duration", 0))
    width    = vstream.get("width", 0)
    height   = vstream.get("height", 0)

    raw_fps = vstream.get("r_frame_rate", "25/1")
    try:
        num, den = raw_fps.split("/")
        fps = float(num) / float(den)
    except Exception:
        fps = 25.0

    return {"duration": duration, "width": width, "height": height, "fps": fps}


# --------------------------------------------------------------------------- #
#  GPU / codec detection
# --------------------------------------------------------------------------- #

def _detect_nvenc() -> bool:
    """Return True if the installed ffmpeg supports h264_nvenc (NVIDIA GPU)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


# Cache the result so we only probe once per process lifetime.
_NVENC_AVAILABLE: bool | None = None


def _nvenc_available() -> bool:
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is None:
        _NVENC_AVAILABLE = _detect_nvenc()
    return _NVENC_AVAILABLE


# --------------------------------------------------------------------------- #
#  Export
# --------------------------------------------------------------------------- #

def _build_atempo_chain(audio_node, speed: float):
    """
    Chain atempo filters because a single atempo only supports 0.5 – 2.0.
    E.g. speed=3.0 → atempo(2.0) + atempo(1.5).
    """
    if speed == 1.0:
        return audio_node

    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        filters.append(0.5)
        remaining /= 0.5
    filters.append(remaining)

    node = audio_node
    for f in filters:
        node = node.filter("atempo", f)
    return node


def _pre_mix_tts_audio(
    captions: List[Caption],
    duration: float,
    tmp_path: str,
) -> bool:
    """
    Pre-mix all TTS clips into a single PCM WAV file at *tmp_path*.

    Doing this in a separate fast pass reduces the main export command from
    having N+1 amix inputs (one per caption) down to just 2 inputs, which
    dramatically speeds up the final render.

    Returns True if at least one TTS clip was processed, False otherwise.
    """
    audio_streams = []

    for cap in captions:
        if not cap.tts_audio_path or not os.path.exists(cap.tts_audio_path):
            continue

        tts_node = ffmpeg.input(cap.tts_audio_path).audio
        tts_node = _build_atempo_chain(tts_node, cap.speed)

        delay_ms = int(cap.effective_start * 1000)
        if delay_ms > 0:
            tts_node = tts_node.filter("adelay", f"{delay_ms}|{delay_ms}")

        # apad so all clips share the same timeline before amix
        tts_node = tts_node.filter("apad")
        audio_streams.append(tts_node)

    if not audio_streams:
        return False

    if len(audio_streams) == 1:
        combined = audio_streams[0]
    else:
        combined = ffmpeg.filter(
            audio_streams,
            "amix",
            inputs=len(audio_streams),
            duration="longest",
            normalize=0,
        )

    # Trim to video length so the intermediate file doesn't balloon
    combined = combined.filter("atrim", duration=duration)

    out_node = (
        ffmpeg
        .output(combined, tmp_path, acodec="pcm_s16le", ar=44100, threads=0)
        .overwrite_output()
    )
    log.debug("[ffmpeg pre-mix] command: %s", " ".join(ffmpeg.compile(out_node)))

    process = out_node.run_async(pipe_stderr=True)
    for line in process.stderr:
        text = line.decode("utf-8", errors="ignore").rstrip()
        if text:
            log.debug("[ffmpeg pre-mix] %s", text)
    process.wait()
    if process.returncode and process.returncode != 0:
        raise RuntimeError(f"ffmpeg pre-mix exited with code {process.returncode}")
    return True


def export_video(
    video_path: str,
    captions: List[Caption],
    output_video_path: str,
    original_volume: float = 0.3,
    mute_during_captions: bool = False,
    progress_callback=None,
) -> None:
    """
    Render the final video:
      - lower original audio to *original_volume* (0.0 – 1.0)
      - if mute_during_captions=True: mute original audio during each TTS segment
      - overlay each caption's TTS audio at its effective start time
      - encode with h264_nvenc (CUDA) when available, else libx264 / aac

    Optimisations vs the naive approach
    ------------------------------------
    * **Two-pass audio**: all TTS clips are pre-mixed into a single PCM WAV in
      a fast first pass, so the main ffmpeg command only ever has 2 amix inputs
      instead of one per caption.  This is the single biggest speed-up.
    * **CUDA hardware encoding**: uses h264_nvenc if the installed ffmpeg
      supports it, with a balanced quality/speed preset.  Falls back to
      libx264 fast + CRF 23.
    * **Hardware decoding**: passes ``hwaccel=cuda`` when NVENC is available
      so the GPU also handles decoding.
    * **Threading**: ``threads=0`` lets ffmpeg pick the optimal thread count.
    * **Fast-start**: ``movflags=+faststart`` moves the MP4 index to the front
      so the file is usable before it finishes writing.

    Args:
        video_path:             source video.
        captions:               list of Caption objects with tts_audio_path set.
        output_video_path:      destination .mp4 path.
        original_volume:        volume multiplier for the original audio track.
        mute_during_captions:   if True, silence orig audio during dubbed segments.
        progress_callback:      optional callable(int 0-100) for progress.
    """
    duration  = get_video_duration(video_path)
    use_nvenc = _nvenc_available()

    # ------------------------------------------------------------------ #
    #  Pass 1: pre-mix all TTS clips → temp WAV                           #
    # ------------------------------------------------------------------ #
    tmp_tts_path: str | None = None
    has_tts = any(
        cap.tts_audio_path and os.path.exists(cap.tts_audio_path)
        for cap in captions
    )

    if has_tts:
        tmp_fd, tmp_tts_path = tempfile.mkstemp(suffix="_tts_mix.wav")
        os.close(tmp_fd)
        if progress_callback:
            progress_callback(10)
        try:
            _pre_mix_tts_audio(captions, duration, tmp_tts_path)
        except Exception as exc:
            log.error("[ffmpeg pre-mix] failed: %s — continuing without TTS overlay", exc)
            # If pre-mix fails, fall back gracefully (no TTS overlay)
            tmp_tts_path = None
        if progress_callback:
            progress_callback(30)

    # ------------------------------------------------------------------ #
    #  Pass 2: mux video + mixed audio → output                           #
    # ------------------------------------------------------------------ #

    # Input — enable CUDA hw decode when NVENC is available
    src_kwargs: dict = {}
    if use_nvenc:
        src_kwargs["hwaccel"] = "cuda"

    src          = ffmpeg.input(video_path, **src_kwargs)
    video_stream = src.video
    orig_audio   = src.audio.filter("volume", original_volume)

    # Optionally mute original audio under TTS segments
    if mute_during_captions and has_tts:
        for cap in captions:
            if cap.tts_audio_path and os.path.exists(cap.tts_audio_path):
                s = cap.effective_start
                e = cap.end
                orig_audio = orig_audio.filter(
                    "volume", 0.0,
                    enable=f"between(t,{s},{e})",
                )

    # Final audio mix — always just 2 inputs now
    if tmp_tts_path and os.path.exists(tmp_tts_path):
        tts_mixed = ffmpeg.input(tmp_tts_path).audio
        mixed_audio = ffmpeg.filter(
            [orig_audio, tts_mixed],
            "amix",
            inputs=2,
            duration="longest",
            normalize=0,
        )
    else:
        mixed_audio = orig_audio

    # Codec selection
    if use_nvenc:
        # NVIDIA GPU H.264 — p4 = balanced speed/quality, cq=23 ≈ CRF 23
        video_codec_kwargs: dict = {
            "vcodec":  "h264_nvenc",
            "preset":  "p4",
            "rc":      "vbr",
            "cq":      23,
            "b:v":     "0",
            "profile:v": "high",
        }
    else:
        # CPU fallback — fast preset + CRF 23 gives good quality/speed balance
        video_codec_kwargs = {
            "vcodec": "libx264",
            "preset": "fast",
            "crf":    23,
            "profile:v": "high",
        }

    out = ffmpeg.output(
        video_stream,
        mixed_audio,
        output_video_path,
        **video_codec_kwargs,
        acodec="aac",
        audio_bitrate="192k",
        movflags="+faststart",
        threads=0,
    ).overwrite_output()

    log.info(
        "[ffmpeg export] codec=%s  nvenc=%s  output=%s",
        video_codec_kwargs.get("vcodec"), use_nvenc, output_video_path,
    )
    log.debug("[ffmpeg export] command: %s", " ".join(ffmpeg.compile(out)))

    # ------------------------------------------------------------------ #
    #  Run with progress tracking via stderr                              #
    # ------------------------------------------------------------------ #
    try:
        process = out.run_async(pipe_stderr=True)
        # Weight this phase as 30–100 % of total
        base_pct = 30 if has_tts else 5
        span_pct = 100 - base_pct
        stderr_lines: list[str] = []
        for line in process.stderr:
            text = line.decode("utf-8", errors="ignore").rstrip()
            if text:
                log.debug("[ffmpeg export] %s", text)
                stderr_lines.append(text)
            if progress_callback:
                m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", text)
                if m and duration:
                    elapsed = (
                        int(m.group(1)) * 3600
                        + int(m.group(2)) * 60
                        + float(m.group(3))
                    )
                    pct = base_pct + int(min(elapsed / duration, 1.0) * span_pct * 0.99)
                    progress_callback(pct)
        process.wait()
        if process.returncode and process.returncode != 0:
            # Surface the last few stderr lines so the error message is useful
            tail = "\n".join(stderr_lines[-20:])
            raise RuntimeError(
                f"ffmpeg exited with code {process.returncode}\n{tail}"
            )
        if progress_callback:
            progress_callback(100)
        log.info("[ffmpeg export] finished → %s", output_video_path)
    finally:
        # Clean up temporary TTS mix file
        if tmp_tts_path and os.path.exists(tmp_tts_path):
            try:
                os.remove(tmp_tts_path)
            except OSError:
                pass
