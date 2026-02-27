---
title: 'Transcription-First Lecture Content Pipeline'
slug: 'transcription-first-lecture-pipeline'
created: '2026-02-27'
status: 'Completed'
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9]
tech_stack:
  - Python 3.10+
  - yt-dlp (Python API - YoutubeDL class)
  - faster-whisper (Whisper large-v3)
  - google-generativeai (Gemini 1.5 Flash)
  - notion-client (Notion API)
  - RunPod A40 GPU pod (all scripts run here via SSH)
files_to_modify:
  - config/channels.yaml
  - config/speakers.yaml
  - scripts/01_download.py
  - scripts/02_transcribe.py
  - scripts/utils/__init__.py
  - scripts/utils/resolve_speaker.py
  - scripts/03_tag.py
  - scripts/04_upload_notion.py
  - run_pipeline.sh
  - requirements.txt
code_patterns:
  - run_pipeline.sh: download-transcribe loop (BATCH_SIZE lectures at a time) then tag + upload
  - 01_download.py accepts --batch-size N; exits 101 if more remain, 0 if all done
  - Run inside screen session on RunPod for SSH-disconnect resilience
  - Each script is idempotent - skips already-completed work
  - JSON files as intermediate storage (data/transcripts/, data/tagged/)
  - Audio deleted immediately after successful transcription (bounded disk usage)
  - Channel URLs in config/channels.yaml (dynamic, N channels; NOT speaker-tied — speaker identity comes from title parsing)
  - Canonical speaker names in config/speakers.yaml (exact Notion display form; fuzzy-matched against video title)
  - resolve_speaker() in scripts/utils/resolve_speaker.py: splits title on `|`, fuzzy-matches each segment against canonical list (rapidfuzz WRatio, threshold=85); returns canonical name or None
  - Speaker pre-filter in 01_download.py via yt-dlp match_filter callable: resolve_speaker() runs at download time against video title; unresolved speakers skipped BEFORE audio download; NOT added to archive.txt — enables automatic retry after adding speaker to speakers.yaml; match_filter-skipped videos do NOT count toward max_downloads/BATCH_SIZE
  - Videos with unresolved speakers skipped at 01_download.py (match_filter); title logged to data/unresolved_speakers.txt; 02_transcribe.py speaker check serves as safety net only
  - yt-dlp --download-archive archive.txt for global deduplication
  - yt-dlp --write-info-json to preserve metadata for timestamp URLs
  - All scripts print [N/TOTAL] progress for pipeline.log monitoring
  - 03_tag.py: robust JSON parsing with regex fallback + failed_tag.txt logging
test_patterns:
  - No formal test framework (personal tooling)
  - Idempotency smoke test: run each script twice, verify second run skips all work
  - Single-video dry run before full bulk execution
---

# Tech-Spec: Transcription-First Lecture Content Pipeline

**Created:** 2026-02-27

## Overview

### Problem Statement

Scouting for relevant lecture content across 10 YouTube speakers is painfully slow — typically 1+ hour per preparation session. The only discovery method is listening, making retrieval entirely memory-based and fragile. The "hidden angle" problem (can't preview a speaker's treatment of a topic without listening through) compounds the pain. There is no searchable index of what was said, when, and how each topic was treated.

### Solution

Bulk transcribe all configured speakers' YouTube playlists using Whisper on RunPod, AI-tag each transcript segment using Gemini 1.5 Flash (free tier), and load everything into a structured Notion database. During lecture preparation, search Notion by verse reference, theme, content type, or audience circle to instantly surface relevant segments with exact YouTube timestamps — eliminating the need to listen before finding a match.

### Scope

**In Scope:**
- Audio download from YouTube playlists using yt-dlp (~1000 lectures across N speakers, dynamically configured)
- Whisper large-v3 transcription on RunPod (faster-whisper, GPU-accelerated)
- Segment-level AI tagging via Gemini 1.5 Flash: verse references, themes, content type (story/analogy/philosophy/practical), circle fit (1–4), key quotes, timestamps
- Structured Notion database with one row per tagged segment
- Notion-native search and filter as the lecture prep interface

**Out of Scope:**
- Transcribing own (Dwarakadas's) lectures
- Real-time or incremental transcription — bulk upfront only
- Custom search UI — Notion's built-in search/filter is sufficient
- Auto-ingesting new uploads as they are published (future phase)
- Perfect manual tagging — good-enough AI tagging is the goal

## Context for Development

### Codebase Patterns

**Confirmed Clean Slate** — no existing codebase. Greenfield Python project.

**Execution model**: All 4 scripts run on a single RunPod A40 GPU pod via SSH. No local execution, no SCP handoffs. SSH once → run scripts 01–04 in sequence → done.

**Orchestration**: `run_pipeline.sh` runs a **download-transcribe loop** (bounded by `BATCH_SIZE` lectures per cycle) to keep disk usage bounded (~3–6 GB audio at a time), then runs tag and upload once all audio is processed. Run inside a `screen` session on RunPod so the process survives SSH disconnects. Each script is idempotent — if the pipeline fails mid-run, fix the issue and re-run `run_pipeline.sh`; completed stages are skipped automatically.

**Data flow**:
```
config/channels.yaml
      ↓
┌─────────────────────────────────────────────┐  ← loop until all downloaded
│  01_download.py  →  data/audio/ (BATCH_SIZE) │
│        ↓ (audio deleted after transcription) │
│  02_transcribe.py  →  data/transcripts/      │
└─────────────────────────────────────────────┘
      ↓
03_tag.py  →  data/tagged/{video_id}.json
      ↓
04_upload_notion.py  →  Notion DB (one row per segment)
```

**Key patterns**:
- Audio files deleted immediately after successful transcription (save ~20–50 GB)
- `archive.txt` (yt-dlp) tracks downloaded video IDs for deduplication across N speakers
- `--write-info-json` preserves video_id, title, channel for timestamp URL construction
- Transcript and tagged JSON filenames keyed by `video_id` for cross-stage traceability
- Gemini rate limit: 15 RPM → ~67 min for 1000 lectures (acceptable for one-time job)
- Notion rate limit: 3 req/sec → ~1 hr for 10,000 rows; scripts handle retries with backoff

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `_bmad-output/brainstorming/brainstorming-session-2026-02-25.md` | Pipeline vision, tagging taxonomy, audience circle definitions |
| `_bmad-output/planning-artifacts/research/technical-yt-dlp-channel-playlist-download-strategy-research-2026-02-27.md` | yt-dlp flags, rate limiting, archive dedup strategy |

### Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Execution environment | RunPod A40 GPU pod (all 4 stages) | Eliminates SCP handoffs; GPU stages dominate cost anyway |
| Orchestration | Numbered scripts 01–04, manual sequential execution | Simple, transparent, no orchestrator overhead for one-time job |
| Intermediate storage | JSON files per lecture (`data/transcripts/`, `data/tagged/`) | Resumable, inspectable, no extra dependencies |
| Audio lifecycle | Delete after successful transcription | Saves 20–50 GB; transcript JSON is the permanent artifact |
| Channel list | `config/channels.yaml` (dynamic, N channels; not speaker-tied) + `config/speakers.yaml` (canonical speaker names) | Configurable without code changes; speaker resolved from video title via fuzzy match |
| Transcription service | Whisper large-v3 via faster-whisper on RunPod A40 | GPU-accelerated, ~20–25x real-time, ~$0.39/hr |
| AI tagging model | Gemini 1.5 Flash (Google AI Studio free tier) | ~$0 cost at 15 RPM; 1M context handles full transcripts |
| Tagging approach | Per-lecture (full transcript → all segments + tags in one Gemini call) | Fewer API calls vs. per-segment; Gemini handles full transcript in one shot |
| Storage & search | Notion database (schema defined in spec) | Familiar; native filter/search sufficient for lecture prep |
| Download tool | yt-dlp Python API (YoutubeDL class) | Scriptable, archive dedup, metadata preservation via --write-info-json |
| Segmentation | Timestamp-based, 5–10 min segments | Granular enough for retrieval; manageable Notion row count |
| Scale | ~1000 lectures, ~900 hrs audio, ~10,000 segments | N speakers' playlists (dynamically configured) |
| Cost estimate | $15–32 total (RunPod) + $0 (Gemini free tier) | One-time bulk processing |
| Time estimate | 2–3 days elapsed (mostly unattended), 5–7 hrs active | Transcription runs overnight |

## Implementation Plan

### Tasks

- [x] **Task 1: Create project scaffold and `requirements.txt`**
  - File: `requirements.txt`, `config/`, `scripts/`, `scripts/utils/`, `data/audio/`, `data/transcripts/`, `data/tagged/`, `.env.example`, `.gitignore`
  - Action: Create all directories (including `scripts/utils/`); create `requirements.txt` with pinned dependencies; create `scripts/utils/__init__.py` as an empty file; create `.env.example` as a template; create `.gitignore` excluding `data/audio/`, `.env`, `*.log`
  - Note: only `data/audio/` is gitignored — audio files are ephemeral (deleted after transcription) and too large for git. All other `data/` files (`data/transcripts/`, `data/tagged/`, `data/unresolved_speakers.txt`, `data/uploaded.txt`) and `archive.txt` are tracked by git for recovery purposes.
  - Notes: `requirements.txt` contents:
    ```
    yt-dlp>=2024.1.0
    faster-whisper>=1.0.0
    google-generativeai>=0.8.0
    notion-client>=2.2.0
    python-dotenv>=1.0.0
    pyyaml>=6.0
    rapidfuzz>=3.0.0
    ```
  - **F6 — Setup:** Before running the pipeline on RunPod, execute: `apt-get install -y ffmpeg` (required by yt-dlp's `FFmpegExtractAudio` post-processor). Add this as a comment in `.env.example` under a `# Pod setup` heading.

- [x] **Task 2: Create `config/channels.yaml`**
  - File: `config/channels.yaml`
  - Action: Create YAML with a list of channel URLs in this format:
    ```yaml
    channels:
      - url: "https://www.youtube.com/@ChannelHandle"
      - url: "https://www.youtube.com/@AnotherChannel"
    ```
  - Notes: Channels are NOT speaker-tied — speaker identity comes from video title parsing (Task 2b). Adding/removing channels requires only editing this file — no code changes needed. In `scripts/01_download.py`, load `channels[*].url` (not `speakers[*].url`).

- [x] **Task 2a: Create `config/speakers.yaml`**
  - File: `config/speakers.yaml`
  - Action: Create YAML with the canonical speaker name list (exact form as desired in Notion):
    ```yaml
    speakers:
      - "Speaker Name Exactly As Wanted In Notion"
      - "Another Speaker Name"
      # Populate ALL known speakers before first pipeline run.
      # Fuzzy matching handles title variants (honorifics, abbreviations, typos).
    ```
  - Notes: Names must match the exact string you want to appear in the Notion `Speaker` Select property. One-time manual population before pipeline execution. To add a new speaker after the initial run: add their name here and re-run the pipeline — idempotency ensures already-processed videos are skipped automatically.

- [x] **Task 2b: Create `scripts/utils/resolve_speaker.py`**
  - File: `scripts/utils/resolve_speaker.py` (also requires `scripts/utils/__init__.py`, already created empty in Task 1)
  - Action: Implement `load_speakers()` and `resolve_speaker()`:
    ```python
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
  - Notes: All `|`-delimited title segments are tested against the canonical list. Date strings, scripture references, and topic titles score near 0 against speaker names — the correct segment wins by score regardless of position in the title. Example titles the algorithm must handle:
    - `"Core of Spiritual Life | Sri Vasudev Keshava Dasa | SB 6.12.22 | 05.02.2026"` → matches canonical `"Sri Vasudev Keshava Dasa"`
    - `"2015-11-13 | SB 3.18.1 | HG Amitasana Dasa"` → matches canonical `"Amitasana Dasa"` (WRatio tolerates honorific prefix `HG`)
    - `threshold=85` is tunable: after a single-speaker dry run, inspect `data/unresolved_speakers.txt` for false negatives and lower the threshold if needed (false positives are more dangerous than false negatives — prefer leaving ambiguous videos unresolved)

- [x] **Task 3: Create Notion database (manual browser step)**
  - File: Notion (browser)
  - Action: Create a new full-page database named "Lecture Content Index" with these exact properties:

    | Property Name | Notion Type |
    |---|---|
    | Segment Title | Title (required) |
    | Speaker | Select |
    | Video Title | Rich Text |
    | Timestamp URL | URL |
    | Start Time | Number |
    | End Time | Number |
    | Verse References | Multi-select |
    | Themes | Multi-select |
    | Content Type | Select |
    | Circle Fit | Multi-select |
    | Key Quote | Rich Text |
    | Transcript | Rich Text |
    | Upload Date | Date |

  - Notes: After creating the DB, copy the 32-char database ID from the URL (hex string after the last `/` and before `?v=`). Share the database with your Notion integration (Connections → Add connection). Paste both the integration token and database ID into `.env`.

- [x] **Task 4: Create `.env` secrets file**
  - File: `.env` (never commit to git)
  - Action: Create `.env` with:
    ```
    GEMINI_API_KEY=your_google_ai_studio_key
    NOTION_API_KEY=secret_your_notion_integration_token
    NOTION_DATABASE_ID=your_32char_database_id
    ```

- [x] **Task 5: Implement `scripts/01_download.py`**
  - File: `scripts/01_download.py`
  - Action:
    1. Parse `--batch-size N` argument (default: 50) using `argparse`
    2. Load `config/channels.yaml` with PyYAML; collect all channel URLs from `channels[*].url`
    3. Load speakers for pre-filtering: `from scripts.utils.resolve_speaker import load_speakers, resolve_speaker`; `speakers = load_speakers("config/speakers.yaml")`
    4. Define `match_filter` callable for speaker pre-filtering:
       ```python
       def speaker_match_filter(info_dict, *, incomplete):
           title = info_dict.get('title', '')
           if resolve_speaker(title, speakers) is None:
               return "Speaker unresolved — skipping download"
           return None
       ```
    5. Call `YoutubeDL(opts).download(all_urls)` — pass all channel URLs in one call so `max_downloads` applies globally across all speakers:
       ```python
       {
           'format': 'bestaudio/best',
           'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
           'outtmpl': 'data/audio/%(id)s.%(ext)s',
           'download_archive': 'archive.txt',
           'writeinfojson': True,
           'max_downloads': batch_size,
           'match_filter': speaker_match_filter,
           'sleep_interval': 15,
           'max_sleep_interval': 45,
           'sleep_requests': 3,
           'ignoreerrors': True,
       }
       ```
    6. Catch `MaxDownloadsReached` from `yt_dlp.utils`:
       ```python
       from yt_dlp.utils import MaxDownloadsReached
       try:
           with YoutubeDL(opts) as ydl:
               ydl.download(all_urls)
           print("[01_download] All channels fully downloaded.")
           sys.exit(0)   # done — no more to fetch
       except MaxDownloadsReached:
           print(f"[01_download] Batch of {batch_size} downloaded. More remain.")
           sys.exit(101)  # signal: loop should continue
       ```
  - Notes: `match_filter` callable is invoked by yt-dlp after fetching video metadata (title available) but before any audio download. Videos that return a non-None string are skipped entirely: no audio downloaded, no info.json written, NOT added to archive.txt — this means re-running after adding a missing speaker to speakers.yaml will automatically retry the video on the next run. `match_filter`-skipped videos do NOT count against `max_downloads`, so BATCH_SIZE=50 means 50 actually-downloaded (resolved-speaker) lectures. Unresolved video titles are still logged to `data/unresolved_speakers.txt` by `resolve_speaker()`. `ignoreerrors=True` skips unavailable/private/age-gated videos; `archive.txt` deduplicates globally — already-downloaded video IDs are never re-fetched; exit code 101 is how `run_pipeline.sh` detects that more batches remain; **F3** — print `[01_download] Batch N complete` at end

- [x] **Task 6: Implement `scripts/02_transcribe.py`**
  - File: `scripts/02_transcribe.py`
  - Action:
    1. At startup: `from scripts.utils.resolve_speaker import load_speakers, resolve_speaker`; `speakers = load_speakers("config/speakers.yaml")`
    2. At startup: delete any stale `data/transcripts/*.json.tmp` files from previous interrupted runs
    3. Load `WhisperModel("large-v3", device="cuda", compute_type="float16")` once at startup
    4. Glob `data/audio/` for audio files (`*.mp3`, `*.m4a`, `*.webm`); count total for progress
    5. For each audio file (print `[N/TOTAL] Transcribing {video_id}...`):
       - Derive `video_id` = filename stem (e.g., `abc123` from `abc123.mp3`)
       - **P3 — Skip + cleanup:** If `data/transcripts/{video_id}.json` already exists AND audio file also exists → delete the audio file (handles re-download-after-archive-loss case) then `continue`. Print `[N/TOTAL] Skipping {video_id} (transcript exists)`
       - **F4 — info.json fix:** Load info.json using glob: `info_path = sorted(Path("data/audio").glob(f"{video_id}*.info.json"))[0]` — handles pre-post-processor extension variants (e.g., `abc123.webm.info.json`)
       - Extract `title`, `webpage_url`, `duration` from info.json — do NOT use `uploader` (channel owner ≠ speaker)
       - Resolve speaker: `speaker = resolve_speaker(title, speakers)`
       - If `speaker is None`: print `[N/TOTAL] Skipping {video_id}: speaker unresolved`; delete audio file (`audio_path.unlink()`); `continue` — no transcript JSON created, no GPU time wasted. Title already logged to `data/unresolved_speakers.txt` by `resolve_speaker()`.
         **Safety net only**: under normal operation this path should not trigger — `01_download.py` pre-filters unresolved speakers at download time via `match_filter`, so no unresolved-speaker audio should reach this stage. This check remains as defense-in-depth (e.g., if `speakers.yaml` was modified between the download and transcription stages).
       - Run `segments, _ = model.transcribe(str(audio_path), beam_size=5)`
       - **P4 — Atomic write:** Write to temp file then rename (prevents corruption if pod is killed mid-write):
         ```python
         tmp_path = Path(f"data/transcripts/{video_id}.json.tmp")
         tmp_path.write_text(json.dumps(data, ensure_ascii=False))
         tmp_path.rename(f"data/transcripts/{video_id}.json")
         ```
       - Output JSON structure:
         ```json
         {
           "video_id": "abc123",
           "title": "Video Title",
           "speaker": "Canonical Speaker Name",
           "youtube_url": "https://www.youtube.com/watch?v=abc123",
           "duration": 3600,
           "segments": [
             {"start": 0.0, "end": 4.2, "text": "First sentence."}
           ]
         }
         ```
       - Delete audio file: `audio_path.unlink()`
       - Print `[N/TOTAL] Transcribed + deleted: {video_id}`
  - Notes: `compute_type="float16"` is optimal for A40; `beam_size=5` is default quality; iterate segments from faster-whisper as a generator (don't load all into RAM at once for very long files). Speaker is guaranteed non-null at this point — unresolved speakers are skipped above before transcription begins.

- [x] **Task 7: Implement `scripts/03_tag.py`**
  - File: `scripts/03_tag.py`
  - Action:
    1. `genai.configure(api_key=os.environ["GEMINI_API_KEY"])`; `model = genai.GenerativeModel("gemini-1.5-flash")`
    2. Glob `data/transcripts/*.json`; count total for progress
    3. For each transcript (print `[N/TOTAL] Tagging {video_id}...`):
       - Derive `video_id` from filename
       - **Skip** if `data/tagged/{video_id}.json` already exists
       - Load transcript JSON
       - Format transcript text: one line per Whisper segment → `[{start:.0f}s] {text}`
       - Build and send prompt (see Notes for full template)
       - **F2 — Robust JSON parsing:**
         ```python
         raw = response.text
         # Strip markdown fences
         raw = re.sub(r'```json|```', '', raw).strip()
         # Fallback: extract first {...} block if prose wraps the JSON
         match = re.search(r'\{.*\}', raw, re.DOTALL)
         if not match:
             print(f"[WARN] No JSON found for {video_id} — logging to data/failed_tag.txt")
             with open("data/failed_tag.txt", "a") as f: f.write(video_id + "\n")
             continue
         data = json.loads(match.group())
         # Validate required keys on each segment
         required = {"start_time","end_time","verse_references","themes","content_type","circle_fit","key_quote","summary"}
         valid_segments = [s for s in data.get("segments", []) if required.issubset(s.keys())]
         if not valid_segments:
             print(f"[WARN] No valid segments for {video_id} — logging to data/failed_tag.txt")
             with open("data/failed_tag.txt", "a") as f: f.write(video_id + "\n")
             continue
         ```
       - **P2 — Timestamp URL:** Use `timestamp_url = f"https://youtu.be/{video_id}?t={int(segment['start_time'])}"` — avoids the double-`?` bug from appending to `watch?v=` URLs
       - **P5 — Key name:** Save tagged segments under the key `"segments"` (same key name as transcript JSON for consistency):
         ```python
         output = {
             "video_id": video_id, "title": title, "speaker": speaker,
             "youtube_url": youtube_url,
             "segments": valid_segments  # each enriched with timestamp_url etc.
         }
         ```
       - Save `data/tagged/{video_id}.json`
       - `time.sleep(4)` after each call (15 RPM = max 1 per 4s)
    4. Wrap each API call in retry loop: on exception, sleep 2s → 4s → 8s, then log to `failed_tag.txt` and `continue`
  - Notes: Gemini prompt template:
    ```
    You are analyzing a transcript from a Vaishnava lecture.
    Speaker: {speaker}
    Title: {title}
    URL: {youtube_url}

    Timestamped transcript:
    {transcript_text}

    Identify thematic segments of 5–10 minutes each. For each segment return ONLY valid JSON (no markdown fences):
    {
      "segments": [
        {
          "start_time": <int seconds>,
          "end_time": <int seconds>,
          "verse_references": ["BG 2.47", "SB 1.2.6"],
          "themes": ["detachment", "karma"],
          "content_type": "story|analogy|philosophy|practical",
          "circle_fit": [1, 2],
          "key_quote": "Most impactful sentence from this segment",
          "summary": "One sentence describing this segment"
        }
      ]
    }
    circle_fit: 1=full-time devotees, 2=congregation/volunteers, 3=newcomers, 4=general public with no prior exposure
    VERSE REFERENCE FORMAT (P1): Use ONLY "BG X.Y" for Bhagavad-gita and "SB X.Y.Z" for Srimad Bhagavatam.
    No other formats ("Bg.", "Bhagavad-gita", chapter references without verse). If uncertain, use empty list [].
    ```
  - After run, check `data/failed_tag.txt` — re-run `03_tag.py` after fixing any issue; failed video_ids have no tagged JSON so they will be retried automatically

- [x] **Task 8: Implement `scripts/04_upload_notion.py`**
  - File: `scripts/04_upload_notion.py`
  - Action:
    1. `client = Client(auth=os.environ["NOTION_API_KEY"])`; `db_id = os.environ["NOTION_DATABASE_ID"]`
    2. Load `data/uploaded.txt` into a set (create empty file if missing)
    3. Glob `data/tagged/*.json`
    4. For each tagged file:
       - **Skip** if `video_id` in uploaded set (print `[N/TOTAL] Already uploaded: {video_id}`)
       - Load tagged JSON; iterate over `data["segments"]` (P5 — same key name saved by Task 7)
       - For each segment, call `client.pages.create()` with all 13 properties:
         ```python
         properties={
             "Segment Title": {"title": [{"text": {"content": segment["key_quote"][:2000]}}]},
             "Speaker": {"select": {"name": data["speaker"]}},
             "Video Title": {"rich_text": [{"text": {"content": data["title"][:2000]}}]},
             "Timestamp URL": {"url": segment["timestamp_url"]},
             "Start Time": {"number": segment["start_time"]},
             "End Time": {"number": segment["end_time"]},
             "Verse References": {"multi_select": [{"name": v} for v in segment["verse_references"]]},
             "Themes": {"multi_select": [{"name": t} for t in segment["themes"]]},
             "Content Type": {"select": {"name": segment["content_type"]}},
             "Circle Fit": {"multi_select": [{"name": str(c)} for c in segment["circle_fit"]]},
             "Key Quote": {"rich_text": [{"text": {"content": segment["key_quote"][:2000]}}]},
             "Transcript": {"rich_text": [{"text": {"content": segment_transcript[:2000]}}]},
             "Upload Date": {"date": {"start": datetime.date.today().isoformat()}},
         }
         ```
       - `time.sleep(0.35)` after each page create (3 req/sec limit)
       - On `APIResponseError` with status 429: `time.sleep(60)` then retry once
       - After all segments uploaded, append `video_id + "\n"` to `data/uploaded.txt`
       - Print `[N/TOTAL] Uploaded {video_id} ({len(segments)} segments)`
  - Notes: Notion rich_text fields have a 2000-char limit — always truncate; segment transcript is reconstructed from the source `data/transcripts/{video_id}.json` by matching `start_time`/`end_time` to original Whisper segments; multi_select values are created automatically in Notion if they don't already exist

- [x] **Task 9: Create `run_pipeline.sh`**
  - File: `run_pipeline.sh`
  - Action: Create shell script:
    ```bash
    #!/bin/bash
    # Run inside a screen session to survive SSH disconnects:
    #   screen -S pipeline
    #   bash run_pipeline.sh 2>&1 | tee pipeline.log
    #   Ctrl+A D  →  detach (safe to close SSH)
    #   screen -r pipeline  →  reattach next day

    set -e

    BATCH_SIZE=50  # lectures per download-transcribe cycle (~3-6 GB audio at a time)
    BATCH_NUM=0

    echo "=== Pipeline start (BATCH_SIZE=$BATCH_SIZE) ==="

    # Phase 1: Download-transcribe loop (keeps disk bounded)
    while true; do
        BATCH_NUM=$((BATCH_NUM + 1))
        echo "--- [Batch $BATCH_NUM] Downloading up to $BATCH_SIZE lectures ---"

        set +e
        python scripts/01_download.py --batch-size $BATCH_SIZE
        DOWNLOAD_CODE=$?
        set -e

        echo "--- [Batch $BATCH_NUM] Transcribing ---"
        python scripts/02_transcribe.py

        if [ $DOWNLOAD_CODE -eq 0 ]; then
            echo "--- All channels fully downloaded after $BATCH_NUM batches ---"
            break
        fi
    done

    # Phase 2: Tag all transcripts
    echo "=== [3/3] Tagging with Gemini ==="
    python scripts/03_tag.py

    # Phase 3: Upload to Notion
    echo "=== [4/3] Uploading to Notion ==="
    python scripts/04_upload_notion.py

    # Phase 4: GitHub Final Commit
    # audio/ is gitignored (ephemeral); all other data/ files are tracked
    echo "=== [4/4] Final GitHub commit ==="
    git add archive.txt
    git add config/*.yaml
    git add data/transcripts/*.json data/tagged/*.json 2>/dev/null || true
    git add data/unresolved_speakers.txt data/uploaded.txt 2>/dev/null || true
    git diff --cached --quiet || git commit -m "pipeline complete: $(date +'%Y-%m-%d %H:%M') — $(wc -l < archive.txt) videos processed"
    git push origin main

    echo "=== Pipeline complete ==="
    ```
  - Notes: `BATCH_SIZE=50` keeps disk usage to ~3–6 GB per cycle; `set +e` around download allows exit code 101 (more batches remain) without halting the script; `set -e` is restored immediately after; safe to re-run after any failure — all scripts skip already-completed work
  - **Git checkpoint strategy:** `archive.txt` + new `data/transcripts/*.json` are committed to GitHub after EACH batch inside the while loop — preserves GPU transcription work and download progress if pod dies mid-run. `data/tagged/*.json` and `data/uploaded.txt` are committed in Phase 4 (they don't exist until after the loop). `data/audio/` is gitignored (ephemeral); all other `data/` files are tracked.
  - **Per-batch checkpoint location:** inside the while loop after `02_transcribe.py` completes, before the `DOWNLOAD_CODE -eq 0` break check
  - **Commit guards:** `git add ... 2>/dev/null || true` handles empty directories (safe no-op); `git diff --cached --quiet || git commit` skips commit if nothing new to stage (e.g., all batch videos already archived)
  - **Recovery workflow:** pod dies → new pod → `git pull` → transcript JSONs + `archive.txt` restored → `bash run_pipeline.sh` → completed batches skipped automatically; only the current incomplete batch redone

### Acceptance Criteria

- [x] **AC 1:** Given `config/channels.yaml` contains N speaker channel URLs, when `01_download.py` runs, then all audio files appear in `data/audio/` named by video ID (`{id}.mp3`), `archive.txt` contains one entry per downloaded video, and `data/audio/{id}.info.json` files are present with video metadata.

- [x] **AC 2:** Given a video ID already exists in `archive.txt`, when `01_download.py` runs again, then that video is skipped without re-downloading and the `data/audio/` file count does not increase.

- [x] **AC 3a:** Given a video title that matches a canonical speaker in `config/speakers.yaml` (e.g., `"Core of Spiritual Life | Sri Vasudev Keshava Dasa | SB 6.12.22"`), when `resolve_speaker()` is called, then it returns the exact canonical name string from `speakers.yaml`.

- [x] **AC 3b:** Given a video title with no fuzzy match above threshold 85, when `resolve_speaker()` is called, then it returns `None` and appends the full title to `data/unresolved_speakers.txt`.

- [x] **AC 3c:** Given a video title cannot be resolved to a canonical speaker in `config/speakers.yaml`:

  **Primary (01_download.py match_filter):** When `01_download.py` encounters this video, `match_filter` returns a skip reason; no audio is downloaded; the video ID is NOT added to `archive.txt`; and the unresolved title is logged to `data/unresolved_speakers.txt`. After adding the missing speaker to `speakers.yaml` and re-running the pipeline, this video is automatically discovered and downloaded.

  **Safety net (02_transcribe.py):** If an unresolved-speaker audio file somehow reaches `02_transcribe.py` (e.g., `speakers.yaml` was modified between stages), then no transcript JSON is created, the audio file is deleted, and the pipeline continues to the next video without error.

- [x] **AC 3d:** Given audio files exist in `data/audio/` for videos with resolved speakers, when `02_transcribe.py` runs, then each produces `data/transcripts/{video_id}.json` with a `segments` array (each entry has `start`, `end`, `text`), `speaker` contains the canonical name, and the source audio file is deleted from `data/audio/`.

- [x] **AC 4:** Given `data/transcripts/{video_id}.json` already exists, when `02_transcribe.py` runs again, then that video prints "Skipping {video_id}" and no re-transcription or deletion occurs.

- [x] **AC 5:** Given `data/transcripts/{video_id}.json` exists, when `03_tag.py` runs, then `data/tagged/{video_id}.json` is created where each entry in `tagged_segments` has: `start_time`, `end_time`, `timestamp_url`, `verse_references`, `themes`, `content_type` (one of: story/analogy/philosophy/practical), `circle_fit`, `key_quote`, `summary`.

- [x] **AC 6:** Given the Gemini 15 RPM limit, when `03_tag.py` processes 100 consecutive lectures, then no `429 ResourceExhausted` errors occur (4s sleep between calls keeps throughput at ≤15 RPM).

- [x] **AC 7:** Given `data/tagged/{video_id}.json` exists and that `video_id` is not in `data/uploaded.txt`, when `04_upload_notion.py` runs, then one Notion row is created per tagged segment with all 13 properties populated, and `video_id` is appended to `data/uploaded.txt`.

- [x] **AC 8:** Given a `video_id` is already in `data/uploaded.txt`, when `04_upload_notion.py` runs, then that video prints "Already uploaded: {video_id}" and no duplicate Notion rows are created.

- [x] **AC 9:** Given the full pipeline has completed for at least 3 lectures, when filtering the Notion database by a Verse Reference (e.g., "BG 2.47"), then matching rows appear and clicking any Timestamp URL opens YouTube at the exact segment start time.

- [x] **AC 10:** Given the pipeline fails midway (e.g., Gemini API error at lecture 500 of 1000), when the error is resolved and `bash run_pipeline.sh` is re-run, then stages 1–2 skip entirely (already done), stage 3 resumes from the first untagged video, and no data is duplicated.

## Additional Context

### Dependencies

- **RunPod account** with GPU pod access (A40 recommended)
- **Google AI Studio API key** — already obtained
- **Notion account** with API access (integration token needed)
- **yt-dlp**, **faster-whisper**, **google-generativeai**, **notion-client** installed on RunPod pod
- **Python 3.10+** on RunPod pod

### Testing Strategy

**Step 0 — Speaker resolution dry run (before any download):**
1. Populate `config/speakers.yaml` with all known speakers
2. Run `resolve_speaker()` manually against 5–10 representative video titles (copy from YouTube)
3. Verify correct canonical names returned; check `data/unresolved_speakers.txt` for false negatives
4. Adjust `threshold` down (e.g., to 80) if valid speakers are being missed, up (e.g., to 90) if wrong matches appear
5. **Retry workflow (automatic):** Since unresolved videos are filtered at download time by `match_filter` in `01_download.py`, they are never added to `archive.txt`. After adding a missing speaker name to `speakers.yaml`, simply re-run `run_pipeline.sh` — the previously-skipped videos will be automatically discovered and downloaded on the next run. No manual `archive.txt` editing required.

**Step 1 — Single-channel smoke test (before full bulk run):**
1. Set `config/channels.yaml` to ONE channel URL only
2. Temporarily add `'playlistend': 3` to yt-dlp options in `01_download.py` (downloads only first 3 videos)
3. Run `bash run_pipeline.sh` end-to-end
4. Verify: 3 audio files downloaded → resolved-speaker videos transcribed → tagged JSONs → Notion rows created
5. Inspect `data/unresolved_speakers.txt` — titles of skipped videos appear here; add missing speaker names to `speakers.yaml` and re-run
6. Open Notion DB and manually confirm: Speaker property matches canonical name, Timestamp URLs clickable and land at correct moment
7. Re-run `bash run_pipeline.sh` — confirm all stages print only "Skipping" messages (idempotency)
8. Remove `'playlistend': 3` and restore all channels for the full run

**Step 2 — Per-stage idempotency verification:**
- Run each script twice on the same data; second run must produce zero new files and print only skip messages

**Step 3 — Notion schema spot-check (after first 5 real videos):**
- Check: verse references are non-empty, themes make sense, content_type is valid, circle_fit values are 1–4, Timestamp URLs click through to the right YouTube moment

### Notes

- **Pre-mortem / high-risk items:**
  - Speaker resolution false negatives: if a speaker's title format is unusual (e.g., abbreviated names, mixed languages), `resolve_speaker()` may return `None` and skip valid lectures. Mitigation: Step 0 dry run before full bulk execution; inspect `data/unresolved_speakers.txt` after any run; add missing names to `speakers.yaml` and re-run — unresolved videos were never added to `archive.txt` (filtered at download time by `match_filter`), so they are retried automatically on the next pipeline run without any manual intervention.
  - Gemini JSON parsing: handled via regex fallback + key validation + `failed_tag.txt` logging (see Task 7) — pipeline never crashes on a bad Gemini response
  - Verse reference inconsistency: constrained by prompt format rule (`"BG X.Y"` / `"SB X.Y.Z"` only) — Notion multi_select filters will match reliably
  - Timestamp URL format: use `https://youtu.be/{video_id}?t={seconds}` — never append `?t=` to a `watch?v=` URL (double-`?` bug)
  - Transcript JSON corruption from pod kill: prevented by atomic `.json.tmp` → rename write pattern (see Task 6); stale `.json.tmp` files cleaned at startup
  - Audio orphaned after archive-loss re-download: `02_transcribe.py` deletes audio even on skip if transcript already exists (see Task 6 skip logic)
  - Notion rich_text 2000-char limit: transcripts will often exceed this — always truncate to 2000 chars
  - RunPod disk: bounded by `BATCH_SIZE=50` download-transcribe loop — max ~3–6 GB audio on disk at any time; increase BATCH_SIZE only if pod has more storage
  - yt-dlp updates: YouTube changes APIs frequently; run `pip install -U yt-dlp` on the RunPod pod before starting
  - ffmpeg: must be installed on the pod (`apt-get install -y ffmpeg`) before running `01_download.py`

- **Known limitations:**
  - Gemini's segmentation quality varies — some lectures may produce poorly-bounded segments or miss verse references; acceptable for first-pass index; failed videos logged to `data/failed_tag.txt` for manual review
  - **Partial upload duplicates (F5):** If `04_upload_notion.py` is killed mid-video, re-running will re-upload all segments for that video (creating duplicates). Detection: filter Notion by `Timestamp URL` — duplicates share the same URL. `uploaded.txt` prevents full-video re-uploads; only mid-video interruptions are vulnerable.
  - Notion multi_select values accumulate over time (e.g., misspelled theme tags); no cleanup mechanism built in
  - No mechanism to detect lectures deleted from YouTube after download

- **Future considerations (out of scope):**
  - Incremental ingestion: auto-detect and process new uploads from configured channels
  - Per-segment deduplication in Notion: query existing rows by `Timestamp URL` before inserting to fully prevent duplicates even on mid-video interruption
  - Segment transcript retrieval: reconstruct full segment text from original Whisper segments for richer Notion content

## Review Notes

- Adversarial review completed (2026-02-27)
- Findings: 15 total, 9 fixed, 6 skipped
- Resolution approach: walk-through
- Fixed: F1 (partial upload tracking), F2 (broken retry loop), F3 (set -e transcription crash), F5 (download error logging), F8 (relative path defaults), F9 (transcription exception handling), F10 (archive.txt now tracked by git), F11 (transcript truncation warning), F12 (pipeline echo labels)
- Skipped: F4 (audio deletion on unresolved speaker), F6 (brace interpolation in prompt), F7 (greedy JSON regex), F13 (bare KeyError on missing env vars), F14 (loose dependency versions), F15 (.webm glob undocumented)
