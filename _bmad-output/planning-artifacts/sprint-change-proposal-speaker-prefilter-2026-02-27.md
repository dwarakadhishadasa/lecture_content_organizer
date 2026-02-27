# Sprint Change Proposal — Speaker Pre-Filter at Download Time
**Date:** 2026-02-27
**Author:** Dwarakadas
**Scope Classification:** Minor
**Status:** Approved

---

## Section 1: Issue Summary

**Problem Statement:**
The current pipeline design resolves speaker identity in `02_transcribe.py`, *after* audio has already been downloaded by `01_download.py`. This creates two problems:

1. **Wasted resources**: Audio files for videos with unresolvable speakers are downloaded, consuming bandwidth and RunPod disk, only to be deleted immediately in `02_transcribe.py`.
2. **Broken retry workflow**: Once an audio file is downloaded by `01_download.py`, its video ID is added to `archive.txt`. If the speaker cannot be resolved, the audio is deleted — but the video ID remains in `archive.txt`. Re-running the pipeline after adding the missing speaker to `speakers.yaml` will not re-download the audio (archive prevents it), requiring manual archive.txt surgery.

**Discovery Context:**
Identified during design review of Tech Spec Task 5/6, before any implementation began. The issue was surfaced when considering the full lifecycle: download → unresolved skip → delete audio → add speaker → re-run → stuck.

**Evidence:**
- Task 6 spec: *"If `speaker is None`: delete audio file → continue"* — audio has already been downloaded by this point
- Testing Strategy Step 0: *"Add missing speakers to `speakers.yaml` and re-run pipeline"* — this re-run would be blocked by archive.txt for already-attempted videos
- BATCH_SIZE=50 downloads 50 audio files per cycle; if 10–20% have unresolvable speakers, that's 5–10 unnecessary downloads per batch

---

## Section 2: Impact Analysis

**Epic Impact:** N/A — No epics exist (pre-implementation phase)

**Story Impact:** N/A — No stories exist

**Artifact Conflicts:**

| Artifact | Impact | Action Required |
|----------|--------|-----------------|
| `scripts/01_download.py` (Task 5) | Does not load `speakers.yaml`; no speaker filtering | Add `match_filter` callable that resolves speaker at download time |
| `scripts/02_transcribe.py` (Task 6) | Primary speaker resolution point | Demote to safety net only; add clarifying comment; no code logic change |
| `tech-spec-wip.md` — AC 3c | Describes unresolved-speaker skip in `02_transcribe.py` | Update to reflect pre-filtering in `01_download.py` as the primary mechanism |
| `tech-spec-wip.md` — code_patterns | No mention of match_filter pre-filtering | Add pattern entry |
| `tech-spec-wip.md` — Testing Strategy | Re-run after adding speaker described as working | Clarify it works because unresolved videos stay out of archive.txt |
| `tech-spec-wip.md` — Notes (pre-mortem) | Speaker false negatives mitigated by `unresolved_speakers.txt` + re-run | Clarify re-run works automatically due to archive.txt exclusion |

**Technical Impact:**
The yt-dlp Python API supports a `match_filter` option that accepts a callable `(info_dict, *, incomplete) -> str | None`. Returning a non-None string causes yt-dlp to skip the video entirely — no audio download, no `archive.txt` entry. The callable has access to `info_dict['title']` which is the exact input needed by `resolve_speaker()`.

Key consequence: videos filtered out by `match_filter` are **not counted** against `max_downloads`. So `BATCH_SIZE=50` continues to mean 50 actually-downloaded (resolved-speaker) lectures per cycle — a minor benefit over the current behavior where 5–10 of 50 "slots" were wasted on unresolvable videos.

---

## Section 3: Recommended Approach

**Selected Path:** Direct Adjustment (Option 1)

**Rationale:**
- No implementation exists to roll back — pure tech-spec update
- yt-dlp's `match_filter` callable is a well-established API feature; risk is minimal
- Fixes both the efficiency problem AND the broken retry workflow simultaneously
- Simplifies `02_transcribe.py`'s responsibility (speaker resolution becomes a safety net, not primary logic)
- All changes contained within Task 5 description; pipeline architecture unchanged
- Effort: **Low** | Risk: **Low** | Timeline impact: **None**

---

## Section 4: Detailed Change Proposals

### Proposal 1 — `scripts/01_download.py` (Task 5) — Add `match_filter`

```
OLD (Task 5, Action step 3 opts):
  {
      'format': 'bestaudio/best',
      'postprocessors': [...],
      'outtmpl': 'data/audio/%(id)s.%(ext)s',
      'download_archive': 'archive.txt',
      'writeinfojson': True,
      'max_downloads': batch_size,
      'sleep_interval': 15,
      'max_sleep_interval': 45,
      'sleep_requests': 3,
      'ignoreerrors': True,
  }

  (No speaker loading at startup)

NEW (Task 5, Action):
  # Step 0 (add before batch size parse):
  from scripts.utils.resolve_speaker import load_speakers, resolve_speaker
  speakers = load_speakers("config/speakers.yaml")

  def speaker_match_filter(info_dict, *, incomplete):
      title = info_dict.get('title', '')
      if resolve_speaker(title, speakers) is None:
          return "Speaker unresolved — skipping download"
      return None

  # opts dict (add match_filter key):
  {
      'format': 'bestaudio/best',
      'postprocessors': [...],
      'outtmpl': 'data/audio/%(id)s.%(ext)s',
      'download_archive': 'archive.txt',
      'writeinfojson': True,
      'max_downloads': batch_size,
      'match_filter': speaker_match_filter,   # ← NEW
      'sleep_interval': 15,
      'max_sleep_interval': 45,
      'sleep_requests': 3,
      'ignoreerrors': True,
  }
```

**Rationale:** Speaker is resolved at download time. Videos that fail return a skip reason string — yt-dlp logs it and moves on. No audio is downloaded; no archive.txt entry is created. The unresolved title is still logged to `data/unresolved_speakers.txt` via `resolve_speaker()`.

**Important behaviour note:** `match_filter`-skipped videos do NOT count against `max_downloads`. BATCH_SIZE=50 means 50 successfully-downloaded lectures, not "50 attempts including filtered ones".

---

### Proposal 2 — `scripts/02_transcribe.py` (Task 6) — Safety Net Clarification

```
OLD (Task 6 intent):
  Primary speaker resolution point:
  "Resolve speaker: speaker = resolve_speaker(title, speakers)
   If speaker is None: skip + delete audio"

NEW (Task 6 intent):
  Safety net only:
  "Resolve speaker: speaker = resolve_speaker(title, speakers)
   If speaker is None: skip + delete audio  # Safety net — should not trigger
   # under normal operation since 01_download.py pre-filters via match_filter.
   # Can trigger if speakers.yaml was modified between 01 and 02 stages."

  Code logic: UNCHANGED
  (The safety net remains — defense in depth is valuable)
```

**Rationale:** No code change. The task description is updated to clarify the path is a safety net, not primary logic. This prevents a future implementer from removing it thinking it's redundant.

---

### Proposal 3 — Tech Spec AC 3c — Update Acceptance Criterion

```
OLD:
  AC 3c: Given resolve_speaker() returns None for a video, when 02_transcribe.py
  processes that video, then no transcript JSON is created, the audio file is deleted,
  and the pipeline continues to the next video without error.

NEW:
  AC 3c: Given a video title cannot be resolved to a canonical speaker in
  config/speakers.yaml:

  PRIMARY (01_download.py match_filter):
  - yt-dlp skips the video before any audio is downloaded
  - The video ID is NOT added to archive.txt
  - The unresolved title is logged to data/unresolved_speakers.txt
  - The video will be automatically retried on the next pipeline run if/when its
    speaker is added to speakers.yaml

  SAFETY NET (02_transcribe.py):
  - If an unresolved-speaker audio file somehow reaches 02_transcribe.py
    (e.g., speakers.yaml was modified between stages), no transcript JSON is
    created, the audio file is deleted, and the pipeline continues without error.
```

---

### Proposal 4 — Tech Spec `code_patterns` — Add Pre-Filter Pattern

```
ADD to code_patterns:
  - Speaker pre-filter in 01_download.py via yt-dlp match_filter callable:
    resolve_speaker() runs at download time against video title;
    unresolved speakers skipped BEFORE audio download;
    NOT added to archive.txt — enables automatic retry after adding speaker to speakers.yaml;
    match_filter-skipped videos do NOT count toward max_downloads/BATCH_SIZE
```

---

### Proposal 5 — Tech Spec Testing Strategy — Clarify Retry Workflow

```
OLD (Step 0 — Speaker resolution dry run):
  "Inspect data/unresolved_speakers.txt for false negatives"
  "Add missing speakers to speakers.yaml and re-run pipeline"

ADD clarification:
  "Automatic retry: Since unresolved videos are never added to archive.txt
  (filtered at download time by match_filter), re-running run_pipeline.sh
  after adding missing speakers to speakers.yaml will automatically discover
  and download the previously-skipped videos — no manual archive.txt editing required."
```

---

### Proposal 6 — Tech Spec Pre-mortem Notes — Update Speaker False Negatives

```
OLD:
  "Speaker resolution false negatives: ... Mitigation: Step 0 dry run before
  full bulk execution; inspect data/unresolved_speakers.txt after any run;
  add missing names and re-run (idempotent)."

UPDATE:
  "Speaker resolution false negatives: ... Mitigation: Step 0 dry run before
  full bulk execution; inspect data/unresolved_speakers.txt after any run;
  add missing names to speakers.yaml and re-run — unresolved videos were never
  added to archive.txt (filtered at download time), so they are retried
  automatically on the next pipeline run without any manual intervention."
```

---

## Section 5: Implementation Handoff

**Change Scope Classification:** Minor

**Handoff:** Development team (Dwarakadas) — direct tech-spec update and implementation.

**Responsibilities:**
1. Update Task 5 (`scripts/01_download.py`) description to add `match_filter` callable and `speakers.yaml` loading at startup (Proposal 1)
2. Update Task 6 (`scripts/02_transcribe.py`) description to clarify safety-net role — no code logic change (Proposal 2)
3. Update AC 3c to reflect pre-filtering as primary mechanism (Proposal 3)
4. Add `match_filter` pattern to `code_patterns` frontmatter (Proposal 4)
5. Update Testing Strategy Step 0 and Notes pre-mortem entry (Proposals 5 & 6)

**Success Criteria:**
- `01_download.py` loads `config/speakers.yaml` at startup alongside `config/channels.yaml`
- `01_download.py` passes `match_filter` callable to yt-dlp opts; callable calls `resolve_speaker(title, speakers)`
- Videos with unresolvable speakers are never downloaded; their IDs are never written to `archive.txt`
- Unresolved video titles continue to be logged to `data/unresolved_speakers.txt` (via `resolve_speaker()`)
- After adding a speaker to `speakers.yaml`, re-running `run_pipeline.sh` automatically downloads previously-skipped videos for that speaker — no manual archive.txt editing required
- `02_transcribe.py` speaker-resolution safety-net code remains in place (unchanged), documented as defense-in-depth
- `BATCH_SIZE=50` continues to mean 50 fully-processed (resolved-speaker) downloads per cycle
