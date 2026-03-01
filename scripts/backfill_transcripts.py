"""
backfill_transcripts.py — Add "transcript" field to existing tagged JSONs.

03_tag.py now writes "transcript" into every segment, but the 742 tagged files
created before this change are missing it. This script patches them in-place by
reconstructing the spoken text from the matching Whisper segments in data/transcripts/.

Safe to re-run: skips files where all segments already have "transcript".
"""
import json
from pathlib import Path


def backfill(tagged_path: Path) -> tuple[int, int]:
    """Add missing "transcript" to segments. Returns (patched_count, skipped_count)."""
    with open(tagged_path) as f:
        data = json.load(f)

    video_id = data["video_id"]
    segments = data["segments"]

    if all("transcript" in seg for seg in segments):
        return 0, len(segments)

    transcript_path = Path("data/transcripts") / f"{video_id}.json"
    if not transcript_path.exists():
        print(f"  [WARN] No source transcript for {video_id} — skipping")
        return 0, 0

    with open(transcript_path) as f:
        whisper_segs = json.load(f).get("segments", [])

    patched = 0
    for seg in segments:
        if "transcript" in seg:
            continue
        start, end = seg["start_time"], seg["end_time"]
        seg["transcript"] = " ".join(
            ws["text"].strip()
            for ws in whisper_segs
            if ws["start"] >= start and ws["start"] < end
        )
        patched += 1

    tagged_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return patched, len(segments) - patched


def main():
    tagged_files = sorted(Path("data/tagged").glob("*.json"))
    if not tagged_files:
        print("[backfill] No tagged files found in data/tagged/")
        return

    total_files = len(tagged_files)
    files_patched = 0
    files_skipped = 0
    segs_patched = 0

    for n, path in enumerate(tagged_files, 1):
        patched, skipped = backfill(path)
        if patched:
            files_patched += 1
            segs_patched += patched
            print(f"[{n}/{total_files}] Patched {path.stem} ({patched} segments)")
        else:
            files_skipped += 1

    print(f"\n[backfill] Done. {files_patched} files patched ({segs_patched} segments), "
          f"{files_skipped} already up-to-date or skipped.")


if __name__ == "__main__":
    main()
