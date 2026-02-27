---
stepsCompleted: [1, 2]
inputDocuments: []
workflowType: 'research'
lastStep: 1
research_type: 'technical'
research_topic: 'yt-dlp channel and playlist download strategy with duplicate video handling'
research_goals: 'Design a reliable strategy for downloading all playlists from specific YouTube channels using yt-dlp, with intelligent duplicate video handling across playlists, for bulk transcription preprocessing (~1000 lectures, any number of speakers with dynamically managed channel list)'
user_name: 'Dwarakadas'
date: '2026-02-27'
web_research_enabled: true
source_verification: true
---

# Research Report: Technical

**Date:** 2026-02-27
**Author:** Dwarakadas
**Research Type:** Technical

---

## Research Overview

[Research overview and methodology will be appended here]

---

## Technical Research Scope Confirmation

**Research Topic:** yt-dlp channel and playlist download strategy with duplicate video handling
**Research Goals:** Design a reliable strategy for downloading all playlists from specific YouTube channels using yt-dlp, with intelligent duplicate video handling across playlists, for bulk transcription preprocessing (~1000 lectures, any number of speakers with dynamically managed channel list)

**Technical Research Scope:**

- Architecture Analysis - design patterns, frameworks, system architecture
- Implementation Approaches - development methodologies, coding patterns
- Technology Stack - languages, frameworks, tools, platforms
- Integration Patterns - APIs, protocols, interoperability
- Performance Considerations - scalability, optimization, patterns

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information
- Comprehensive technical coverage with architecture-specific insights

**Scope Confirmed:** 2026-02-27

## Technology Stack Analysis

### Core Tool: yt-dlp

yt-dlp is the primary and only required tool for the download stage. It is a feature-rich fork of youtube-dl, actively maintained, and the de facto standard for YouTube bulk downloading in 2025–2026.

_Language:_ Python (pip installable: `pip install -U yt-dlp`)
_Interface:_ CLI with a full Python API (`YoutubeDL` class)
_Status:_ Actively maintained — keeping it updated is critical as YouTube frequently changes delivery methods.

_Source:_ [GitHub - yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | [PyPI](https://pypi.org/project/yt-dlp/)

---

### Channel & Playlist Download Patterns

**Channel-level download (all videos in a channel):**
```bash
yt-dlp "https://www.youtube.com/@ChannelHandle"
```

**Channel-level download with playlist-organized output:**
```bash
yt-dlp -x --audio-format mp3 \
  -o "%(uploader)s/%(playlist)s/%(playlist_index)s - %(title)s.%(ext)s" \
  "https://www.youtube.com/@ChannelHandle"
```
This downloads all videos and organizes them by playlist, preserving playlist membership in the directory structure.

**Batch file for multiple channels:**
```bash
# channels.txt — one URL per line
yt-dlp --batch-file channels.txt
```

_Source:_ [End Point Dev - Download YouTube Channel](https://www.endpointdev.com/blog/2025/09/how-to-download-youtube-channel/) | [corbpie.com - yt-dlp playlists](https://write.corbpie.com/downloading-youtube-videos-and-playlists-with-yt-dlp/)

---

### Duplicate Video Handling: `--download-archive`

This is the **central mechanism** for deduplication across playlists and re-runs.

**How it works:**
- `--download-archive archive.txt` writes each successfully downloaded video's ID (format: `youtube VIDEOID`) to a text file
- On subsequent runs, any video ID found in the archive is **skipped entirely** — no re-download, no re-processing
- Works across playlists: if `VideoX` appears in Playlist A and Playlist B, it is downloaded once and skipped the second time

```bash
yt-dlp --download-archive archive.txt \
  -o "%(uploader)s/%(playlist)s/%(title)s.%(ext)s" \
  "https://www.youtube.com/@SpeakerChannel"
```

**Key behavior (confirmed by yt-dlp issues):**
- The archive is a flat text file — one entry per line: `youtube <video_id>`
- A single shared archive across all speakers' channel downloads is the recommended approach for global deduplication (scales to any number of speakers)
- `--no-overwrites` pairs well with `--download-archive` to skip files already on disk even if not in archive

**Known limitation:**
- If a video legitimately appears in multiple playlists and you want one file *per playlist occurrence*, `--download-archive` will prevent this (it skips on second encounter). For this project, downloading each unique video once is the correct behavior — so this limitation is actually an advantage.

_Source:_ [yt-dlp --download-archive flag (neilzone.co.uk)](https://neilzone.co.uk/2026/01/yt-dlps---download-archive-flag/) | [yt-dlp Issue #2754](https://github.com/yt-dlp/yt-dlp/issues/2754) | [ArchWiki yt-dlp](https://wiki.archlinux.org/title/Yt-dlp)

---

### Rate Limiting & Throttling Avoidance

YouTube actively throttles automated downloaders. The recommended defensive strategy:

| Option | Recommended Value | Purpose |
|--------|-------------------|---------|
| `--sleep-interval` | 10–30 seconds | Minimum delay between downloads |
| `--max-sleep-interval` | 60–120 seconds | Maximum delay (randomized in range) |
| `--sleep-requests` | 3–5 seconds | Delay between metadata requests |
| `-r / --rate-limit` | `2M`–`5M` | Cap download bandwidth per video |

**Practical command:**
```bash
yt-dlp --sleep-interval 15 --max-sleep-interval 45 \
       --sleep-requests 3 -r 3M \
       --download-archive archive.txt \
       ...
```

_Note:_ yt-dlp adds ±50% random variation to sleep intervals by default, which helps avoid pattern detection.

_Source:_ [yt-dlp Issue #11897 - sleep options](https://github.com/yt-dlp/yt-dlp/issues/11897) | [VideoHelp Forum - YT throttling](https://forum.videohelp.com/threads/406862-YT-throttling-and-how-to-fix-it-(YT-DLP)) | [rapidseedbox.com yt-dlp guide](https://www.rapidseedbox.com/blog/yt-dlp-complete-guide)

---

### Python API for Metadata & Orchestration

For multi-channel orchestration, yt-dlp's Python API is preferable to shell scripting:

```python
from yt_dlp import YoutubeDL

# Metadata-only (no download) — for pre-flight inventory
with YoutubeDL({'quiet': True, 'dump_single_json': True}) as ydl:
    info = ydl.extract_info(channel_url, download=False)

# Actual download with options
opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
    'outtmpl': '%(uploader)s/%(playlist)s/%(title)s.%(ext)s',
    'download_archive': 'archive.txt',
    'sleep_interval': 15,
    'max_sleep_interval': 45,
}
with YoutubeDL(opts) as ydl:
    ydl.download([channel_url])
```

_Source:_ [OSTechNix yt-dlp Tutorial](https://ostechnix.com/yt-dlp-tutorial/) | [VideoHelp - Batch Download](https://forum.videohelp.com/threads/403844-YT-DLP-Learning-To-Batch-Download)

---

### Metadata Preservation for Downstream Pipeline

For the transcription pipeline, key metadata to preserve alongside audio files:

| Metadata Field | yt-dlp Flag | Purpose |
|----------------|-------------|---------|
| Video info JSON | `--write-info-json` | Preserve title, description, upload date, video ID, channel |
| Thumbnail | `--write-thumbnail` | Optional; useful for Notion display |
| Subtitles | `--write-subs` | If available, can supplement Whisper transcription |

The video ID from the info JSON is the critical link back to the YouTube timestamp URL:
`https://youtu.be/{video_id}?t={seconds}`

_Source:_ [tglyn.ch - Automate YouTube Playlist Downloads](https://www.tglyn.ch/blog/youtube_download_for_djs/) | [blacktree.nl - Automate Channels with PowerShell](https://blacktree.nl/2024/03/05/automate-downloading-of-youtube-playlists-with-yt-dlp-and-powershell/)

<!-- Content will be appended sequentially through research workflow steps -->
