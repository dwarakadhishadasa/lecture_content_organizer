# Sprint Change Proposal — Per-Batch Git Checkpoint Commits
**Date:** 2026-02-27
**Author:** Dwarakadas
**Scope Classification:** Minor
**Status:** Approved

---

## Section 1: Issue Summary

**Problem Statement:**
Two related issues:

1. **No per-batch checkpoint**: `run_pipeline.sh` commits to GitHub only at the very end (Phase 4), after all downloading, transcribing, tagging, and uploading complete. If the RunPod pod dies at any point before Phase 4, all progress is lost — `archive.txt`, transcript JSONs, and tagged JSONs are not on GitHub. A pod death at batch 15 of 20 means 15 batches' worth of GPU transcription time and download bandwidth must be redone from scratch.

2. **Overly broad `.gitignore`**: `.gitignore` excludes all of `data/`. This was originally intended to keep large audio files out of git, but it also blocks the valuable output files (`data/transcripts/*.json`, `data/tagged/*.json`, `data/unresolved_speakers.txt`, `data/uploaded.txt`). As a result, Phase 4's `git add data/...` commands are all silent no-ops — those files were never actually being committed.

**Root cause of issue 2:**
The concern was audio file size (`data/audio/*.mp3` — 50–200MB each). But audio files are already deleted immediately after transcription by `02_transcribe.py`. So `data/audio/` is always nearly empty by the time any git operation runs. Only `data/audio/` needs to be gitignored.

**Discovery Context:**
Identified during post-implementation review of `run_pipeline.sh`. The checkpoint gap and gitignore conflict were found together when tracing what Phase 4 actually commits.

**Evidence:**
- `.gitignore` line 1: `data/` — blocks all subdirectories
- `run_pipeline.sh` lines 52–55: `git add data/...` — all silent no-ops due to gitignore
- `run_pipeline.sh` lines 16–38: the download-transcribe while loop — no git checkpoint
- `data/audio/` is empty during git operations (audio deleted by `02_transcribe.py` after each file)

---

## Section 2: Impact Analysis

**Epic Impact:** N/A — No formal epics exist (project uses tech-spec directly)

**Story Impact:** N/A — No formal stories exist

**Artifact Conflicts:**

| Artifact | Impact | Action Required |
|----------|--------|-----------------|
| `.gitignore` | `data/` too broad — blocks valuable output files | Change to `data/audio/` only |
| `run_pipeline.sh` while loop | No per-batch checkpoint; pod death loses all GPU transcript work | Add git checkpoint inside loop after each batch's transcription |
| `run_pipeline.sh` Phase 4 | `git add data/...` commands were broken (gitignored); now fixed by gitignore change | Update to commit all data files correctly; add idempotency guard |
| `tech-spec-wip.md` Task 1 | `.gitignore` spec says exclude `archive.txt` and `data/`; actual implementation differs | Update to reflect `data/audio/` only |
| `tech-spec-wip.md` Task 9 | Phase 4 git section not in spec; no per-batch checkpoint documented | Add Phase 4 and checkpoint pattern to Task 9 |

**Size analysis — what gets tracked in git:**

| File(s) | Est. size per file | Est. total (1000 lectures) | Per-batch commit (~50 lectures) |
|---|---|---|---|
| `archive.txt` | grows by ~20 bytes/video | ~20KB | negligible |
| `data/transcripts/*.json` | 50–150KB | 50–150MB | ~3–7MB |
| `data/tagged/*.json` | 10–30KB | 10–30MB | ~0.5–1.5MB |
| `data/unresolved_speakers.txt` | tiny | tiny | negligible |
| `data/uploaded.txt` | tiny | tiny | negligible |
| `data/audio/` | 50–200MB each | **gitignored** | — |

Total repo size at completion: ~80–180MB. Well within GitHub's 1GB soft limit; individual files far under the 50MB threshold.

**Technical Impact:**
- `.gitignore` change is a one-line edit; unblocks all existing Phase 4 `git add` commands
- Per-batch checkpoint inside while loop: each commit is ~3–7MB, takes 2–5 seconds (negligible vs. hours of transcription)
- Recovery with per-batch commits: pod dies → new pod → `git pull` → transcript JSONs + `archive.txt` restored → re-run → only the current incomplete batch redone

---

## Section 3: Recommended Approach

**Selected Path:** Direct Adjustment (Option 1)

**Rationale:**
- `.gitignore` fix is a 1-word change (`data/` → `data/audio/`) and unblocks everything else
- Per-batch checkpoint is a small addition to the while loop — no structural change to the pipeline
- Transcript JSONs represent real GPU cost (~$0.39/hr on A40) — worth preserving in git per batch
- Tagged JSONs represent real Gemini API time — worth preserving in the Phase 4 final commit
- After the gitignore fix, Phase 4's existing `git add` commands work exactly as intended
- Effort: **Low** | Risk: **Low** | Timeline impact: **None**

---

## Section 4: Detailed Change Proposals

### Proposal 1 — `.gitignore` — Narrow to `data/audio/` only

```
File: .gitignore

OLD:
data/
.env
*.log

NEW:
data/audio/
.env
*.log
```

**Rationale:** Only audio files need to be excluded — they're ephemeral (deleted after transcription) and too large for git. Transcript JSONs, tagged JSONs, and tracking files (`unresolved_speakers.txt`, `uploaded.txt`) are all small enough and valuable enough to track.

---

### Proposal 2 — `run_pipeline.sh` — Add Per-Batch Git Checkpoint

```
Section: while-loop body (after transcription, before download-done check)

OLD (lines 25–37):
    echo "--- [Batch $BATCH_NUM] Transcribing ---"
    set +e
    python scripts/02_transcribe.py
    TRANSCRIBE_CODE=$?
    set -e
    if [ $TRANSCRIBE_CODE -ne 0 ]; then
        echo "[WARN] 02_transcribe.py exited with code $TRANSCRIBE_CODE — check logs before continuing"
    fi

    if [ $DOWNLOAD_CODE -eq 0 ]; then
        echo "--- All channels fully downloaded after $BATCH_NUM batches ---"
        break
    fi
done

NEW:
    echo "--- [Batch $BATCH_NUM] Transcribing ---"
    set +e
    python scripts/02_transcribe.py
    TRANSCRIBE_CODE=$?
    set -e
    if [ $TRANSCRIBE_CODE -ne 0 ]; then
        echo "[WARN] 02_transcribe.py exited with code $TRANSCRIBE_CODE — check logs before continuing"
    fi

    # Checkpoint: commit archive + new transcripts to GitHub after each batch
    # Preserves GPU transcription work and download progress if pod dies mid-run
    echo "--- [Batch $BATCH_NUM] Checkpointing to GitHub ---"
    git add archive.txt
    git add data/transcripts/*.json data/unresolved_speakers.txt 2>/dev/null || true
    git diff --cached --quiet || git commit -m "checkpoint: batch $BATCH_NUM ($(wc -l < archive.txt) videos archived)"
    git push origin main

    if [ $DOWNLOAD_CODE -eq 0 ]; then
        echo "--- All channels fully downloaded after $BATCH_NUM batches ---"
        break
    fi
done
```

**Rationale:**
- `data/transcripts/*.json` — commits this batch's Whisper output (the expensive GPU work)
- `data/unresolved_speakers.txt` — commits any new unresolved video titles from this batch
- `2>/dev/null || true` — suppresses the "no files matched" error if a directory is empty (safe no-op)
- `git diff --cached --quiet || git commit` — skips commit if nothing staged (handles case where all batch videos were already archived)
- Recovery: pod dies → new pod → `git pull` → transcripts + archive.txt restored → re-run → only current incomplete batch redone

---

### Proposal 3 — `run_pipeline.sh` — Fix Phase 4 (now works correctly after Proposal 1)

```
Section: Phase 4: GitHub Data Persistence (lines 48–62)

OLD:
# Phase 4: GitHub Data Persistence
echo "=== [4/4] Pushing data artifacts to GitHub ==="

# We only track JSONs and config; exclude bulky audio via .gitignore
git add data/transcripts/*.json
git add data/tagged/*.json
git add data/unresolved_speakers.txt
git add data/uploaded.txt
git add archive.txt
git add config/*.yaml

git commit -m "Auto-archive batch: $(date +'%Y-%m-%d %H:%M')"
git push origin main

echo "=== Data safely pushed to GitHub. Pipeline Complete. ==="

NEW:
# Phase 4: GitHub Final Commit
# audio/ is gitignored (ephemeral); all other data/ files are now tracked
echo "=== [4/4] Final GitHub commit ==="
git add archive.txt
git add config/*.yaml
git add data/transcripts/*.json data/tagged/*.json 2>/dev/null || true
git add data/unresolved_speakers.txt data/uploaded.txt 2>/dev/null || true
git diff --cached --quiet || git commit -m "pipeline complete: $(date +'%Y-%m-%d %H:%M') — $(wc -l < archive.txt) videos processed"
git push origin main

echo "=== Data safely pushed to GitHub. Pipeline complete. ==="
```

**Rationale:** After the `.gitignore` fix (Proposal 1), the `git add data/...` commands work as originally intended. Added `2>/dev/null || true` guards for empty directories. Added `git diff --cached --quiet` guard — since transcripts were already committed per-batch, the final commit mainly captures `data/tagged/*.json` and `data/uploaded.txt`.

---

### Proposal 4 — `tech-spec-wip.md` Task 1 — Update `.gitignore` spec

```
Section: Task 1 — Create project scaffold

OLD (Action line):
  create `.gitignore` excluding `data/`, `archive.txt`, `.env`, `*.log`, `data/unresolved_speakers.txt`

NEW:
  create `.gitignore` excluding `data/audio/`, `.env`, `*.log`
  Note: only audio files are excluded — transcripts, tagged JSONs, and tracking files
  (unresolved_speakers.txt, uploaded.txt) are tracked by git for recovery purposes.
  archive.txt is tracked (not excluded) — it is the primary recovery artifact.
```

---

### Proposal 5 — `tech-spec-wip.md` Task 9 — Document Phase 4 and Per-Batch Checkpoint

```
Section: Task 9 — run_pipeline.sh

ADD to Notes (after existing notes):
  - "Git checkpoint strategy: archive.txt + new transcript JSONs committed after EACH batch
    inside the while loop; tagged JSONs + uploaded.txt committed in Phase 4 final commit.
    audio/ is gitignored (ephemeral); all other data/ files are tracked."
  - "Per-batch commit guard: git add ... 2>/dev/null || true handles empty directories safely"
  - "git diff --cached --quiet || git commit: skips commit if nothing new to commit"
  - "Recovery workflow: on a new RunPod pod — git pull → transcripts + archive.txt restored
    → bash run_pipeline.sh → completed batches skipped automatically; only current batch redone"
  - "Phase 4 final commit captures: tagged JSONs (from 03_tag.py) + uploaded.txt (from
    04_upload_notion.py) — these don't exist until after the while loop completes"

ADD Phase 4 section to the run_pipeline.sh code block (currently missing from spec):
  # Phase 4: GitHub Final Commit
  echo "=== [4/4] Final GitHub commit ==="
  git add archive.txt
  git add config/*.yaml
  git add data/transcripts/*.json data/tagged/*.json 2>/dev/null || true
  git add data/unresolved_speakers.txt data/uploaded.txt 2>/dev/null || true
  git diff --cached --quiet || git commit -m "pipeline complete: $(date +'%Y-%m-%d %H:%M') — $(wc -l < archive.txt) videos processed"
  git push origin main
```

---

## Section 5: Implementation Handoff

**Change Scope Classification:** Minor

**Handoff:** Development team (Dwarakadas) — direct file edits.

**Responsibilities:**
1. Edit `.gitignore`: change `data/` to `data/audio/` (Proposal 1)
2. Edit `run_pipeline.sh` while loop: add per-batch checkpoint block (Proposal 2)
3. Edit `run_pipeline.sh` Phase 4: add guards and fix git add commands (Proposal 3)
4. Edit `tech-spec-wip.md` Task 1: update gitignore description (Proposal 4)
5. Edit `tech-spec-wip.md` Task 9: add Phase 4 and checkpoint notes (Proposal 5)

**Success Criteria:**
- `data/audio/` is the only gitignored subdirectory of `data/`; all JSON and tracking files are tracked
- After each batch completes, `archive.txt` + new `data/transcripts/*.json` are committed and pushed
- Phase 4 commit captures `data/tagged/*.json` and `data/uploaded.txt` correctly
- All `git add` commands use `2>/dev/null || true` to handle empty directories safely
- All `git commit` calls use `git diff --cached --quiet ||` guard to skip empty commits
- Recovery verified: fresh pod → `git pull` → transcript JSONs + archive.txt present → re-run skips completed work
