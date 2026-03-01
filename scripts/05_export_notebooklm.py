"""
05_export_notebooklm.py — Export tagged lectures to NotebookLM markdown sources.

Reads data/tagged/*.json and produces ready-to-upload files in data/notebooklm/:
  - {speaker}.md (or {speaker}_1.md, {speaker}_2.md, …) — per-speaker deep dives
  - all_speakers.md (or all_speakers_1.md, …)            — cross-speaker queries

Every segment includes: transcript, timestamp URL, themes, verse references,
content type, audience (circle-fit labels), key quote, and summary.

NotebookLM limit: ~500,000 words per source. Files are auto-split to stay under
450,000 words (safe margin). All output files are ready for direct upload.

Run: python scripts/05_export_notebooklm.py
"""

import json
import re
from datetime import date
from pathlib import Path

WORD_LIMIT = 450_000        # Safe margin below NotebookLM's ~500k per-source limit
OUTPUT_DIR = Path("data/notebooklm")

CIRCLE_FIT_LABELS = {
    1: "Full-time devotees",
    2: "Congregation / volunteers",
    3: "Newcomers",
    4: "General public",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))


def format_segment(seg: dict, heading: str = "###") -> str:
    themes      = ", ".join(seg.get("themes", [])) or "general"
    verse_refs  = ", ".join(seg.get("verse_references", [])) or "—"
    content_type = seg.get("content_type") or "—"
    key_quote   = seg.get("key_quote", "")
    summary     = seg.get("summary", "")
    timestamp   = seg.get("timestamp_url", "")
    transcript  = seg.get("transcript", "")
    audience    = ", ".join(
        CIRCLE_FIT_LABELS.get(n, str(n)) for n in sorted(seg.get("circle_fit", []))
    ) or "—"

    lines = [
        f"{heading} {themes}",
        f"**Timestamp:** {timestamp}",
        f"**Verse References:** {verse_refs}",
        f"**Content Type:** {content_type}",
        f"**Audience:** {audience}",
        f'**Key Quote:** "{key_quote}"',
        f"**Summary:** {summary}",
    ]
    if transcript:
        lines += ["", transcript]
    lines += ["", "---"]
    return "\n".join(lines)


def format_lecture(
    lecture: dict,
    lecture_heading: str = "##",
    segment_heading: str = "###",
) -> str:
    segs = sorted(lecture["segments"], key=lambda s: s["start_time"])
    parts = [
        f"{lecture_heading} {lecture['title']}",
        f"**Speaker:** {lecture['speaker']}",
        "",
    ]
    for seg in segs:
        parts.append(format_segment(seg, segment_heading))
    return "\n".join(parts)


def greedy_pack(
    blocks: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    """Greedy-pack (label, text) pairs into bins each ≤ WORD_LIMIT words."""
    bins: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_words = 0
    for label, block in blocks:
        w = len(block.split())
        if current and current_words + w > WORD_LIMIT:
            bins.append(current)
            current, current_words = [], 0
        current.append((label, block))
        current_words += w
    if current:
        bins.append(current)
    return bins


def write_packed(
    blocks: list[tuple[str, str]],
    filename_base: str,
    title_base: str,
    today: str,
    fixed_meta: str = "",
) -> int:
    """Greedy-pack blocks and write numbered .md files. Returns number of files written."""
    bins = greedy_pack(blocks)
    total = len(bins)
    for i, bin_blocks in enumerate(bins, 1):
        path = OUTPUT_DIR / (
            f"{filename_base}_{i}.md" if total > 1 else f"{filename_base}.md"
        )
        body = "\n\n".join(block for _, block in bin_blocks)
        word_count = len(body.split())
        part_note = f" | Part {i}/{total}" if total > 1 else ""
        meta = f"Generated: {today}{part_note}"
        if fixed_meta:
            meta += f" | {fixed_meta}"
        meta += f" | Est. words: {word_count:,}"
        title = f"{title_base} (Part {i}/{total})" if total > 1 else title_base
        path.write_text(f"# {title}\n{meta}\n\n---\n\n{body}", encoding="utf-8")
        print(f"  {path.name}: ~{word_count:,} words")
    if total > 1:
        print(f"  → auto-split into {total} files (each ≤ {WORD_LIMIT:,} words)")
    return total


def main():
    tagged_files = sorted(Path("data/tagged").glob("*.json"))
    if not tagged_files:
        print("[05_export] No tagged files found in data/tagged/")
        return

    # Load and group by speaker
    groups: dict[str, list[dict]] = {}
    for path in tagged_files:
        data = json.loads(path.read_text())
        groups.setdefault(data["speaker"], []).append(data)
    for lectures in groups.values():
        lectures.sort(key=lambda d: d["title"])

    OUTPUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()

    # Remove stale output files — script regenerates everything
    for old in OUTPUT_DIR.glob("*.md"):
        old.unlink()

    # ── Per-speaker exports ────────────────────────────────────────────────────
    print(f"\n[05_export] Per-speaker ({len(groups)} speakers):")
    for speaker, lectures in sorted(groups.items()):
        total_segs = sum(len(lec["segments"]) for lec in lectures)
        lecture_blocks = [(lec["title"], format_lecture(lec)) for lec in lectures]
        write_packed(
            lecture_blocks,
            filename_base=slugify(speaker),
            title_base=f"{speaker} — Vaishnava Lecture Corpus",
            today=today,
            fixed_meta=f"Lectures: {len(lectures)} | Segments: {total_segs}",
        )

    # ── All-speakers export ────────────────────────────────────────────────────
    # Pack at speaker level; if a speaker's block alone exceeds WORD_LIMIT,
    # fall back to lecture-level blocks so greedy-pack can distribute them.
    print(f"\n[05_export] All-speakers export:")
    all_blocks: list[tuple[str, str]] = []
    for speaker, lectures in sorted(groups.items()):
        speaker_body = "\n\n".join(
            format_lecture(lec, "###", "####") for lec in lectures
        )
        full_block = f"## {speaker}\n\n{speaker_body}"
        if len(full_block.split()) <= WORD_LIMIT:
            all_blocks.append((speaker, full_block))
        else:
            # Speaker too large for one bin — split into per-lecture blocks
            for lec in lectures:
                block = f"## {speaker}\n\n{format_lecture(lec, '###', '####')}"
                all_blocks.append((f"{speaker} | {lec['title']}", block))
    write_packed(
        all_blocks,
        filename_base="all_speakers",
        title_base="All Speakers — Vaishnava Lecture Corpus",
        today=today,
        fixed_meta=f"Speakers: {len(groups)}",
    )

    total_files = len(list(OUTPUT_DIR.glob("*.md")))
    print(f"\n[05_export] Done. {total_files} files written to {OUTPUT_DIR}/")
    print(
        f"  Upload all .md files to NotebookLM "
        f"(limit: {WORD_LIMIT:,} words/source, 50 sources/notebook)."
    )


if __name__ == "__main__":
    main()
