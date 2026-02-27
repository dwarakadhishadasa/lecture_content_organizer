"""
01_download.py — Download audio from configured YouTube channels.

Exit codes:
  0   — All channels fully downloaded (no more videos to fetch)
  101 — Batch complete, more remain (run_pipeline.sh loops)

Usage:
  python scripts/01_download.py [--batch-size N]
"""
import argparse
import sys
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Max lectures to download per run (default: 50)")
    args = parser.parse_args()
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
