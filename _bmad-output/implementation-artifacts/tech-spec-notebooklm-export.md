---
title: 'NotebookLM Export Script'
slug: 'notebooklm-export'
created: '2026-02-28'
status: 'Draft'
stepsCompleted: []
tech_stack:
  - Python 3.10+
  - No new dependencies (uses stdlib: json, pathlib, argparse, datetime)
files_to_modify: []
files_to_create:
  - scripts/05_export_notebooklm.py
code_patterns:
  - Standalone script — NOT part of run_pipeline.sh; run on-demand
  - Reads from data/tagged/*.json (permanent pipeline artifacts)
  - Optionally enriches with transcript text from data/transcripts/*.json
  - Outputs one .md file per speaker to data/notebooklm/
  - Idempotent: regenerates all output files on each run (fast, no API calls)
test_patterns:
  - No formal test framework (personal tooling)
  - Single-speaker dry run: verify word count, segment count, file structure
  - Open generated .md in text editor before uploading to NotebookLM
---

# Tech-Spec: NotebookLM Export Script

**Created:** 2026-02-28

## Overview

### Problem Statement

The existing pipeline produces ~10,000 tagged segments across ~1,000 lectures stored in `data/tagged/*.json`. This rich corpus is currently accessible only via Notion (structured filter/search). There is no way to ask a conversational question like *"What did the speakers say about surrender across all lectures?"* and get a synthesized, grounded answer with precise citations. A second application is needed that enables this conversational mode without duplicating or replacing the Notion-based pipeline.

### Solution

A standalone Python script (`scripts/05_export_notebooklm.py`) that reads the existing tagged JSON artifacts and generates clean, structured Markdown files suitable for upload as NotebookLM sources. Each output file combines all lectures for one speaker into a single NotebookLM source document. The result: one `.md` file per speaker containing all tagged content — ready for conversational Q&A with grounded citations and no hallucinations.

### Scope

**In Scope:**
- Read `data/tagged/*.json` to build export documents
- Group all lectures by speaker — one `.md` file per speaker
- Optionally enrich each segment with reconstructed transcript text from `data/transcripts/*.json`
- Output one `.md` file per group to `data/notebooklm/`
- Word count tracking with a warning when a file approaches NotebookLM's ~500,000-word per-source limit
- CLI with sensible defaults — runnable in one command with no configuration

**Out of Scope:**
- Automated upload to NotebookLM (no public API exists — upload is manual)
- Incremental/delta exports — full regeneration on each run is fast enough (~1,000 JSON files, no API calls)
- Any changes to `run_pipeline.sh` or existing pipeline scripts

---

## Context for Development

### Where This Fits

```
data/tagged/*.json      ──→  run_pipeline.sh  ──→  Notion       (App 1: structured index)
data/transcripts/*.json ─┘
                         └→  05_export_notebooklm.py  ──→  data/notebooklm/*.md  ──→  NotebookLM  (App 2: conversational)
```

The export script is a **pure consumer** — it reads existing artifacts and writes new files. Nothing upstream is modified. Run it locally after a `git pull` from the RunPod-produced tagged data.

### Key Constraints

- **NotebookLM source limit:** ~500,000 words per source document (~2 MB text). A 1-hour lecture transcript is ~8,000–12,000 words of raw text; tagged-only (summaries + key quotes, no transcript) is ~300–500 words per segment. A single prolific speaker with 200 lectures will comfortably fit in one source in tagged-only mode; may split if transcript text is included.
- **No new dependencies:** Uses only Python stdlib. The `data/tagged/` and `data/transcripts/` files are already on disk.
- **Transcript text is optional:** Tagged JSON does not store segment-level transcript text (only key_quote and summary). Transcript text must be reconstructed from `data/transcripts/{video_id}.json` by matching Whisper segments within the tagged segment's `[start_time, end_time]` window.

### Output Format

Each generated `.md` file is structured for maximum NotebookLM citation precision:

```markdown
# {Speaker Name} — Vaishnava Lecture Corpus
Generated: {date} | Lectures: {N} | Segments: {N} | Est. words: {N}

---

## {Lecture Title}
**Speaker:** {speaker}

### {themes joined}
**Verse References:** {verse_references joined}
**Content Type:** {content_type}
**Key Quote:** "{key_quote}"
**Summary:** {summary}

{transcript_text if --include-transcript}

---
```

This structure ensures:
- NotebookLM cites at the segment level (not just "somewhere in this 3-hour lecture")
- Verse references are scannable in plain text for scripture-based queries
- Content type distinguishes narrative/pastime content from philosophical explanation
- Transcript text provides the full spoken words for rich, grounded citations

---

## Implementation Plan

### Tasks

- [ ] **Task 1: Implement `scripts/05_export_notebooklm.py`**
  - File: `scripts/05_export_notebooklm.py`
  - Action: Implement the full export script with the following structure:

  **1. CLI argument parsing:**
  ```python
  parser = argparse.ArgumentParser(description="Export tagged lectures to NotebookLM markdown sources")
  parser.add_argument("--include-transcript", action="store_true",
                      help="Enrich segments with reconstructed transcript text")
  parser.add_argument("--output-dir", default="data/notebooklm")
  parser.add_argument("--word-limit", type=int, default=450_000,
                      help="Warn if a group file exceeds this word count (default: 450k, safe margin below 500k limit)")
  ```

  **2. Load tagged files:**
  ```python
  tagged_files = sorted(Path("data/tagged").glob("*.json"))
  # Group by speaker into: Dict[speaker_name, List[dict]]
  ```

  **3. Grouping logic:**
  - Group key = `data["speaker"]`; filename = `{speaker_slug}.md`

  **4. Transcript reconstruction (if `--include-transcript`):**
  ```python
  def get_segment_transcript(video_id: str, start: int, end: int,
                              transcript_cache: dict) -> str:
      """Reconstruct transcript text for a tagged segment's time window."""
      if video_id not in transcript_cache:
          t_path = Path(f"data/transcripts/{video_id}.json")
          if not t_path.exists():
              return ""
          transcript_cache[video_id] = json.loads(t_path.read_text())["segments"]
      whisper_segs = transcript_cache[video_id]
      text = " ".join(
          s["text"].strip()
          for s in whisper_segs
          if s["start"] >= start and s["end"] <= end + 5  # +5s tolerance
      )
      return text.strip()
  ```

  **5. Markdown generation per group:**
  - Sort lectures within group by speaker then title (alphabetical)
  - For each lecture, sort segments by `start_time`
  - Format each segment using the output template above
  - Track running word count (approximate: `len(text.split())`)

  **6. Word count warning:**
  ```python
  if word_count > args.word_limit:
      print(f"  [WARN] {filename}: {word_count:,} words — exceeds {args.word_limit:,} limit. "
            f"Consider splitting by adding a second speaker grouping or disabling --include-transcript.")
  ```

  **7. Write output files:**
  ```python
  Path(args.output_dir).mkdir(exist_ok=True)
  output_path.write_text(markdown_content, encoding="utf-8")
  print(f"  Wrote {filename}: {lecture_count} lectures, {segment_count} segments, ~{word_count:,} words")
  ```

  **8. Summary on completion:**
  ```python
  print(f"\n[05_export] Done. {len(groups)} files written to {args.output_dir}/")
  print(f"  Next step: upload .md files to NotebookLM as sources.")
  ```

  - Notes:
    - Slugify group keys for filenames: replace spaces with `_`, strip special chars, lowercase
    - `transcript_cache` dict avoids re-reading the same `data/transcripts/{video_id}.json` for multiple segments from the same lecture
    - Script is idempotent: overwrites output files on every run; fast (no API calls, pure I/O)
    - If `data/transcripts/{video_id}.json` is missing (e.g., deleted to save space), transcript reconstruction silently returns `""` — tagged metadata is still exported

---

## Acceptance Criteria

- [ ] **AC 1:** Given `data/tagged/` contains N tagged lectures for M speakers, when `python scripts/05_export_notebooklm.py` runs, then M `.md` files are created in `data/notebooklm/`, one per speaker, each containing all of that speaker's lectures and segments.

- [ ] **AC 2:** Given a speaker with 50 lectures (each ~6 segments), when exported, then the output file contains 300 segment entries, each with `Key Quote`, `Summary`, `Verse References`, and `Content Type` populated.

- [ ] **AC 3:** Given `--include-transcript` flag, when a tagged segment's `video_id` has a matching `data/transcripts/{video_id}.json`, then the segment entry in the output `.md` includes the reconstructed transcript text for that segment's time window.

- [ ] **AC 4:** Given a speaker file exceeds 450,000 words, when the script runs, then a `[WARN]` message is printed with the file name, word count, and a suggestion to split.

- [ ] **AC 5:** Given a `data/transcripts/{video_id}.json` is missing, when `--include-transcript` is used for that lecture, then the script does not crash — it silently omits transcript text and exports all other metadata fields normally.

- [ ] **AC 6:** Given the script is run twice on the same `data/tagged/` contents, then output files are identical on both runs (idempotent).

---

## Additional Context

### Usage

```bash
# Default: tagged metadata only (fastest, smallest files)
python scripts/05_export_notebooklm.py

# Include full transcript text per segment (recommended — richer citations)
python scripts/05_export_notebooklm.py --include-transcript

# Custom output directory
python scripts/05_export_notebooklm.py --output-dir exports/notebooklm_2026-03
```

### Manual Upload Workflow (NotebookLM)

1. Run `python scripts/05_export_notebooklm.py`
2. Open [notebooklm.google.com](https://notebooklm.google.com) → New Notebook
3. Add sources → Upload files → select `.md` files from `data/notebooklm/`
4. NotebookLM indexes sources; conversational Q&A is available immediately
5. Share notebook publicly (optional) for devotee access — similar to deltaflow.com approach

### When to Re-Export

Re-run `05_export_notebooklm.py` after:
- A new pipeline batch completes (new lectures tagged and added to `data/tagged/`)
- A new speaker is added to `config/speakers.yaml` and their lectures processed
- NotebookLM sources should be refreshed (delete old sources, re-upload new `.md` files)

### Notes

- **No RunPod needed:** This script runs locally. `git pull` the tagged data from GitHub, then run the export.
- **File size estimate (tagged-only, no transcript):** ~200–400 KB per speaker with 100 lectures. Well within NotebookLM limits.
- **File size estimate (with transcript):** ~2–5 MB per speaker with 100 lectures. May approach the 500k-word limit for prolific speakers — `--word-limit` warning will catch this.
- **NotebookLM citation quality:** The structured segment format (Themes + Verse References + Content Type + Key Quote + Summary + transcript text) gives NotebookLM clear anchors for precise, segment-level citations. Users will see grounded answers with specific passage references, not vague "somewhere in this lecture" responses.
- **Future:** If NotebookLM ever exposes an API, upload can be automated as a pipeline stage. Until then, manual upload is the workflow.
