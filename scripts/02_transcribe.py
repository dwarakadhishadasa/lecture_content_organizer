"""
02_transcribe.py — Transcribe audio files using Whisper large-v3 on GPU.

For each audio file in data/audio/:
  - Resolves speaker from video title (safety net; 01_download.py pre-filters)
  - Transcribes with faster-whisper
  - Writes data/transcripts/{video_id}.json atomically
  - Deletes source audio to keep disk bounded

Idempotent: skips videos where transcript JSON already exists.
"""
import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel

from scripts.utils.resolve_speaker import load_speakers, resolve_speaker


def main():
    speakers = load_speakers("config/speakers.yaml")

    Path("data/transcripts").mkdir(parents=True, exist_ok=True)

    # Clean up stale temp files from any interrupted previous run
    for tmp in Path("data/transcripts").glob("*.json.tmp"):
        tmp.unlink()
        print(f"[02_transcribe] Removed stale temp file: {tmp.name}")

    model = WhisperModel("large-v3", device="cuda", compute_type="float16")

    audio_files = sorted(Path("data/audio").glob("*.mp3")) + \
                  sorted(Path("data/audio").glob("*.m4a")) + \
                  sorted(Path("data/audio").glob("*.webm"))
    total = len(audio_files)

    if total == 0:
        print("[02_transcribe] No audio files found in data/audio/")
        sys.exit(0)

    for n, audio_path in enumerate(audio_files, 1):
        video_id = audio_path.stem
        transcript_path = Path(f"data/transcripts/{video_id}.json")

        # Skip + cleanup: if transcript exists, delete audio (handles re-download-after-archive-loss)
        if transcript_path.exists():
            if audio_path.exists():
                audio_path.unlink()
            print(f"[{n}/{total}] Skipping {video_id} (transcript exists)")
            continue

        # Load info.json — glob handles pre/post-processor extension variants
        info_matches = sorted(Path("data/audio").glob(f"{video_id}*.info.json"))
        if not info_matches:
            print(f"[{n}/{total}] WARNING: No info.json for {video_id} — skipping")
            continue
        info_path = info_matches[0]

        with open(info_path) as f:
            info = json.load(f)

        title = info.get("title", "")
        youtube_url = info.get("webpage_url", "")
        duration = info.get("duration", 0)

        # Safety net: resolve speaker (primary filter was in 01_download.py match_filter)
        speaker = resolve_speaker(title, speakers)
        if speaker is None:
            print(f"[{n}/{total}] Skipping {video_id}: speaker unresolved")
            audio_path.unlink()
            continue

        print(f"[{n}/{total}] Transcribing {video_id} ({speaker})...")
        try:
            whisper_segments, _ = model.transcribe(str(audio_path), beam_size=5)
            segments = [
                {"start": seg.start, "end": seg.end, "text": seg.text}
                for seg in whisper_segments
            ]
        except Exception as e:
            print(f"[{n}/{total}] ERROR transcribing {video_id}: {e} — skipping")
            continue

        data = {
            "video_id": video_id,
            "title": title,
            "speaker": speaker,
            "youtube_url": youtube_url,
            "duration": duration,
            "segments": segments,
        }

        # Atomic write: temp file → rename (prevents corruption if pod is killed mid-write)
        tmp_path = Path(f"data/transcripts/{video_id}.json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False))
        tmp_path.rename(transcript_path)

        audio_path.unlink()
        print(f"[{n}/{total}] Transcribed + deleted: {video_id}")

    print(f"[02_transcribe] Done. {total} audio files processed.")


if __name__ == "__main__":
    main()
