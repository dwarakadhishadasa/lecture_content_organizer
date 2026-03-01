# Sprint Change Proposal — NotebookLM Export Simplification
Generated: 2026-03-02 | Scope: Minor | Status: Approved and Implemented

---

## 1. Issue Summary

The `05_export_notebooklm.py` implementation diverged from the original tech spec
(`tech-spec-notebooklm-export.md`) in two directions:

**Scope creep (feature additions):** Five CLI flags were added beyond the original spec —
`--all-speakers`, `--circle-fit`, `--content-type`, `--split-by-content-type`, and
`--split-speakers`. While technically valid, these flags added complexity and required
the user to remember invocation patterns for a script that should just run.

**Missing spec item:** The segment-level `timestamp_url` was not rendered in the markdown
output despite being the primary citation mechanism for NotebookLM queries.

**Data model drift:** The tagged JSONs (`data/tagged/*.json`) now include `transcript`
embedded per segment (added by the Vertex AI batch tagging stage). The original spec
assumed transcript text would be reconstructed from `data/transcripts/*.json`. The
implementation partially handled this (`seg.get("transcript")` check) but retained the
file-based fallback unnecessarily.

## 2. Impact Analysis

- **PRD / Epics / Architecture**: None — no formal planning docs exist for this project.
- **Tech spec**: `tech-spec-notebooklm-export.md` is stale; reflects the original design.
- **Output quality**: Timestamp URL was missing from output (citation gap). Circle fit was
  displayed as raw integers (1,2,3,4) with no human-readable meaning.
- **Usability**: Users had to know which flags to combine for a complete export.

## 3. Recommended Approach — Direct Adjustment (Implemented)

Rewrite `05_export_notebooklm.py` as a zero-flag script with all settings hardcoded.
All "options" from the old design are now defaults:

| Setting | Old (flagged) | New (hardcoded) |
|---|---|---|
| Include transcript | `--include-transcript` | Always on (`seg.get("transcript")`) |
| Output directory | `--output-dir data/notebooklm` | `Path("data/notebooklm")` constant |
| All-speakers export | `--all-speakers` | Always generated |
| Circle fit display | Raw integers `[1, 2]` | `"Full-time devotees, Congregation / volunteers"` |
| Timestamp URL | Missing from output | `**Timestamp:** {url}` in every segment |
| Auto-split | `--split-speakers` flag | Always on (greedy-pack, 450k word limit) |
| Content type | `--content-type` filter | Always shown, never filtered |
| Stale file cleanup | Manual | Auto-deletes `data/notebooklm/*.md` before run |

**NotebookLM limit handling (new):** Large speakers (e.g. HH Stoka Krishna Swami at ~1.08M
words with transcripts) caused the old greedy-pack to overflow a single file. The new script
falls back to lecture-level packing for any speaker whose block alone exceeds 450k words.

## 4. Output After Implementation

```
python scripts/05_export_notebooklm.py

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

All 17 files are under the 450,000-word limit and ready for direct upload.

## 5. Implementation Handoff

- **Scope**: Minor — implemented directly.
- **Artifacts modified**: `scripts/05_export_notebooklm.py` (complete rewrite)
- **Artifacts to update**: `_bmad-output/implementation-artifacts/tech-spec-notebooklm-export.md`
  (stale — should be updated to reflect zero-flag design if needed for future reference)
- **Next step**: Upload `data/notebooklm/*.md` to NotebookLM.
