# Sprint Change Proposal — Speaker Attribution Model
**Date:** 2026-02-27
**Author:** Dwarakadas
**Scope Classification:** Minor
**Status:** Approved

---

## Section 1: Issue Summary

**Problem Statement:**
The tech-spec (`tech-spec-wip.md`) incorrectly assumes that one YouTube channel maps to one speaker, and derives the speaker name from yt-dlp's `uploader` field (the channel owner). In reality, channels contain multiple playlists with lectures by various speakers. The actual speaker of each lecture must be extracted from the video title and matched against a canonical speaker list.

**Discovery Context:**
Identified during tech-spec review, before any implementation began. The assumption was embedded in Task 6 (`02_transcribe.py`) which extracted `uploader` from info.json as the speaker, and in `config/channels.yaml` which stored a `name` field per channel entry.

**Evidence:**
- Task 6 spec: *"Extract `title`, `uploader`, `channel` from info.json"* — `uploader` = channel owner, not lecture speaker
- Task 7 spec: *"Speaker: {speaker}"* passed to Gemini — would be wrong channel name for multi-speaker playlists
- Task 8 spec: Speaker Notion property populated from the same wrong value
- `config/channels.yaml` structure: `{name: "Speaker Name", url: "..."}` — incorrectly conflates channel with speaker

**Title format examples (actual data):**
```
"Core of Spiritual Life | Sri Vasudev Keshava Dasa | SB 6.12.22 | 05.02.2026"
"2015-11-13 | SB 3.18.1 | HG Amitasana Dasa"
```
Speaker position within `|`-delimited segments is variable — a position-based approach is insufficient.

---

## Section 2: Impact Analysis

**Epic Impact:** N/A — No epics exist (pre-implementation)

**Story Impact:** N/A — No stories exist

**Artifact Conflicts:**

| Artifact | Impact | Action Required |
|----------|--------|-----------------|
| `config/channels.yaml` | `name:` field incorrectly ties speaker to channel | Remove `name:` field; use `channels:` key with URLs only |
| `config/speakers.yaml` | Does not exist | Create new file with canonical speaker names |
| `scripts/utils/resolve_speaker.py` | Does not exist | Create new utility: title parsing + fuzzy matching |
| `scripts/utils/__init__.py` | Does not exist | Create empty package init file |
| `scripts/02_transcribe.py` | Uses `uploader` for speaker | Use `resolve_speaker(title, speakers)` instead |
| `scripts/03_tag.py` | Assumes speaker is always non-null | Handle `null` speaker gracefully in Gemini prompt |
| `scripts/04_upload_notion.py` | Passes speaker unconditionally to Notion Select | Omit Speaker property entirely when null |
| `requirements.txt` | Missing `rapidfuzz` | Add `rapidfuzz>=3.0.0` |
| `data/unresolved_speakers.txt` | Does not exist | New runtime log artifact; add to `.gitignore` |

**Technical Impact:**
The pipeline architecture (4 scripts + shell orchestrator) is unchanged. The change introduces one new utility module and one new config file. All speaker attribution flows through `resolve_speaker()` — a single point of control. The fix must be in place before any implementation begins; it has no runtime dependencies on existing code.

---

## Section 3: Recommended Approach

**Selected Path:** Direct Adjustment (Option 1)

**Rationale:**
- No implementation exists to roll back — this is a pure tech-spec correction
- The underlying pipeline architecture, technology choices, and MVP goal are unchanged
- All changes are contained within the tech-spec and implementable directly by the dev team
- The new `resolve_speaker()` utility is self-contained and testable in isolation
- Effort: **Medium** | Risk: **Low** | Timeline impact: **None**

**Speaker Resolution Algorithm:**
```python
def resolve_speaker(title, canonical, threshold=85, log_path="data/unresolved_speakers.txt"):
    segments = [s.strip() for s in title.split("|")]
    best_score, best_match = 0, None
    for segment in segments:
        result = process.extractOne(segment, canonical, scorer=fuzz.WRatio)
        if result and result[1] > best_score:
            best_score, best_match = result[1], result[0]
    if best_score >= threshold:
        return best_match
    with open(log_path, "a") as f:
        f.write(f"{title}\n")
    return None
```

All `|`-delimited segments are tested against the canonical list. Date strings, scripture references, and lecture topic titles score near 0 against speaker names — the correct segment wins by score, regardless of position.

---

## Section 4: Detailed Change Proposals

### Proposal 1 — `config/channels.yaml` restructure

```
OLD:
  speakers:
    - name: "Speaker Name"
      url: "https://www.youtube.com/@ChannelHandle"

NEW:
  channels:
    - url: "https://www.youtube.com/@ChannelHandle"
    - url: "https://www.youtube.com/@AnotherChannel"
```

Rationale: Channels are not speaker-tied. Speaker identity comes from title parsing.
Impact on `01_download.py`: Load `channels[*].url` instead of `speakers[*].url`. Download logic otherwise unchanged.

---

### Proposal 2 — New `config/speakers.yaml`

```
NEW:
  speakers:
    - "Speaker Name Exactly As Wanted In Notion"
    - "Another Speaker Name"
    # Populate before first pipeline run
    # Fuzzy matching handles title variants (honorifics, abbreviations, typos)
```

Rationale: Ground truth canonical name list. Names appear verbatim in Notion `Speaker` property.
Setup: One-time manual population before pipeline execution.

---

### Proposal 3 — New `scripts/utils/resolve_speaker.py`

```python
NEW:
  import yaml
  from rapidfuzz import process, fuzz

  def load_speakers(path: str = "config/speakers.yaml") -> list[str]:
      with open(path) as f:
          return yaml.safe_load(f)["speakers"]

  def resolve_speaker(
      title: str,
      canonical: list[str],
      threshold: int = 85,
      log_path: str = "data/unresolved_speakers.txt"
  ) -> str | None:
      segments = [s.strip() for s in title.split("|")]
      best_score, best_match = 0, None
      for segment in segments:
          result = process.extractOne(segment, canonical, scorer=fuzz.WRatio)
          if result and result[1] > best_score:
              best_score, best_match = result[1], result[0]
      if best_score >= threshold:
          return best_match
      with open(log_path, "a") as f:
          f.write(f"{title}\n")
      return None
```

Rationale: Centralized speaker resolution. `fuzz.WRatio` handles honorific variants. Threshold=85 tunable after dry run.
Also required: `scripts/utils/__init__.py` (empty).

---

### Proposal 4 — `scripts/02_transcribe.py` speaker attribution fix

```
OLD:
  - Extract title, uploader, channel from info.json
  - "speaker": uploader value in output JSON

NEW:
  - At startup: speakers = load_speakers("config/speakers.yaml")
  - Extract title from info.json only (uploader/channel unused)
  - For each video: speaker = resolve_speaker(title, speakers)
  - If None → print "[N/TOTAL] Skipping {video_id}: speaker unresolved"
              → log title to data/unresolved_speakers.txt
              → continue  (no transcript JSON created, audio deleted)
  - If resolved → "speaker": canonical name in output JSON, proceed normally
```

Rationale: `uploader` = channel owner, not lecture speaker. Unresolved videos are skipped at the earliest stage — no wasted GPU time on transcription, no null values anywhere downstream. The `unresolved_speakers.txt` log enables a review-and-retry workflow: add missing speakers to `speakers.yaml`, re-run pipeline, idempotency ensures already-processed videos are skipped.

---

### Proposal 5 — `scripts/03_tag.py`

**No change required.** Videos with unresolved speakers never produce a transcript JSON, so `03_tag.py` never encounters a null speaker.

---

### Proposal 6 — `scripts/04_upload_notion.py`

**No change required.** Videos with unresolved speakers have no tagged JSON, so `04_upload_notion.py` never encounters a null speaker.

---

### Proposal 7 — `requirements.txt`

```
OLD:
  (rapidfuzz not present)

NEW:
  rapidfuzz>=3.0.0
```

Rationale: Required by `resolve_speaker.py`. Significantly more accurate and faster than `difflib` for this use case.

---

### Proposal 8 — Tech-spec scaffold and task list restructure

**Frontmatter `files_to_modify` additions:**
```
+ config/speakers.yaml
+ scripts/utils/__init__.py
+ scripts/utils/resolve_speaker.py
```

**Task 1 scaffold additions:**
```
+ Directory: scripts/utils/
+ File: scripts/utils/__init__.py (empty)
+ .gitignore: also exclude data/unresolved_speakers.txt
```

**New tasks inserted after Task 2:**
```
Task 2a: Create config/speakers.yaml
  - Create YAML with list of canonical speaker names (exact Notion display form)
  - Populate all known speakers before first pipeline run

Task 2b: Create scripts/utils/resolve_speaker.py
  - Implement load_speakers() and resolve_speaker() as specified
  - Tune threshold after single-speaker dry run by inspecting
    data/unresolved_speakers.txt for false negatives/positives
```

---

## Section 5: Implementation Handoff

**Change Scope Classification:** Minor

**Handoff:** Development team (Dwarakadas) — direct implementation.

**Responsibilities:**
1. Update `config/channels.yaml` to new structure (Proposal 1)
2. Create and populate `config/speakers.yaml` with all canonical names (Proposal 2)
3. Create `scripts/utils/__init__.py` and `scripts/utils/resolve_speaker.py` (Proposal 3)
4. Update `scripts/02_transcribe.py` to skip unresolved speakers (Proposal 4)
5. Add `rapidfuzz>=3.0.0` to `requirements.txt` (Proposal 7)
6. Update tech-spec scaffold section and task list (Proposal 8)

**Success Criteria:**
- No script references `info_json["uploader"]` for speaker attribution
- `config/speakers.yaml` exists and is populated before pipeline execution
- `resolve_speaker()` correctly identifies speakers from both title formats tested
- Videos with unresolvable speakers are skipped entirely (no transcription, no tagging, no Notion row)
- Skipped video titles logged to `data/unresolved_speakers.txt` for manual review
- Workflow for missed speakers: inspect `unresolved_speakers.txt` → add names to `speakers.yaml` → re-run pipeline (idempotent; already-transcribed videos skip automatically)
- No null speaker values anywhere in the pipeline
- After single-speaker dry run, `data/unresolved_speakers.txt` is inspected and threshold adjusted if needed
