"""
01_download.py — Download audio from configured YouTube channels and playlists.

Exit codes:
  0   — All channels fully downloaded (no more videos to fetch)
  101 — Batch complete, more remain (run_pipeline.sh loops)

Usage:
  python scripts/01_download.py [--batch-size N]   # channel mode (default)
  python scripts/01_download.py --playlists         # playlist mode (known speakers)
"""
import argparse
import json
import sys
from pathlib import Path

import yaml
from yt_dlp import YoutubeDL
from yt_dlp.utils import MaxDownloadsReached

from scripts.utils.resolve_speaker import load_speakers, resolve_speaker

ERROR_LOG = "data/download_errors.txt"


class _DownloadLogger:
    """Captures yt-dlp errors to download_errors.txt while ignoreerrors suppresses crashes."""

    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        print(f"  [yt-dlp ERROR] {msg}")
        with open(ERROR_LOG, "a") as f:
            f.write(msg + "\n")


SPEAKER_MAP_PATH = Path("data/speaker_map.json")


def download_playlists():
    """
    Download all configured playlists, bypassing speaker resolution.
    Speaker name is taken directly from channels.yaml and written to
    data/speaker_map.json so 02_transcribe.py can use it without fuzzy matching.
    Idempotent: archive.txt deduplicates already-downloaded videos.
    """
    with open("config/channels.yaml") as f:
        config = yaml.safe_load(f)

    playlists = config.get("playlists") or []
    if not playlists:
        print("[01_download] No playlists configured — skipping playlist phase.")
        return

    # Load existing speaker_map (preserve entries from previous runs)
    speaker_map = {}
    if SPEAKER_MAP_PATH.exists():
        with open(SPEAKER_MAP_PATH) as f:
            speaker_map = json.load(f)

    for playlist in playlists:
        url = playlist["url"]
        speaker = playlist["speaker"]
        print(f"[01_download] Playlist: {url}")
        print(f"[01_download]   Speaker: {speaker}")

        newly_captured = []

        def progress_hook(d, _captured=newly_captured):
            if d["status"] == "finished":
                video_id = d.get("info_dict", {}).get("id")
                if video_id:
                    _captured.append(video_id)

        opts = {
            "format": "bestaudio/best",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            "outtmpl": "data/audio/%(id)s.%(ext)s",
            "download_archive": "archive.txt",
            "writeinfojson": True,
            "progress_hooks": [progress_hook],
            "sleep_interval": 15,
            "max_sleep_interval": 45,
            "sleep_requests": 3,
            "ignoreerrors": True,
            "logger": _DownloadLogger(),
        }

        with YoutubeDL(opts) as ydl:
            ydl.download([url])

        for video_id in newly_captured:
            speaker_map[video_id] = speaker

        # Write after each playlist — crash between playlists won't lose captured IDs
        SPEAKER_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SPEAKER_MAP_PATH, "w") as f:
            json.dump(speaker_map, f, ensure_ascii=False, indent=2)
        print(f"[01_download]   Done: {len(newly_captured)} new video(s) mapped to '{speaker}' "
              f"({len(speaker_map)} total entries in speaker_map.json)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Max lectures to download per run (default: 50)")
    parser.add_argument("--playlists", action="store_true",
                        help="Download configured playlists with known speakers (no batch limit)")
    args = parser.parse_args()

    if args.playlists:
        download_playlists()
        sys.exit(0)

    # Always download playlists first before processing channels
    download_playlists()

    batch_size = args.batch_size

    # Load channel URLs
    with open("config/channels.yaml") as f:
        config = yaml.safe_load(f)
    all_urls = [ch["url"] for ch in config["channels"]]

    # Load canonical speakers for pre-filter
    speakers = load_speakers("config/speakers.yaml")

    def speaker_match_filter(info_dict, *, incomplete=False):
        """
        Invoked by yt-dlp after fetching metadata, before any audio download.
        Returns a non-None string to skip the video; None to allow download.
        Skipped videos are NOT added to archive.txt and do NOT count against max_downloads.
        """
        if incomplete:
            return None  # Wait for full metadata before filtering
        title = info_dict.get("title", "")
        if resolve_speaker(title, speakers) is None:
            return "Speaker unresolved — skipping download"
        return None

    opts = {
        "format": "bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "outtmpl": "data/audio/%(id)s.%(ext)s",
        "download_archive": "archive.txt",
        "writeinfojson": True,
        "max_downloads": batch_size,
        "match_filter": speaker_match_filter,
        "sleep_interval": 15,
        "max_sleep_interval": 45,
        "sleep_requests": 3,
        "ignoreerrors": True,
        "logger": _DownloadLogger(),
    }

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download(all_urls)
        print(f"[01_download] All channels fully downloaded.")
        sys.exit(0)
    except MaxDownloadsReached:
        print(f"[01_download] Batch of {batch_size} downloaded. More remain.")
        sys.exit(101)


if __name__ == "__main__":
    main()
