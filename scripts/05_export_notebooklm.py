"""
05_export_notebooklm.py — Export tagged lectures to NotebookLM markdown sources.

Reads data/tagged/*.json (pipeline artifacts) and generates one .md file per speaker
in data/notebooklm/. Each file contains all tagged segments for that speaker, structured
for optimal NotebookLM citation precision.

Optional --include-transcript enriches each segment with reconstructed transcript text
from data/transcripts/{video_id}.json.

Idempotent: regenerates all output files on each run. No API calls.

Usage:
    python scripts/05_export_notebooklm.py
    python scripts/05_export_notebooklm.py --include-transcript
    python scripts/05_export_notebooklm.py --output-dir exports/notebooklm_2026-03
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path


def slugify(name: str) -> str:
    """Convert speaker name to a safe filename slug."""
    return re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))


def get_segment_transcript(
    video_id: str, start: int, end: int, transcript_cache: dict
) -> str:
    """Reconstruct transcript text for a tagged segment's time window."""
    if video_id not in transcript_cache:
        t_path = Path(f"data/transcripts/{video_id}.json")
        if not t_path.exists():
            transcript_cache[video_id] = []
        else:
            transcript_cache[video_id] = json.loads(t_path.read_text())["segments"]
    whisper_segs = transcript_cache[video_id]
    text = " ".join(
        s["text"].strip()
        for s in whisper_segs
        if s["start"] >= start and s["end"] <= end + 5  # +5s tolerance
    )
    return text.strip()


def format_segment(
    seg: dict, video_id: str, include_transcript: bool, transcript_cache: dict
) -> str:
    """Format a single tagged segment as markdown."""
    themes = ", ".join(seg.get("themes", [])) or "general"
    verse_refs = ", ".join(seg.get("verse_references", [])) or "—"
    content_type = seg.get("content_type", "")
    key_quote = seg.get("key_quote", "")
    summary = seg.get("summary", "")

    lines = [
        f"### {themes}",
        f"**Verse References:** {verse_refs}",
        f"**Content Type:** {content_type}",
        f'**Key Quote:** "{key_quote}"',
        f"**Summary:** {summary}",
    ]

    if include_transcript:
        transcript_text = get_segment_transcript(
            video_id, seg["start_time"], seg["end_time"], transcript_cache
        )
        if transcript_text:
            lines.append("")
            lines.append(transcript_text)

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def format_lecture(
    lecture: dict, include_transcript: bool, transcript_cache: dict
) -> str:
    """Format all segments of a single lecture as markdown."""
    video_id = lecture["video_id"]
    title = lecture["title"]
    speaker = lecture["speaker"]
    segments = sorted(lecture["segments"], key=lambda s: s["start_time"])

    parts = [
        f"## {title}",
        f"**Speaker:** {speaker}",
        "",
    ]
    for seg in segments:
        parts.append(format_segment(seg, video_id, include_transcript, transcript_cache))

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Export tagged lectures to NotebookLM markdown sources"
    )
    parser.add_argument(
        "--include-transcript",
        action="store_true",
        help="Enrich segments with reconstructed transcript text",
    )
    parser.add_argument("--output-dir", default="data/notebooklm")
    parser.add_argument(
        "--word-limit",
        type=int,
        default=450_000,
        help="Warn if a group file exceeds this word count (default: 450k, safe margin below 500k limit)",
    )
    args = parser.parse_args()

    tagged_files = sorted(Path("data/tagged").glob("*.json"))
    if not tagged_files:
        print("[05_export] No tagged files found in data/tagged/")
        return

    # Group lectures by speaker
    groups: dict[str, list[dict]] = {}
    for path in tagged_files:
        data = json.loads(path.read_text())
        speaker = data["speaker"]
        if speaker not in groups:
            groups[speaker] = []
        groups[speaker].append(data)

    # Sort lectures within each group alphabetically by title
    for speaker in groups:
        groups[speaker].sort(key=lambda d: d["title"])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    transcript_cache: dict[str, list] = {}
    today = date.today().isoformat()

    for speaker, lectures in sorted(groups.items()):
        slug = slugify(speaker)
        filename = f"{slug}.md"
        output_path = output_dir / filename

        lecture_count = len(lectures)
        segment_count = sum(len(lec["segments"]) for lec in lectures)

        # Generate body content
        body_parts = [
            format_lecture(lec, args.include_transcript, transcript_cache)
            for lec in lectures
        ]
        body = "\n\n".join(body_parts)
        word_count = len(body.split())

        # Assemble full document
        header = (
            f"# {speaker} — Vaishnava Lecture Corpus\n"
            f"Generated: {today} | Lectures: {lecture_count} | "
            f"Segments: {segment_count} | Est. words: {word_count:,}\n\n---\n"
        )
        content = header + "\n" + body

        output_path.write_text(content, encoding="utf-8")
        print(
            f"  Wrote {filename}: {lecture_count} lectures, "
            f"{segment_count} segments, ~{word_count:,} words"
        )

        if word_count > args.word_limit:
            print(
                f"  [WARN] {filename}: {word_count:,} words — exceeds {args.word_limit:,} limit. "
                f"Consider splitting by adding a second speaker grouping or disabling --include-transcript."
            )

    print(f"\n[05_export] Done. {len(groups)} files written to {args.output_dir}/")
    print("  Next step: upload .md files to NotebookLM as sources.")


if __name__ == "__main__":
    main()
