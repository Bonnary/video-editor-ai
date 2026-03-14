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
#  Safe async runner (avoids WinError 206 on long filter graphs)
# --------------------------------------------------------------------------- #

# Windows CreateProcess limit is 32 767 chars.  Use a conservative threshold
# so there is always headroom for the rest of the command line.
_WIN_CMDLINE_THRESHOLD = 8_000


def _run_ffmpeg_node_async(
    node,
    pipe_stderr: bool = True,
) -> tuple["subprocess.Popen", "str | None"]:
    """
    Compile an ffmpeg-python node and launch it as an async subprocess.

    On Windows the ``CreateProcess`` API has a ~32 767-character command-line
    limit.  When a large filter graph (e.g. hundreds of chained volume filters)
    would exceed that limit the function writes the ``-filter_complex`` value
    to a temporary file and substitutes it with ``-filter_complex_script``,
    keeping the command line short.

    Returns
    -------
    (process, tmp_script_path)
        *tmp_script_path* is the path of the temp file when the script trick
        was used, or ``None`` otherwise.  The caller is responsible for
        deleting the file after the process finishes.
    """
    cmd = ffmpeg.compile(node)

    # Estimate the final command-line length using Windows quoting rules.
    cmd_len = len(subprocess.list2cmdline(cmd))
    tmp_script: str | None = None

    if cmd_len > _WIN_CMDLINE_THRESHOLD:
        log.debug(
            "[ffmpeg] command line is %d chars – switching to -filter_complex_script",
            cmd_len,
        )
        new_cmd: list[str] = []
        i = 0
        while i < len(cmd):
            if cmd[i] == "-filter_complex" and i + 1 < len(cmd):
                tmp_fd, tmp_script = tempfile.mkstemp(suffix="_fc.txt")
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                        fh.write(cmd[i + 1])
                except Exception:
                    os.close(tmp_fd)
                    raise
                new_cmd.extend(["-filter_complex_script", tmp_script])
                i += 2
            else:
                new_cmd.append(cmd[i])
                i += 1
        cmd = new_cmd

    kwargs: dict = {}
    if pipe_stderr:
        kwargs["stderr"] = subprocess.PIPE
    process = subprocess.Popen(cmd, **kwargs)
    return process, tmp_script


# --------------------------------------------------------------------------- #
#  GPU / codec detection
# --------------------------------------------------------------------------- #

def _detect_nvenc() -> bool:
    """
    Return True only when the installed ffmpeg can actually encode with h264_nvenc.

    Two-stage check:
      1. Encoder list    → does the ffmpeg build include h264_nvenc?
      2. Live smoke-test → can the GPU actually initialise the encoder?
    Stage 2 catches cases where the build has NVENC support but the driver /
    GPU is absent or misconfigured (the encoder appears in the list but fails
    at runtime, which would cause the export to error out).
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        if "h264_nvenc" not in combined:
            log.debug("[nvenc-detect] h264_nvenc not in encoder list")
            return False
    except Exception as exc:
        log.warning("[nvenc-detect] encoder-list probe failed: %s", exc)
        return False

    # Stage 2: quick smoke-test — encode 1 second of a null source
    try:
        test = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "nullsrc=s=128x128:d=1",
                "-vcodec", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True, timeout=15,
        )
        ok = test.returncode == 0
        log.debug("[nvenc-detect] smoke-test returncode=%d  nvenc=%s", test.returncode, ok)
        if not ok:
            log.info(
                "[nvenc-detect] h264_nvenc smoke-test failed — falling back to CPU.\n%s",
                test.stderr.decode("utf-8", errors="ignore")[-400:],
            )
        return ok
    except Exception as exc:
        log.warning("[nvenc-detect] smoke-test failed: %s", exc)
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

def _get_audio_duration(audio_path: str) -> float:
    """Return audio file duration in seconds, or 0.0 if probing fails."""
    try:
        info = ffmpeg.probe(audio_path)
        return float(info["format"].get("duration", 0))
    except Exception as exc:
        log.warning("[ffmpeg] could not probe audio duration for %s: %s", audio_path, exc)
        return 0.0


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


def _run_ffmpeg_mix_batch(
    audio_streams: list,
    duration: float,
    output_path: str,
    label: str = "batch",
) -> None:
    """Mix *audio_streams* (already built ffmpeg-python nodes) into *output_path*."""
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
    combined = combined.filter("atrim", duration=duration)
    out_node = (
        ffmpeg
        .output(combined, output_path, acodec="pcm_s16le", ar=44100, threads=0)
        .overwrite_output()
    )
    log.debug("[ffmpeg %s] command: %s", label, " ".join(ffmpeg.compile(out_node)))
    process, tmp_fc = _run_ffmpeg_node_async(out_node, pipe_stderr=True)
    try:
        for line in process.stderr:
            text = line.decode("utf-8", errors="ignore").rstrip()
            if text:
                log.debug("[ffmpeg %s] %s", label, text)
        process.wait()
    finally:
        if tmp_fc and os.path.exists(tmp_fc):
            try:
                os.remove(tmp_fc)
            except OSError:
                pass
    if process.returncode != 0:
        raise RuntimeError(
            f"ffmpeg {label} exited with code {process.returncode}"
        )


# Each batch of TTS clips is kept below this many inputs so the ffmpeg
# command line stays well within the Windows 32 767-char CreateProcess limit.
_TTS_BATCH_SIZE = 30


def _pre_mix_tts_audio(
    captions: List[Caption],
    duration: float,
    tmp_path: str,
) -> bool:
    """
    Pre-mix all TTS clips into a single PCM WAV file at *tmp_path*.

    With hundreds of captions each TTS clip is its own ``-i`` input, which
    can push the ffmpeg command line past Windows' CreateProcess limit even
    after extracting ``-filter_complex`` to a script.  To avoid this the
    clips are processed in batches of ``_TTS_BATCH_SIZE``.  Each batch is
    mixed to a temporary WAV, then all batch WAVs are reduced to the final
    output file.

    Returns True if at least one TTS clip was processed, False otherwise.
    """
    # ---- Build per-caption streams ----------------------------------------
    tts_entries: list[tuple[float, object]] = []  # (effective_start, node)
    for cap in captions:
        if not cap.tts_audio_path or not os.path.exists(cap.tts_audio_path):
            continue

        # Available playback window for this caption (start → end).
        window = max(0.01, cap.end - cap.effective_start)

        # Probe the actual TTS audio duration so we can auto-fit it.
        audio_dur = _get_audio_duration(cap.tts_audio_path)

        # If the TTS clip is longer than the window, speed it up just enough
        # to fit.  Honour any manual cap.speed if it already achieves that.
        effective_speed = cap.speed
        if audio_dur > 0 and audio_dur > window:
            auto_speed = audio_dur / window
            effective_speed = max(cap.speed, auto_speed)
            log.debug(
                "[tts-fit] caption %d: audio=%.3fs  window=%.3fs  "
                "speed %.2f → %.2f",
                cap.index, audio_dur, window, cap.speed, effective_speed,
            )

        tts_node = ffmpeg.input(cap.tts_audio_path).audio
        tts_node = _build_atempo_chain(tts_node, effective_speed)

        # Hard-clip: prevent audio from overflowing into adjacent captions.
        tts_node = tts_node.filter("atrim", duration=window)

        delay_ms = int(cap.effective_start * 1000)
        if delay_ms > 0:
            tts_node = tts_node.filter("adelay", f"{delay_ms}|{delay_ms}")

        # Pad to exactly the video duration so amix sees a finite-length stream.
        # Without whole_dur, apad is infinite → amix never ends → 20 GB temp file.
        tts_node = tts_node.filter("apad", whole_dur=duration)
        tts_entries.append((cap.effective_start, tts_node))

    if not tts_entries:
        return False

    streams = [node for _, node in tts_entries]

    # ---- Fast path: few enough clips to do in a single pass ---------------
    if len(streams) <= _TTS_BATCH_SIZE:
        _run_ffmpeg_mix_batch(streams, duration, tmp_path, label="pre-mix")
        return True

    # ---- Batched path: mix in chunks, then merge batch WAVs ---------------
    batch_files: list[str] = []
    try:
        for batch_start in range(0, len(streams), _TTS_BATCH_SIZE):
            batch = streams[batch_start: batch_start + _TTS_BATCH_SIZE]
            tmp_fd, batch_path = tempfile.mkstemp(suffix=f"_tts_batch_{len(batch_files)}.wav")
            os.close(tmp_fd)
            batch_files.append(batch_path)
            log.debug(
                "[ffmpeg pre-mix] batch %d/%d (%d clips)",
                len(batch_files),
                (len(streams) + _TTS_BATCH_SIZE - 1) // _TTS_BATCH_SIZE,
                len(batch),
            )
            _run_ffmpeg_mix_batch(
                batch, duration, batch_path,
                label=f"pre-mix batch {len(batch_files)}",
            )

        # Merge all batch WAVs into the final output
        merge_streams = [ffmpeg.input(p).audio for p in batch_files]
        _run_ffmpeg_mix_batch(merge_streams, duration, tmp_path, label="pre-mix merge")
    finally:
        for p in batch_files:
            try:
                os.remove(p)
            except OSError:
                pass

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

    # Input — enable full CUDA pipeline when NVENC is available:
    #   hwaccel=cuda             → GPU decodes the source video
    #   hwaccel_output_format=cuda → decoded frames stay in GPU memory
    # Without hwaccel_output_format, frames are copied back to CPU after decode
    # then re-uploaded to the GPU for h264_nvenc — this is why GPU shows ~0 %.
    src_kwargs: dict = {}
    if use_nvenc:
        src_kwargs["hwaccel"] = "cuda"
        src_kwargs["hwaccel_output_format"] = "cuda"

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
    tmp_fc: str | None = None
    try:
        process, tmp_fc = _run_ffmpeg_node_async(out, pipe_stderr=True)
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
        # Clean up temporary filter-complex script (if used)
        if tmp_fc and os.path.exists(tmp_fc):
            try:
                os.remove(tmp_fc)
            except OSError:
                pass
        # Clean up temporary TTS mix file
        if tmp_tts_path and os.path.exists(tmp_tts_path):
            try:
                os.remove(tmp_tts_path)
            except OSError:
                pass
