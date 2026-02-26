import argparse
import os
import warnings
warnings.filterwarnings("ignore")

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
    Load a Whisper model onto the best available device (CUDA or CPU).
    If model_name is 'auto', selects the best model based on available VRAM.

    Model options and VRAM usage:
        tiny    ~1 GB   - fastest, least accurate
        base    ~1 GB   - fast, decent accuracy
        small   ~2 GB   - good balance
        medium  ~5 GB   - great accuracy
        large   ~10 GB  - best accuracy
        auto            - automatically pick best model for your GPU (default)

    Returns:
        model:  loaded Whisper model
        device: device string ("cuda" or "cpu")
    """
    import torch
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"

    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"✅ GPU detected: {gpu_name} ({vram_gb:.1f} GB VRAM)")

        if model_name == "auto":
            model_name = auto_select_model(vram_gb)
            print(f"🤖 Auto-selected model: '{model_name}' (fits in {vram_gb * 0.8:.1f} GB usable VRAM)")
        else:
            required = MODEL_VRAM_REQUIREMENTS.get(model_name, 0)
            usable   = vram_gb * 0.80
            if required > usable:
                suggested = auto_select_model(vram_gb)
                print(f"⚠️  '{model_name}' needs ~{required} GB but only {usable:.1f} GB usable VRAM available.")
                print(f"   Switching to '{suggested}' to avoid OOM errors.")
                model_name = suggested
            else:
                print(f"✅ Using manually selected model: '{model_name}' (~{required} GB VRAM)")
    else:
        print("⚠️  CUDA not available — using CPU (slower)")
        if model_name == "auto":
            # On CPU, default to small to keep things manageable
            model_name = "small"
            print(f"🤖 Auto-selected model for CPU: '{model_name}'")
        elif model_name == "large":
            print("⚠️  'large' on CPU will be very slow. Consider using 'medium' or 'small'.")

    print(f"📦 Loading Whisper model: '{model_name}'...")
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
        fp16=(device == "cuda")
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