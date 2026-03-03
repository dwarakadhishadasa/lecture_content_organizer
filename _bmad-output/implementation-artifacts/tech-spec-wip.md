---
title: 'Graceful Download Session Error Handling'
slug: 'graceful-download-session-error-handling'
created: '2026-03-03'
status: 'in-progress'
stepsCompleted: [1, 2]
tech_stack:
  - Python 3.10+
  - yt-dlp (Python API - YoutubeDL class)
files_to_modify:
  - scripts/01_download.py
  - run_pipeline.sh
code_patterns:
  - Logger class passed via opts dict to YoutubeDL — reference must be hoisted to access after download
  - Exit codes as contract between 01_download.py and run_pipeline.sh (0/101 pattern)
  - download_playlists() returns tuple — all return paths must be updated together
test_patterns:
  - No test suite exists — manual verification only
---

# Tech-Spec: Graceful Download Session Error Handling

**Created:** 2026-03-03

## Overview

### Problem Statement

Session-level yt-dlp errors — bot detection (`"Sign in to confirm you're not a bot"`) and rate-limiting (`"The current session has been rate-limited by YouTube"`) — are currently logged to `data/download_errors.txt` but otherwise ignored. The pipeline continues attempting subsequent videos in a broken session, wasting time, and never advances to the transcription step.

### Solution

Detect session-killer error messages in `_DownloadLogger.error()` by substring-matching known phrases. Set a flag on the logger instance. After `ydl.download()` returns, check the flag and exit with code `102`. Handle exit code `102` in `run_pipeline.sh` to break out of the download loop and proceed to `02_transcribe.py`.

### Scope

**In Scope:**
- `_DownloadLogger` — add session-failure detection and flag
- `download_playlists()` — check logger flag, return new sentinel or propagate exit 102
- `main()` — check logger flag after each `ydl.download()` call, exit 102
- `run_pipeline.sh` — handle exit code 102 (break loop, advance to transcribe)
- Docstring update in `01_download.py` to document exit code 102

**Out of Scope:**
- Per-video failure tracking (`failed_downloads.txt`)
- Cookie management or authentication fixes
- Retry scheduling or backoff logic
- Changes to `02_transcribe.py` or any downstream scripts

## Context for Development

### Codebase Patterns

- `_DownloadLogger` (lines 30–41 of `01_download.py`) — simple class with `debug()`, `warning()`, `error()` methods. **No `__init__`**. `error()` prints and appends to `data/download_errors.txt`. Adding `__init__` with `self.session_failed = False` is required.
- **Logger reference is not retained after opts dict is built.** Currently `"logger": _DownloadLogger()` is created inline inside opts (line 110 in `download_playlists()`, line 206 in `main()`). Must hoist to a variable to inspect `session_failed` post-download.
- Exit codes are the contract between `01_download.py` and `run_pipeline.sh`: `0` = all done, `101` = batch complete more remain. `102` follows this pattern.
- `download_playlists()` returns `tuple[int, bool]`. Has **3 return paths** at lines 81, 137, 139 — all must be updated to `tuple[int, bool, bool]`.
- `run_pipeline.sh` exit handling (lines 58–61): only checks `$DOWNLOAD_CODE -eq 0` and breaks; `101` falls through implicitly. Add `elif` for `102` before the closing `fi`.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `scripts/01_download.py` | Download script — `_DownloadLogger`, `download_playlists()`, `main()` |
| `run_pipeline.sh` | Pipeline orchestrator — download loop and exit code handling |
| `data/download_errors.txt` | Existing error log written by `_DownloadLogger.error()` |

### Technical Decisions

- **Detection via substring match** on known phrases — simple, no yt-dlp internals dependency. Phrases to match: `"Sign in to confirm"`, `"rate-limited by YouTube"`.
- **Flag on logger instance** (`self.session_failed = False`, set to `True` on match) — logger is instantiated per `YoutubeDL` context, so flag scope is clean.
- **Exit code 102** — new sentinel meaning "session broken, advance anyway". Documented in module docstring.
- **`download_playlists()` return tuple extended** to `(int, bool, bool)` — `(total_downloaded, batch_full, session_failed)`. Caller (`main()`) checks the third value.
- **Immediate exit after `ydl.download()` returns** — do not attempt remaining playlists/channels if session is broken.

## Implementation Plan

### Tasks

**Task 1 — Update `_DownloadLogger` in `scripts/01_download.py`**

File: `scripts/01_download.py`

- Add `self.session_failed = False` to `_DownloadLogger.__init__()` (add `__init__` if not present — currently the class has no `__init__`, so add one)
- In `error()`, after appending to `download_errors.txt`, check:
  ```python
  SESSION_ERRORS = ("Sign in to confirm", "rate-limited by YouTube")
  if any(phrase in msg for phrase in SESSION_ERRORS):
      self.session_failed = True
  ```

**Task 2 — Thread logger instance through `download_playlists()` and check flag**

File: `scripts/01_download.py`

- Instantiate `_DownloadLogger()` outside the per-playlist loop (one instance for the whole function)
- Pass it in each `opts` dict as `"logger": logger`
- Change return signature to `tuple[int, bool, bool]`: `return total_downloaded, batch_full, logger.session_failed`
- After each `ydl.download()` call inside the loop, check `if logger.session_failed: return total_downloaded, False, True` immediately (don't attempt remaining playlists)
- Update all existing `return` statements in the function to include the third value (`False` for non-session-failed paths)

**Task 3 — Update `main()` to handle session failure and exit 102**

File: `scripts/01_download.py`

- Update all callers of `download_playlists()` to unpack three values: `playlist_count, batch_full, session_failed = download_playlists(...)`
- After the playlist call, if `session_failed: sys.exit(102)`
- For the channel download block: instantiate a separate `_DownloadLogger()`, pass as `"logger"` in opts, check `logger.session_failed` after `ydl.download()` returns, `sys.exit(102)` if set
- Update module docstring to add: `102 — Session broken by YouTube (bot detection or rate-limit); transcription can proceed`

**Task 4 — Handle exit code 102 in `run_pipeline.sh`**

File: `run_pipeline.sh`

- In the download-transcribe loop, extend the exit code check after `DOWNLOAD_CODE=$?`:
  ```bash
  if [ $DOWNLOAD_CODE -eq 0 ]; then
      echo "--- All channels fully downloaded after $BATCH_NUM batches ---"
      break
  elif [ $DOWNLOAD_CODE -eq 102 ]; then
      echo "[WARN] Download session broken (bot/rate-limit). Advancing to transcribe."
      break
  fi
  # code 101 falls through — loop continues for next batch
  ```

### Acceptance Criteria

**AC1 — Bot detection detected and flagged**
- Given: `_DownloadLogger.error()` is called with a message containing `"Sign in to confirm"`
- When: the method executes
- Then: `self.session_failed` is `True`

**AC2 — Rate-limit detected and flagged**
- Given: `_DownloadLogger.error()` is called with a message containing `"rate-limited by YouTube"`
- When: the method executes
- Then: `self.session_failed` is `True`

**AC3 — Unrelated per-video error does not set flag**
- Given: `_DownloadLogger.error()` is called with `"[youtube] abc123: Video unavailable"`
- When: the method executes
- Then: `self.session_failed` remains `False`

**AC4 — Session failure exits 01_download.py with code 102**
- Given: a session-killer error fires during playlist or channel download
- When: `ydl.download()` returns
- Then: `main()` calls `sys.exit(102)`

**AC5 — Pipeline advances to transcription on 102**
- Given: `01_download.py` exits with code `102`
- When: `run_pipeline.sh` reads `$DOWNLOAD_CODE`
- Then: the download loop breaks and `02_transcribe.py` is executed next

**AC6 — Normal batch behaviour unchanged**
- Given: no session errors occur
- When: batch completes with more remaining
- Then: exit code is still `101` and the pipeline loops for the next batch

## Additional Context

### Dependencies

None — no new packages required.

### Testing Strategy

Manual testing is the primary approach (reproducing bot/rate-limit errors requires a live YouTube session). For unit-level:
- Instantiate `_DownloadLogger`, call `.error()` with session-killer strings and benign strings, assert `session_failed` flag state.

### Notes

- The `_DownloadLogger` class currently has no `__init__`. Adding one is required to initialise `self.session_failed`.
- One logger instance per `YoutubeDL` context keeps flag scope clean — don't share across playlist and channel download blocks.
- The combined error seen in testing (`"948RKYzxgrQ: Video unavailable... The current session has been rate-limited"`) will correctly set `session_failed=True` because the rate-limit phrase is present, even though the message also has a video ID.
- **Known pre-existing inefficiency (out of scope):** `02_transcribe.py` loads the Whisper large-v3 CUDA model (line 41) before checking whether any audio files exist (line 48). If exit 102 fires with zero audio downloaded, the pipeline wastes ~30–60s on model initialisation before printing "No audio files found" and exiting 0. Functionally harmless but worth fixing in a future pass.
