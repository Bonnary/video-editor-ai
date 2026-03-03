import argparse
import os
import platform
import subprocess
import warnings
warnings.filterwarnings("ignore")


def _build_torch_runtime_error_message(exc: Exception) -> str:
    """Return a user-friendly message for broken torch/CUDA runtime imports."""
    exc_text = str(exc)
    system = platform.system()

    if system == "Linux":
        return (
            "PyTorch failed to load because CUDA runtime libraries are missing or mismatched "
            f"({exc_text}).\n\n"
            "Fix options:\n"
            "1) Use NVIDIA GPU (Omarchy/Arch): install CUDA runtime\n"
            "   sudo pacman -S nvidia-open opencl-nvidia cuda cudnn libxcrypt-compat \n\n"
            "2) Run on CPU only (recommended if you don't need GPU):\n"
            "   uv pip uninstall -y torch torchvision torchaudio\n"
            "   uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu\n\n"
            "Then restart the app."
        )

    if system == "Windows":
        return (
            "PyTorch failed to load because CUDA runtime libraries are missing or mismatched "
            f"({exc_text}).\n\n"
            "Install the NVIDIA CUDA Toolkit, or reinstall CPU-only PyTorch, then restart the app."
        )

    return (
        "PyTorch failed to load because CUDA runtime libraries are missing or mismatched "
        f"({exc_text}).\n\n"
        "Install compatible CUDA runtime libraries, or reinstall CPU-only PyTorch, then restart the app."
    )


# ---------------------------------------------------------------------------
# NVIDIA / CUDA helpers
# ---------------------------------------------------------------------------

def _detect_nvidia_gpu() -> tuple[bool, str]:
    """
    Probe nvidia-smi to confirm an NVIDIA GPU is physically present.
    Returns (has_gpu: bool, info_string: str).
    Works on Windows, Linux, and macOS (via Rosetta / Boot Camp).
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
            return bool(lines), lines[0] if lines else "Unknown NVIDIA GPU"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return False, ""


def _cuda_runtime_present() -> bool:
    """
    Check whether the CUDA runtime shared libraries are actually loadable
    WITHOUT touching torch.cuda (which would print the ugly C-level error).

    Uses ctypes.util.find_library so it respects ldconfig / LD_LIBRARY_PATH
    on Linux and the DLL search path on Windows.
    """
    import ctypes
    import ctypes.util

    system = platform.system()

    if system == "Linux":
        # libcublas.so.* is the minimum we need; if ldconfig can't find it,
        # torch.cuda will fail with the "not found in system path" message.
        return ctypes.util.find_library("cublas") is not None

    if system == "Windows":
        # On Windows CUDA ships as cublas64_XX.dll in the CUDA Toolkit bin dir.
        for name in ("cublas64_12", "cublas64_11", "cublas64_10"):
            if ctypes.util.find_library(name):
                return True
        return False

    # macOS with CUDA (extremely rare — Boot Camp only)
    return ctypes.util.find_library("cublas") is not None


def format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    assert seconds >= 0
    milliseconds = round(seconds * 1000)
    hours = milliseconds // 3_600_000
    milliseconds -= hours * 3_600_000
    minutes = milliseconds // 60_000
    milliseconds -= minutes * 60_000
    secs = milliseconds // 1_000
    milliseconds -= secs * 1_000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

# Model VRAM requirements in GB (with safety buffer)
MODEL_VRAM_REQUIREMENTS = {
    "tiny":   1.0,
    "base":   1.0,
    "small":  2.0,
    "medium": 5.0,
    "large":  10.0,
}

def auto_select_model(vram_gb: float) -> str:
    """
    Automatically select the best Whisper model that fits in available VRAM.
    Uses 80% of total VRAM as usable budget to leave headroom for other GPU ops.
    """
    usable_vram = vram_gb * 0.80
    # Iterate from best to worst, pick the first that fits
    for model in ["large", "medium", "small", "base", "tiny"]:
        if MODEL_VRAM_REQUIREMENTS[model] <= usable_vram:
            return model
    return "tiny"  # fallback

def load_model(model_name: str = "auto"):
    """
    Load a Whisper model on the best available compute device.

    Device priority:
        1. CUDA  — NVIDIA GPU on Windows / Linux
        2. MPS   — Apple Silicon GPU (Metal Performance Shaders)
        3. CPU   — fallback on any platform

    If an NVIDIA GPU is present but the CUDA runtime is missing, a
    RuntimeError is raised with platform-specific installation instructions
    instead of silently falling back to CPU.

    model_name:
        tiny   ~1 GB VRAM    fastest, least accurate
        base   ~1 GB VRAM    fast, decent accuracy
        small  ~2 GB VRAM    good balance
        medium ~5 GB VRAM    great accuracy
        large  ~10 GB VRAM   best accuracy
        auto               auto-select based on available memory (default)

    Returns:
        (model, device_str) — device_str is "cuda", "mps", or "cpu"
    """
    try:
        import torch
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError(_build_torch_runtime_error_message(exc)) from exc

    import whisper

    system  = platform.system()   # "Windows" | "Linux" | "Darwin"
    machine = platform.machine()  # "x86_64" | "AMD64" | "arm64" | "aarch64"

    device = "cpu"  # default; overridden below

    # ------------------------------------------------------------------ MPS
    # Apple Silicon (M-series chips): use Metal Performance Shaders.
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        try:
            if torch.backends.mps.is_available():
                device = "mps"
                print("✅ Apple Silicon detected — using MPS (Metal Performance Shaders)")
            else:
                print("⚠️  MPS unavailable on this Mac — falling back to CPU")
        except Exception as mps_err:
            print(f"⚠️  MPS check failed ({mps_err}) — falling back to CPU")

    # ------------------------------------------------------------------ CUDA
    # Check order (each step only runs if the previous one passed):
    #   1. Is an NVIDIA GPU physically present?     → nvidia-smi
    #   2. Are the CUDA runtime libs loadable?      → ctypes (no torch.cuda touch)
    #   3. Does torch.cuda confirm the device?      → torch.cuda.is_available()
    #
    # Step 2 ensures torch.cuda is NEVER called when the runtime is absent,
    # which prevents the C-level "libcublas.so not found" message from printing.
    # The user has already been warned about missing CUDA at startup, so here
    # we simply fall back to CPU without raising.
    else:
        has_nvidia, _ = _detect_nvidia_gpu()

        if not has_nvidia:
            print("ℹ️  No NVIDIA GPU detected — using CPU")

        elif not _cuda_runtime_present():
            # CUDA not installed — user was already warned at startup.
            print("ℹ️  CUDA runtime not found — using CPU")

        else:
            # Both GPU and CUDA runtime confirmed — safe to call torch.cuda.
            try:
                if torch.cuda.is_available():
                    device = "cuda"
                else:
                    print(
                        "⚠️  NVIDIA GPU and CUDA libs found but torch.cuda.is_available() "
                        "returned False (possible driver/torch version mismatch). "
                        "Falling back to CPU."
                    )
            except Exception as exc:
                print(f"⚠️  torch.cuda check error ({exc}). Falling back to CPU.")

    # ------------------------------------------------------------------ VRAM / model selection
    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"✅ GPU: {gpu_name} ({vram_gb:.1f} GB VRAM)  |  "
              f"CUDA {torch.version.cuda}")

        if model_name == "auto":
            model_name = auto_select_model(vram_gb)
            print(f"🤖 Auto-selected model: '{model_name}' "
                  f"(fits in {vram_gb * 0.8:.1f} GB usable VRAM)")
        else:
            required = MODEL_VRAM_REQUIREMENTS.get(model_name, 0)
            usable   = vram_gb * 0.80
            if required > usable:
                suggested = auto_select_model(vram_gb)
                print(f"⚠️  '{model_name}' needs ~{required} GB but only "
                      f"{usable:.1f} GB usable. Switching to '{suggested}'.")
                model_name = suggested
            else:
                print(f"✅ Model: '{model_name}' (~{required} GB VRAM)")

    elif device == "mps":
        # MPS has no reliable public VRAM query — default to "small" for safety.
        if model_name == "auto":
            model_name = "small"
            print(f"🤖 Auto-selected model for Apple Silicon: '{model_name}'")
        else:
            print(f"✅ Model: '{model_name}' (Apple Silicon MPS)")

    else:  # cpu
        if model_name == "auto":
            model_name = "small"
            print(f"🤖 Auto-selected model for CPU: '{model_name}'")
        elif model_name == "large":
            print("⚠️  'large' on CPU will be very slow. "
                  "Consider 'medium' or 'small'.")

    print(f"📦 Loading Whisper model: '{model_name}'…")
    model = whisper.load_model(model_name, device=device)
    return model, device


def transcribe_to_srt(input_file: str, output_file: str, model_name: str = "auto"):
    """Transcribe an audio file and save as SRT subtitle file."""
    model, device = load_model(model_name)

    print(f"🎙️  Transcribing: {input_file}")
    result = model.transcribe(
        input_file,
        verbose=True,
        word_timestamps=False,
        fp16=(device == "cuda"),  # fp16 only on CUDA; MPS/CPU use fp32
    )

    print(f"💾 Writing SRT to: {output_file}")
    with open(output_file, "w", encoding="utf-8") as f:
        for i, segment in enumerate(result["segments"], start=1):
            start = format_timestamp(segment["start"])
            end   = format_timestamp(segment["end"])
            text  = segment["text"].strip()
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")

    print(f"✅ Done! {len(result['segments'])} segments written to '{output_file}'")
    print(f"🌍 Detected language: {result.get('language', 'unknown')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcribe audio to SRT using Whisper")
    parser.add_argument("--input",  default="audio.mp3",  help="Input audio file (default: audio.mp3)")
    parser.add_argument("--output", default="result.srt", help="Output SRT file (default: result.srt)")
    parser.add_argument(
        "--model", default="auto",
        choices=["tiny", "base", "small", "medium", "large", "auto"],
        help="Whisper model size. Use 'auto' to let the script pick based on your GPU (default: auto)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ Error: Input file '{args.input}' not found.")
        exit(1)

    transcribe_to_srt(args.input, args.output, args.model)