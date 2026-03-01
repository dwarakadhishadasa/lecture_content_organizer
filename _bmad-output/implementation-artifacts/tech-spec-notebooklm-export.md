---
title: 'NotebookLM Export Script'
slug: 'notebooklm-export'
created: '2026-02-28'
updated: '2026-03-02'
status: 'Completed'
stepsCompleted: [1]
tech_stack:
  - Python 3.10+
  - No new dependencies (uses stdlib: json, pathlib, re, datetime)
files_to_modify: []
files_to_create:
  - scripts/05_export_notebooklm.py
code_patterns:
  - Standalone script — NOT part of run_pipeline.sh; run on-demand locally
  - Reads from data/tagged/*.json (permanent pipeline artifacts)
  - Transcript text embedded in tagged segments — no data/transcripts/ lookup needed
  - Outputs per-speaker and all-speakers .md files to data/notebooklm/
  - Auto-splits files exceeding 450k words (greedy-pack by speaker, fallback to lecture)
  - Idempotent — deletes all data/notebooklm/*.md before regenerating
test_patterns:
  - No formal test framework (personal tooling)
  - Run script, inspect output word counts in terminal
  - Open generated .md in editor before uploading to NotebookLM
---

# Tech-Spec: NotebookLM Export Script

**Created:** 2026-02-28 | **Updated:** 2026-03-02

## Overview

### Problem Statement

The existing pipeline produces ~10,000 tagged segments across ~741 lectures stored in
`data/tagged/*.json`. This rich corpus is accessible only via Notion (structured
filter/search). There is no way to ask a conversational question like *"What did the
speakers say about surrender across all lectures?"* and get a synthesized, grounded
answer with precise citations. A second application is needed that enables this
conversational mode without replacing the Notion-based pipeline.

### Solution

A standalone Python script (`scripts/05_export_notebooklm.py`) that reads the existing
tagged JSON artifacts and generates clean, structured Markdown files ready for direct
upload to NotebookLM. The script requires no arguments — run it and upload the output.

Two sets of files are produced:
- **Per-speaker** (`{speaker}.md`) — for deep dives into one teacher's corpus
- **All-speakers** (`all_speakers_N.md`) — for cross-speaker conversational queries

Files are automatically split to stay under NotebookLM's ~500,000-word per-source limit.

### Scope

**In Scope:**
- Read `data/tagged/*.json` to build export documents
- Per-speaker exports: one `.md` file per speaker (auto-split if over word limit)
- All-speakers export: greedy-packed files covering all speakers (auto-split)
- Transcript text from embedded `segment["transcript"]` field in tagged JSONs
- Timestamp URL in every segment — links directly to the exact YouTube moment
- Circle fit rendered as human-readable audience labels, not raw integers
- Automatic cleanup of stale output files before each run
- All output files ready for direct upload to NotebookLM with no post-processing

**Out of Scope:**
- CLI flags or configuration — all settings are hardcoded constants
- Filtering by circle fit, content type, or speaker — full corpus is always exported
- Automated upload to NotebookLM (no public API exists — upload is manual)
- Any changes to `run_pipeline.sh` or other pipeline scripts

---

## Context for Development

### Where This Fits

```
data/tagged/*.json  ──→  run_pipeline.sh  ──→  Notion          (App 1: structured index)
                     └→  05_export_notebooklm.py  ──→  data/notebooklm/*.md  ──→  NotebookLM  (App 2: conversational)
```

The export script is a **pure consumer** — reads existing artifacts, writes new files.
Nothing upstream is modified. Run locally after `git pull` of the tagged data.

### Key Constraints

- **NotebookLM source limit:** ~500,000 words per source document. With transcripts
  embedded, the full corpus is ~2.5M words. Files must be auto-split to fit.
- **No new dependencies:** Uses only Python stdlib (`json`, `pathlib`, `re`, `datetime`).
- **Transcript is embedded:** Tagged JSONs store `segment["transcript"]` directly —
  no reconstruction from `data/transcripts/*.json` is needed.
- **NotebookLM sources per notebook:** Up to 50. Current output is 17 files (well under).

### Key Constants

```python
WORD_LIMIT = 450_000        # Safe margin below NotebookLM's ~500k per-source limit
OUTPUT_DIR = Path("data/notebooklm")

CIRCLE_FIT_LABELS = {
    1: "Full-time devotees",
    2: "Congregation / volunteers",
    3: "Newcomers",
    4: "General public",
}
```

### Output Format

Every segment renders all fields. Example per-speaker segment:

```markdown
## Lecture Title
**Speaker:** HG Madhu Pandit Dasa

### karma, free will, soul
**Timestamp:** https://youtu.be/VIDEO_ID?t=0
**Verse References:** BG 2.47
**Content Type:** philosophy
**Audience:** Full-time devotees, Congregation / volunteers
**Key Quote:** "The most impactful sentence from this segment."
**Summary:** One sentence describing what this segment covers.

{full transcript text of the segment}

---
```

For all-speakers files, lecture headings are `###` and segment headings are `####`
to maintain proper hierarchy under the `## {Speaker}` section heading.

This structure ensures:
- NotebookLM cites at the segment level with a direct YouTube timestamp link
- Audience labels clarify who the content is aimed at
- Verse references are scannable for scripture-based queries
- Full transcript gives NotebookLM the spoken words for grounded citations

---

## Implementation

### Script Structure

```
05_export_notebooklm.py
│
├── slugify(name)                    — speaker name → safe filename
├── format_segment(seg, heading)     — one segment → markdown block
├── format_lecture(lecture, ...)     — all segments of a lecture → markdown
├── greedy_pack(blocks)              — split list of (label, text) into ≤ WORD_LIMIT bins
├── write_packed(blocks, ...)        — pack and write numbered .md files
└── main()
    ├── load data/tagged/*.json, group by speaker
    ├── delete stale data/notebooklm/*.md
    ├── per-speaker loop → write_packed (by lecture)
    └── all-speakers loop → write_packed (by speaker; lecture-level fallback for large speakers)
```

### Auto-Split Logic

**Per-speaker:** Lectures are the packable unit. `greedy_pack` fills files up to
`WORD_LIMIT`, starting a new file when a lecture would overflow. Result:
`{speaker}.md` or `{speaker}_1.md`, `{speaker}_2.md`, …

**All-speakers:** Each speaker's entire content is built as one block. If that block
fits within `WORD_LIMIT`, it goes in as a speaker-level block. If a single speaker's
content exceeds `WORD_LIMIT` (e.g. HH Stoka Krishna Swami), it is broken into
per-lecture blocks so `greedy_pack` can distribute them across files. Result:
`all_speakers.md` or `all_speakers_1.md`, `all_speakers_2.md`, …

### Task

- [x] **Task 1: Implement `scripts/05_export_notebooklm.py`**
  - Status: Complete (2026-03-02)
  - No argparse — zero-argument script
  - `format_segment()` renders all 7 fields: Timestamp, Verse References, Content Type,
    Audience (circle-fit labels), Key Quote, Summary, and transcript text
  - `greedy_pack()` returns list of bins; `write_packed()` writes numbered files
  - `main()` clears stale output, runs per-speaker exports, then all-speakers export

---

## Acceptance Criteria

- [x] **AC 1:** Given `data/tagged/` contains N tagged lectures for M speakers, when
  `python scripts/05_export_notebooklm.py` runs, then `.md` files are created in
  `data/notebooklm/` covering all M speakers (one or more files per speaker).

- [x] **AC 2:** Given a speaker with 50 lectures (each ~6 segments), when exported,
  then every segment entry contains Timestamp URL, Verse References, Content Type,
  Audience labels, Key Quote, Summary, and transcript text.

- [x] **AC 3:** Given tagged segments with `"transcript"` field, when the script runs,
  then each segment in the output `.md` includes the full transcript text.

- [x] **AC 4:** Given a speaker whose content exceeds 450,000 words, when the script
  runs, then that speaker's output is automatically split into numbered files
  (`{speaker}_1.md`, `{speaker}_2.md`, …), each under the word limit.

- [x] **AC 5:** Given the all-speakers corpus exceeds 450,000 words, when the script
  runs, then `all_speakers_1.md`, `all_speakers_2.md`, … are produced, each under the
  word limit and all together covering all speakers.

- [x] **AC 6:** Given the script is run twice on the same `data/tagged/` contents,
  then output files are identical on both runs (idempotent — stale files deleted first).

- [x] **AC 7:** Given circle_fit values `[1, 2]` on a segment, when exported, then the
  output reads `**Audience:** Full-time devotees, Congregation / volunteers` — not
  raw integers.

---

## Additional Context

### Usage

```bash
# Run from project root (local machine after git pull):
python scripts/05_export_notebooklm.py
```

Output (as of 2026-03-02, 741 tagged lectures, 11 speakers, with transcripts):

```
[05_export] Per-speaker (11 speakers):
  hdg_srila_prabhupada.md: ~68,675 words
  hg_amitasana_dasa.md: ~45,587 words
  hg_atma_tattva_dasa.md: ~10,989 words
  hg_chanchalapathi_dasa.md: ~68,763 words
  hg_jai_chaitanya_dasa.md: ~53,195 words
  hg_madhu_pandit_dasa.md: ~173,878 words
  hg_satya_gaura_chandra_dasa.md: ~24,898 words
  hg_suvyakta_narasimha_dasa.md: ~196,767 words
  hg_vasudev_keshav_dasa.md: ~17,487 words
  hh_stoka_krishna_maharaj.md: ~28,482 words
  hh_stoka_krishna_swami_1.md: ~448,278 words
  hh_stoka_krishna_swami_2.md: ~447,854 words
  hh_stoka_krishna_swami_3.md: ~185,455 words
  → auto-split into 3 files (each ≤ 450,000 words)

[05_export] All-speakers export:
  all_speakers_1.md: ~446,018 words
  all_speakers_2.md: ~449,104 words
  all_speakers_3.md: ~447,972 words
  all_speakers_4.md: ~429,787 words
  → auto-split into 4 files (each ≤ 450,000 words)

[05_export] Done. 17 files written to data/notebooklm/
  Upload all .md files to NotebookLM (limit: 450,000 words/source, 50 sources/notebook).
```

### Manual Upload Workflow (NotebookLM)

1. Run `python scripts/05_export_notebooklm.py`
2. Open notebooklm.google.com → New Notebook
3. Add sources → Upload files → select all `.md` files from `data/notebooklm/`
4. NotebookLM indexes sources; conversational Q&A is available immediately
5. Share notebook publicly (optional) for devotee access

### When to Re-Export

Re-run after:
- A new pipeline batch completes (new lectures tagged in `data/tagged/`)
- A new speaker is added and their lectures processed
- Refreshing NotebookLM sources (delete old sources, re-upload new `.md` files)

### Notes

- **No RunPod needed:** Runs locally. `git pull` the tagged data, then run the export.
- **File sizes (with transcripts, as of 2026-03-02):** 61 KB–6.3 MB per speaker before
  splitting; all output files are under 450k words after auto-split.
- **NotebookLM citation quality:** Every segment has a direct YouTube timestamp link
  (`**Timestamp:** https://youtu.be/{id}?t={seconds}`). NotebookLM can cite the exact
  moment in the lecture, not just the lecture title.
- **Future:** If NotebookLM ever exposes an upload API, the output files are already
  formatted correctly — automation would only need to POST them.
