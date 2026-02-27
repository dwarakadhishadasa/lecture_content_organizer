"""
04_upload_notion.py — Upload tagged segments to Notion database.

For each tagged JSON in data/tagged/:
  - Skips if video_id is already in data/uploaded.txt
  - Creates one Notion row per segment with all 13 properties
  - Appends video_id to data/uploaded.txt after all segments uploaded

Idempotent at the video level: re-running skips fully-uploaded videos.
Note: mid-video interruption may create duplicates for that video (see known limitations).

Rate limit: 3 req/sec → 0.35s sleep between page creates.
"""
import datetime
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

load_dotenv()


def load_uploaded(path: Path) -> set[str]:
    if not path.exists():
        path.write_text("")
    return set(path.read_text().splitlines())


def get_segment_transcript(video_id: str, start_time: float, end_time: float) -> str:
    """Reconstruct segment text from original Whisper segments by time range."""
    transcript_path = Path(f"data/transcripts/{video_id}.json")
    if not transcript_path.exists():
        return ""
    with open(transcript_path) as f:
        transcript = json.load(f)
    texts = [
        seg["text"] for seg in transcript.get("segments", [])
        if seg["start"] >= start_time and seg["end"] <= end_time
    ]
    return " ".join(texts)


def create_page_with_retry(client: Client, db_id: str, properties: dict, video_id: str) -> bool:
    """Create a Notion page, retrying once on 429. Returns True on success."""
    last_error = None
    for attempt in range(2):
        try:
            client.pages.create(
                parent={"database_id": db_id},
                properties=properties,
            )
            return True
        except APIResponseError as e:
            last_error = e
            if e.status == 429 and attempt == 0:
                print(f"  [WARN] Notion rate limit hit for {video_id} — sleeping 60s")
                time.sleep(60)
                continue  # retry the API call
            break
    print(f"  [ERROR] Notion API error for {video_id}: {last_error}")
    return False


def main():
    client = Client(auth=os.environ["NOTION_API_KEY"])
    db_id = os.environ["NOTION_DATABASE_ID"]

    uploaded_path = Path("data/uploaded.txt")
    uploaded = load_uploaded(uploaded_path)

    tagged_files = sorted(Path("data/tagged").glob("*.json"))
    total = len(tagged_files)

    if total == 0:
        print("[04_upload] No tagged files found in data/tagged/")
        return

    for n, tagged_path in enumerate(tagged_files, 1):
        video_id = tagged_path.stem

        if video_id in uploaded:
            print(f"[{n}/{total}] Already uploaded: {video_id}")
            continue

        print(f"[{n}/{total}] Uploading {video_id}...")

        with open(tagged_path) as f:
            data = json.load(f)

        segments = data["segments"]
        speaker = data["speaker"]
        title = data["title"]
        youtube_url = data["youtube_url"]
        today = datetime.date.today().isoformat()

        all_ok = True
        for seg in segments:
            segment_transcript = get_segment_transcript(
                video_id, seg["start_time"], seg["end_time"]
            )
            if len(segment_transcript) > 2000:
                print(f"  [WARN] Transcript truncated to 2000 chars for {video_id} "
                      f"@{seg['start_time']}s (full: {len(segment_transcript)} chars)")

            properties = {
                "Segment Title": {"title": [{"text": {"content": seg["key_quote"][:2000]}}]},
                "Speaker": {"select": {"name": speaker}},
                "Video Title": {"rich_text": [{"text": {"content": title[:2000]}}]},
                "Timestamp URL": {"url": seg["timestamp_url"]},
                "Start Time": {"number": seg["start_time"]},
                "End Time": {"number": seg["end_time"]},
                "Verse References": {"multi_select": [{"name": v} for v in seg["verse_references"]]},
                "Themes": {"multi_select": [{"name": t} for t in seg["themes"]]},
                "Content Type": {"select": {"name": seg["content_type"]}},
                "Circle Fit": {"multi_select": [{"name": str(c)} for c in seg["circle_fit"]]},
                "Key Quote": {"rich_text": [{"text": {"content": seg["key_quote"][:2000]}}]},
                "Transcript": {"rich_text": [{"text": {"content": segment_transcript[:2000]}}]},
                "Upload Date": {"date": {"start": today}},
            }

            if not create_page_with_retry(client, db_id, properties, video_id):
                all_ok = False
            time.sleep(0.35)  # 3 req/sec limit

        # Only mark uploaded if all segment creates succeeded
        if all_ok:
            with open(uploaded_path, "a") as f:
                f.write(video_id + "\n")
            uploaded.add(video_id)
            print(f"[{n}/{total}] Uploaded {video_id} ({len(segments)} segments)")
        else:
            print(f"[{n}/{total}] PARTIAL UPLOAD for {video_id} — NOT marked done; re-run to retry")

    print("[04_upload] Done.")


if __name__ == "__main__":
    main()
