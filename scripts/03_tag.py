"""
03_tag.py — AI-tag transcripts using Gemini 1.5 Flash.

For each transcript in data/transcripts/:
  - Sends full transcript to Gemini with structured tagging prompt
  - Parses response with regex fallback for robustness
  - Writes data/tagged/{video_id}.json
  - Logs failures to data/failed_tag.txt for manual review and retry

Idempotent: skips videos where tagged JSON already exists.
Rate limit: 15 RPM → 4s sleep between calls.
"""
import json
import os
import re
import time
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

PROMPT_TEMPLATE = """\
You are analyzing a transcript from a Vaishnava lecture.
Speaker: {speaker}
Title: {title}
URL: {youtube_url}

Timestamped transcript:
{transcript_text}

Identify thematic segments of 5-10 minutes each. For each segment return ONLY valid JSON (no markdown fences):
{{
  "segments": [
    {{
      "start_time": <int seconds>,
      "end_time": <int seconds>,
      "verse_references": ["BG 2.47", "SB 1.2.6"],
      "themes": ["detachment", "karma"],
      "content_type": "story|analogy|philosophy|practical",
      "circle_fit": [1, 2],
      "key_quote": "Most impactful sentence from this segment",
      "summary": "One sentence describing this segment"
    }}
  ]
}}
circle_fit: 1=full-time devotees, 2=congregation/volunteers, 3=newcomers, 4=general public with no prior exposure
VERSE REFERENCE FORMAT (P1): Use ONLY "BG X.Y" for Bhagavad-gita and "SB X.Y.Z" for Srimad Bhagavatam.
No other formats ("Bg.", "Bhagavad-gita", chapter references without verse). If uncertain, use empty list [].
"""

REQUIRED_KEYS = {"start_time", "end_time", "verse_references", "themes",
                 "content_type", "circle_fit", "key_quote", "summary"}


def call_gemini_with_retry(model, prompt: str, video_id: str) -> str | None:
    """Call Gemini with exponential backoff. Returns response text or None on failure."""
    delays = [2, 4, 8]
    for attempt, delay in enumerate(delays, 1):
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            if attempt < len(delays):
                print(f"  [WARN] Gemini error (attempt {attempt}): {e} — retrying in {delay}s")
                time.sleep(delay)
            else:
                print(f"  [WARN] Gemini failed after {len(delays)} attempts for {video_id}: {e}")
                return None


def parse_gemini_response(raw: str, video_id: str) -> list[dict] | None:
    """Parse Gemini response with regex fallback. Returns valid segments or None."""
    # Strip markdown fences
    raw = re.sub(r"```json|```", "", raw).strip()

    # Fallback: extract first {...} block if prose wraps the JSON
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        print(f"  [WARN] No JSON block found for {video_id}")
        return None

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON decode error for {video_id}: {e}")
        return None

    valid_segments = [
        s for s in data.get("segments", [])
        if REQUIRED_KEYS.issubset(s.keys())
    ]
    if not valid_segments:
        print(f"  [WARN] No valid segments in response for {video_id}")
        return None

    return valid_segments


def main():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")

    transcript_files = sorted(Path("data/transcripts").glob("*.json"))
    total = len(transcript_files)

    if total == 0:
        print("[03_tag] No transcript files found in data/transcripts/")
        return

    Path("data/tagged").mkdir(exist_ok=True)
    failed_log = Path("data/failed_tag.txt")

    for n, transcript_path in enumerate(transcript_files, 1):
        video_id = transcript_path.stem
        tagged_path = Path(f"data/tagged/{video_id}.json")

        if tagged_path.exists():
            print(f"[{n}/{total}] Skipping {video_id} (already tagged)")
            continue

        print(f"[{n}/{total}] Tagging {video_id}...")

        with open(transcript_path) as f:
            transcript = json.load(f)

        title = transcript["title"]
        speaker = transcript["speaker"]
        youtube_url = transcript["youtube_url"]

        # Format transcript: one line per Whisper segment
        transcript_text = "\n".join(
            f"[{int(seg['start'])}s] {seg['text']}"
            for seg in transcript["segments"]
        )

        prompt = PROMPT_TEMPLATE.format(
            speaker=speaker,
            title=title,
            youtube_url=youtube_url,
            transcript_text=transcript_text,
        )

        raw_response = call_gemini_with_retry(model, prompt, video_id)
        if raw_response is None:
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
            continue

        valid_segments = parse_gemini_response(raw_response, video_id)
        if valid_segments is None:
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
            continue

        # Enrich each segment with timestamp URL (P2: use youtu.be format to avoid double-? bug)
        for seg in valid_segments:
            seg["timestamp_url"] = f"https://youtu.be/{video_id}?t={int(seg['start_time'])}"

        # Save tagged output (P5: use "segments" key for consistency with transcript JSON)
        output = {
            "video_id": video_id,
            "title": title,
            "speaker": speaker,
            "youtube_url": youtube_url,
            "segments": valid_segments,
        }
        tagged_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        print(f"[{n}/{total}] Tagged {video_id} ({len(valid_segments)} segments)")

        time.sleep(4)  # 15 RPM limit = max 1 request per 4s

    print(f"[03_tag] Done. Check data/failed_tag.txt for any failures to retry.")


if __name__ == "__main__":
    main()
