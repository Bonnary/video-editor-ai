import asyncio
import edge_tts

# Available Khmer voices
VOICES = {
    "male": "km-KH-PisethNeural",
    "female": "km-KH-SreymomNeural",
}

# --- Configuration ---
TEXT = "សួស្តី តើអ្នកសុខសប្បាយជាទេ? ខ្ញុំអាចនិយាយភាសាខ្មែរបាន។"
VOICE_CHOICE = "female"  # Change to "male" for km-KH-PisethNeural
OUTPUT_FILE = "khmer_output.mp3"


async def synthesize(text: str, voice: str, output_path: str) -> None:
    """Synthesize speech using edge-tts and save to a file."""
    print(f"Voice: {voice}")
    print("Synthesizing speech...")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    print(f"Done! Audio saved to {output_path}")


if __name__ == "__main__":
    selected_voice = VOICES.get(VOICE_CHOICE)
    if selected_voice is None:
        raise ValueError(f"Invalid VOICE_CHOICE '{VOICE_CHOICE}'. Choose 'male' or 'female'.")
    asyncio.run(synthesize(TEXT, selected_voice, OUTPUT_FILE))