#!/usr/bin/env python3
"""
cleanup_transcripts.py — Pre-tagging data quality cleanup.

Removes non-lecture content (bhajans, verse recitations, short clips, transcription
failures, non-canonical speakers) and fixes confirmed speaker misassignments.

Usage:
  python scripts/cleanup_transcripts.py           # dry run — shows what would change
  python scripts/cleanup_transcripts.py --apply   # apply all changes
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TRANSCRIPT_DIR = Path("data/transcripts")
APPLY = "--apply" in sys.argv

DEVANAGARI  = re.compile(r"[\u0900-\u097F]")
ARABIC_URDU = re.compile(r"[\u0600-\u06FF]")
TAMIL       = re.compile(r"[\u0B80-\u0BFF]")
TELUGU      = re.compile(r"[\u0C00-\u0C7F]")
BENGALI     = re.compile(r"[\u0980-\u09FF]")


def segment_ratio(segments: list[dict], pattern: re.Pattern) -> float:
    """Fraction of segments containing any match for the given script pattern."""
    if not segments:
        return 0.0
    count = sum(1 for s in segments if pattern.search(s.get("text", "")))
    return count / len(segments)


# Bhajan/kirtan title keywords — content that is not a lecture
BHAJAN_KEYWORDS = [
    "gaura pahu", "nitai guna mani", "nitai pada kamala",
    "hari haraye namah", "boro krpa", "krsna tava punya",
    "akrodha paramananda", "akordha paramananda",
    "jaya jaya advaita", "jaya radha madhava",
    "jaya saci suta", "ha ha prabhu", "hari hari kabe",
    "ei-baro karuna", "are bhai bhaja", "hari he doyal",
    "sri rupa manjari", "madana mohana tanu",
    "govardhanashtakam", "govardhanastakam", "anadi karama",
    "sannyasa kariya", "gauranga bolite", "pranesvara nivedana",
    "hari hari boro sela", "mora prabhu madana", "radha krsna sevon",
    "thakura vaishnava gana", "sachi suta gaurahari",
    "sri krsna chaitanya prabhu jive",
]

# Non-canonical speakers — speaker in title not in speakers.yaml → delete
DELETE_UNKNOWN_SPEAKER = {
    "471cNREgJZ8",  # HG Naveena Neerada Dasa
    "VY0Mw5_-nLQ",  # Sri Gunakara Rama Dasa
}

# Confirmed speaker misassignments: video_id → correct canonical speaker
SPEAKER_FIXES = {
    # Chanchalapathi Dasa misassigned as Madhu Pandit (@ChanchalapathiDas alias unrecognized)
    "j0dIFg-thXE": "HG Chanchalapathi Dasa",
    "8mdZafj-zZs": "HG Chanchalapathi Dasa",
    "pWstJ-lcMl8": "HG Chanchalapathi Dasa",
    "gjUWKTLaLSU": "HG Chanchalapathi Dasa",
    "MVHjpcwTGxY": "HG Chanchalapathi Dasa",
    # @madhupanditdasaofficial alias unrecognized — assigned to wrong speakers
    "Py-nk5cbuKc": "HG Madhu Pandit Dasa",
    "HnJp2c-EQGc": "HG Madhu Pandit Dasa",
    "r_I7elc1_H0": "HG Madhu Pandit Dasa",
    "7EhW7CakYsY": "HG Madhu Pandit Dasa",
    "Trsftx5iy78": "HG Madhu Pandit Dasa",
    "YPsji1KqC1I": "HG Madhu Pandit Dasa",
    # HH Stoka Krishna Swami assigned to HDG Srila Prabhupada
    "Ugj5n_4y1b8": "HH Stoka Krishna Swami",
    # "HG Stoka Krishna Prabhu" (pre-sannyasa honorific) assigned to HG Madhu Pandit Dasa
    "6az3lopn0ho": "HH Stoka Krishna Swami",
    "DHqk0rATFL4": "HH Stoka Krishna Swami",
    "JkTiablKaLk": "HH Stoka Krishna Swami",
    "MVo8wPtN4eA": "HH Stoka Krishna Swami",
    "N4em7bSs-OE": "HH Stoka Krishna Swami",
}


def classify(data: dict) -> tuple[str, str]:
    """
    Return ('delete', reason), ('fix', new_speaker), or ('ok', '').
    Rules are checked in priority order.
    """
    video_id = data.get("video_id", "")
    title = data.get("title", "").lower()
    duration = data.get("duration", 9999)
    segments = data.get("segments", [])

    if video_id in DELETE_UNKNOWN_SPEAKER:
        return "delete", "non-canonical speaker"

    if duration < 60:
        return "delete", f"too short ({duration}s)"

    if any(kw in title for kw in BHAJAN_KEYWORDS):
        return "delete", "bhajan/kirtan"

    dr = segment_ratio(segments, DEVANAGARI)

    # Historical Thakura as composer in title + high Devanagari = bhajan not caught by keyword
    if ("thakura" in title or "thakur" in title) and dr > 0.70:
        return "delete", f"thakura-composer bhajan (deva={dr:.0%})"

    if "recitation" in title and dr > 0.80:
        return "delete", f"verse recitation (deva={dr:.0%})"

    # Transcription failure: valid lecture title but Whisper produced only Devanagari output
    if dr >= 0.95:
        return "delete", f"transcription failure (deva={dr:.0%})"

    # Foreign-language audio: Urdu/Arabic, Tamil, Telugu, Bengali versions of lectures.
    # Threshold >30% — well above any incidental transliteration or hallucination noise.
    for pattern, label in (
        (ARABIC_URDU, "Urdu/Arabic"),
        (TAMIL,       "Tamil"),
        (TELUGU,      "Telugu"),
        (BENGALI,     "Bengali"),
    ):
        ratio = segment_ratio(segments, pattern)
        if ratio > 0.30:
            return "delete", f"{label} audio ({ratio:.0%} affected segments)"

    if video_id in SPEAKER_FIXES:
        return "fix", SPEAKER_FIXES[video_id]

    return "ok", ""


def main():
    files = sorted(TRANSCRIPT_DIR.glob("*.json"))
    mode = "APPLY" if APPLY else "DRY RUN"
    print(f"[{mode}] Scanning {len(files)} transcripts...\n")

    to_delete: list[tuple[Path, str]] = []
    to_fix: list[tuple[Path, dict, str]] = []
    by_title: dict[str, list[Path]] = defaultdict(list)

    # Pass 1: classify each file individually
    for fpath in files:
        with open(fpath) as f:
            data = json.load(f)
        action, detail = classify(data)
        title = data.get("title", "")

        if action == "delete":
            to_delete.append((fpath, detail))
        elif action == "fix":
            to_fix.append((fpath, data, detail))
            by_title[title].append(fpath)
        else:
            by_title[title].append(fpath)

    # Pass 2: detect duplicate titles among surviving files — keep first, delete rest
    for title, paths in by_title.items():
        if len(paths) > 1:
            keeper = paths[0]
            for dup in paths[1:]:
                to_delete.append((dup, f"duplicate of {keeper.name}"))
                to_fix = [(p, d, s) for p, d, s in to_fix if p != dup]

    # Report deletions
    print(f"=== DELETIONS ({len(to_delete)} files) ===")
    reason_counts: dict[str, int] = defaultdict(int)
    for fpath, reason in sorted(to_delete, key=lambda x: x[0].name):
        key = reason.split("(")[0].strip()
        reason_counts[key] += 1
        print(f"  {fpath.name:<28}  {reason}")

    print(f"\n  Breakdown:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {count:3d}  {reason}")

    # Report speaker fixes
    print(f"\n=== SPEAKER FIXES ({len(to_fix)} files) ===")
    for fpath, data, new_speaker in sorted(to_fix, key=lambda x: x[0].name):
        print(f"  {fpath.name:<28}  {data['speaker']!r} → {new_speaker!r}")
        print(f"  {'':28}  {data['title'][:80]}")

    remaining = len(files) - len(to_delete)
    print(f"\nSummary: {len(to_delete)} deletions, {len(to_fix)} speaker fixes")
    print(f"Transcripts after cleanup: {remaining} (from {len(files)})")

    if not APPLY:
        print("\n[DRY RUN] No changes made. Run with --apply to execute.")
        return

    print("\n--- Applying deletions ---")
    for fpath, reason in to_delete:
        fpath.unlink()
        print(f"  Deleted  {fpath.name}  ({reason})")

    print("\n--- Applying speaker fixes ---")
    for fpath, data, new_speaker in to_fix:
        data["speaker"] = new_speaker
        fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"  Fixed    {fpath.name}  → {new_speaker}")

    print(f"\nDone. {len(to_delete)} files deleted, {len(to_fix)} speakers fixed.")
    print(f"Remaining transcripts: {remaining}")


if __name__ == "__main__":
    main()
