"""
03_tag.py — AI-tag transcripts using Gemini 2.0 Flash via Vertex AI Batch API.

For each transcript in data/transcripts/:
  - Builds a JSONL batch request file, uploads to GCS
  - Submits a Vertex AI batch prediction job and waits for completion
  - Downloads output, parses responses, writes data/tagged/{video_id}.json
  - Logs failures to data/failed_tag.txt for manual review and retry

Idempotent: skips videos where tagged JSON already exists.
No per-request rate limiting needed — batch API handles throughput internally.
"""
import datetime
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import aiplatform, storage

load_dotenv()

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"].strip()
LOCATION = "us-central1"
MODEL_NAME = "publishers/google/models/gemini-2.0-flash-001"
BUCKET_NAME = f"{PROJECT_ID}-lco-tagger"


PROMPT_TEMPLATE = """\
VIDEO_ID: {video_id}
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
Do not include transcript text or any extra keys beyond the schema above.
THEMES FIELD: Choose 1-3 descriptive themes per segment.
"""

MODEL_REQUIRED_KEYS = {"start_time", "end_time", "verse_references", "themes",
                       "content_type", "circle_fit", "key_quote", "summary"}

# All non-Latin foreign scripts: Devanagari, Arabic/Urdu, Tamil, Telugu, Bengali,
# Cyrillic, CJK — used to strip segments before sending to Gemini.
# Devanagari: Sanskrit verses embedded in lectures.
# Others: foreign-language audio versions + Whisper hallucination artifacts.
_FOREIGN = re.compile(
    r"[\u0900-\u097F"   # Devanagari
    r"\u0600-\u06FF"    # Arabic / Urdu
    r"\u0980-\u09FF"    # Bengali
    r"\u0B80-\u0BFF"    # Tamil
    r"\u0C00-\u0C7F"    # Telugu
    r"\u0400-\u04FF"    # Cyrillic (Whisper hallucination)
    r"\u4E00-\u9FFF"    # CJK Unified (Whisper hallucination)
    r"\u3040-\u30FF]"   # Hiragana / Katakana (Whisper hallucination)
)


def _foreign_char_ratio(text: str) -> float:
    """Fraction of characters in text that belong to a non-Latin foreign script."""
    if not text:
        return 0.0
    return sum(1 for c in text if _FOREIGN.match(c)) / len(text)


def ensure_bucket(gcs: storage.Client) -> None:
    """Create GCS bucket in us-central1 if it doesn't exist."""
    try:
        gcs.get_bucket(BUCKET_NAME)
        print(f"[GCS] Using existing bucket: gs://{BUCKET_NAME}")
    except NotFound:
        gcs.create_bucket(BUCKET_NAME, location=LOCATION)
        print(f"[GCS] Created bucket: gs://{BUCKET_NAME}")


def build_input_jsonl(transcript_paths: list[Path], failed_log: Path) -> str:
    """Build a JSONL string with one Gemini request per transcript.

    Embeds VIDEO_ID: {id} as the first line of each prompt so results
    can be correlated back to source files after batch completion.
    Transcripts that fail to load or parse are logged to failed_log and skipped.
    """
    lines = []
    for path in transcript_paths:
        video_id = path.stem
        try:
            with open(path) as f:
                t = json.load(f)

            # Strip segments that are predominantly foreign script before sending to Gemini.
            # Catches: Sanskrit verse recitations (Devanagari), Urdu/Tamil audio versions,
            # and Whisper hallucination artifacts (stray Cyrillic/CJK characters).
            transcript_text = "\n".join(
                f"[{int(seg['start'])}s] {seg['text']}"
                for seg in t["segments"]
                if _foreign_char_ratio(seg["text"]) < 0.6
            )
            prompt = PROMPT_TEMPLATE.format(
                video_id=video_id,
                speaker=t["speaker"],
                title=t["title"],
                youtube_url=t["youtube_url"],
                transcript_text=transcript_text,
            )
            request = {
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 8192,
                        "responseMimeType": "application/json",
                    },
                }
            }
            lines.append(json.dumps(request, ensure_ascii=False))
        except Exception as e:
            print(f"  [WARN] Skipping {video_id}: failed to build request: {e}")
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
    return "\n".join(lines)


def upload_jsonl(gcs: storage.Client, content: str, gcs_path: str) -> str:
    """Upload JSONL content to GCS. Returns gs:// URI."""
    blob = gcs.bucket(BUCKET_NAME).blob(gcs_path)
    blob.upload_from_string(content, content_type="application/jsonl")
    uri = f"gs://{BUCKET_NAME}/{gcs_path}"
    print(f"[GCS] Uploaded input to {uri}")
    return uri


def submit_and_wait(input_uri: str, output_prefix: str) -> aiplatform.BatchPredictionJob:
    """Submit a Vertex AI batch prediction job and block until complete."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    job = aiplatform.BatchPredictionJob.create(
        job_display_name=f"lecture-tagger-{ts}",
        model_name=MODEL_NAME,
        gcs_source=[input_uri],
        gcs_destination_prefix=output_prefix,
    )
    print(f"[Vertex] Job submitted: {job.name}  state: {job.state.name}")
    print("[Vertex] Waiting for completion (this may take several minutes)...")
    job.wait()
    print(f"[Vertex] Job done. Final state: {job.state.name}")
    return job


def fetch_results(gcs: storage.Client, output_prefix_uri: str) -> list[dict]:
    """Download and parse all JSONL output files under the given GCS prefix."""
    path = output_prefix_uri.removeprefix("gs://")
    bucket_name, prefix = path.split("/", 1)
    blobs = gcs.bucket(bucket_name).list_blobs(prefix=prefix.rstrip("/"))

    results = []
    for blob in blobs:
        if not blob.name.endswith(".jsonl"):
            continue
        for line in blob.download_as_text().splitlines():
            if line.strip():
                results.append(json.loads(line))
    print(f"[GCS] Fetched {len(results)} result lines")
    return results


def strip_json_fences(raw: str) -> str:
    return re.sub(r"```json|```", "", raw).strip()


def parse_segments(raw: str) -> list[dict] | None:
    data = json.loads(raw)
    segments = data.get("segments")
    return segments if isinstance(segments, list) else None


def is_valid_segment(segment: dict) -> bool:
    return isinstance(segment, dict) and MODEL_REQUIRED_KEYS.issubset(segment.keys())


def sanitize_json_text(raw: str) -> str:
    """Repair common model-output JSON issues without changing semantics."""
    out: list[str] = []
    stack: list[str] = []
    in_string = False
    escape = False

    i = 0
    while i < len(raw):
        ch = raw[i]

        if in_string:
            if escape:
                if ch == "\n":
                    out.append("n")
                elif ch == "\r":
                    out.append("r")
                elif ch == "\t":
                    out.append("t")
                else:
                    out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                j = i + 1
                while j < len(raw) and raw[j] in " \t\r\n":
                    j += 1
                next_sig = raw[j] if j < len(raw) else ""
                if next_sig and next_sig not in ",}]:":
                    out.append('\\"')
                else:
                    out.append(ch)
                    in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 32:
                out.append(" ")
            else:
                out.append(ch)
        else:
            if ch == '"':
                out.append(ch)
                in_string = True
            else:
                out.append(ch)
                if ch in "{[":
                    stack.append(ch)
                elif ch == "}" and stack and stack[-1] == "{":
                    stack.pop()
                elif ch == "]" and stack and stack[-1] == "[":
                    stack.pop()
        i += 1

    if in_string:
        out.append('"')

    repaired = "".join(out)
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)

    closers = []
    for opener in reversed(stack):
        closers.append("}" if opener == "{" else "]")
    repaired += "".join(closers)
    return repaired


def salvage_segment_objects(cleaned: str) -> tuple[list[dict] | None, str]:
    marker = '"start_time"'
    positions = [m.start() for m in re.finditer(marker, cleaned)]
    if not positions:
        return None, "no segment markers found"

    segments: list[dict] = []
    for idx, pos in enumerate(positions):
        obj_start = cleaned.rfind("{", 0, pos)
        if obj_start == -1:
            continue

        next_pos = positions[idx + 1] if idx + 1 < len(positions) else len(cleaned)
        obj_end = cleaned.rfind("}", pos, next_pos)
        if obj_end == -1:
            obj_end = cleaned.find("}", pos)
        if obj_end == -1:
            continue

        candidate = sanitize_json_text(cleaned[obj_start:obj_end + 1])
        try:
            segment = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if is_valid_segment(segment):
            segments.append(segment)

    if segments:
        return segments, f"salvaged {len(segments)} complete segment objects"
    return None, "could not salvage any complete segments"


def recover_segments(raw: str) -> tuple[list[dict] | None, str]:
    cleaned = strip_json_fences(raw)
    if not cleaned:
        return None, "empty response"

    start = cleaned.find("{")
    if start == -1:
        return None, "no JSON object found"
    cleaned = cleaned[start:]

    try:
        segments = parse_segments(cleaned)
        if segments is not None:
            return segments, "already valid"
        return None, "missing segments key"
    except json.JSONDecodeError as err:
        original_error = str(err)

    repaired = sanitize_json_text(cleaned)
    try:
        segments = parse_segments(repaired)
    except json.JSONDecodeError as err:
        salvaged, salvage_status = salvage_segment_objects(cleaned)
        if salvaged is not None:
            return salvaged, salvage_status
        return None, f"repair failed: {err}; original: {original_error}"

    return segments, "repaired"


def parse_response(raw: str, video_id: str) -> list[dict] | None:
    """Parse Gemini response, attempting light repair before failing."""
    segments, status = recover_segments(raw)
    if segments is None:
        print(f"  [WARN] Could not parse {video_id}: {status}")
        return None

    valid = [s for s in segments if is_valid_segment(s)]
    if not valid:
        print(f"  [WARN] No valid segments for {video_id}")
        return None

    if status != "already valid":
        print(f"  [WARN] {video_id}: parsed with recovery ({status})")
    return valid


def build_segment_transcript(whisper_segments: list[dict], start_time: int, end_time: int) -> str:
    """Reconstruct the raw transcript text for a model-defined segment window."""
    chunks = [
        seg["text"].strip()
        for seg in whisper_segments
        if start_time <= seg.get("start", -1) < end_time and seg.get("text", "").strip()
    ]
    if not chunks:
        chunks = [
            seg["text"].strip()
            for seg in whisper_segments
            if seg.get("end", -1) > start_time and seg.get("start", float("inf")) < end_time
            and seg.get("text", "").strip()
        ]
    return " ".join(chunks)


def extract_video_id(request_text: str) -> str | None:
    """Extract VIDEO_ID marker from the embedded prompt text."""
    m = re.search(r"^VIDEO_ID: (\S+)", request_text)
    return m.group(1) if m else None


def main():
    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    gcs = storage.Client(project=PROJECT_ID)

    ensure_bucket(gcs)

    all_transcripts = sorted(Path("data/transcripts").glob("*.json"))
    if not all_transcripts:
        print("[03_tag] No transcript files found in data/transcripts/")
        return

    Path("data/tagged").mkdir(exist_ok=True)
    failed_log = Path("data/failed_tag.txt")

    untagged = [p for p in all_transcripts
                if not Path(f"data/tagged/{p.stem}.json").exists()]
    if not untagged:
        print("[03_tag] All transcripts already tagged.")
        return

    print(f"[03_tag] Tagging {len(untagged)}/{len(all_transcripts)} transcripts "
          f"via Vertex AI Batch API ({MODEL_NAME})...")

    # Build, upload, and submit batch job
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    input_uri = upload_jsonl(gcs, build_input_jsonl(untagged, failed_log),
                             f"batch_jobs/{ts}/input.jsonl")
    output_prefix = f"gs://{BUCKET_NAME}/batch_jobs/{ts}/output/"

    job = submit_and_wait(input_uri, output_prefix)

    if "FAILED" in job.state.name or "CANCELLED" in job.state.name:
        print(f"[ERROR] Batch job did not succeed: {job.state.name}")
        with open(failed_log, "a") as f:
            for p in untagged:
                f.write(p.stem + "\n")
        return

    # Process results
    results = fetch_results(gcs, output_prefix)

    tagged_count = 0
    failed_count = 0
    seen_ids: set[str] = set()

    for item in results:
        req_text = (item.get("request", {})
                        .get("contents", [{}])[0]
                        .get("parts", [{}])[0]
                        .get("text", ""))
        video_id = extract_video_id(req_text)

        if not video_id:
            print("  [WARN] Skipping item: VIDEO_ID not found in request text")
            failed_count += 1
            continue

        seen_ids.add(video_id)

        if item.get("status"):
            print(f"  [WARN] {video_id}: item-level error: {item['status']}")
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
            failed_count += 1
            continue

        candidates = item.get("response", {}).get("candidates", [])
        if not candidates:
            print(f"  [WARN] {video_id}: no candidates in response")
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
            failed_count += 1
            continue

        finish_reason = candidates[0].get("finishReason", "")
        if finish_reason and finish_reason != "STOP":
            print(f"  [WARN] {video_id}: incomplete model response ({finish_reason})")
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
            failed_count += 1
            continue

        raw_text = (candidates[0].get("content", {})
                                 .get("parts", [{}])[0]
                                 .get("text", ""))
        segments = parse_response(raw_text, video_id)
        if segments is None:
            with open(failed_log, "a") as f:
                f.write(video_id + "\n")
            failed_count += 1
            continue

        # Load metadata from source transcript
        with open(Path(f"data/transcripts/{video_id}.json")) as f:
            t = json.load(f)

        for seg in segments:
            seg["timestamp_url"] = f"https://youtu.be/{video_id}?t={int(seg['start_time'])}"
            seg["transcript"] = build_segment_transcript(
                t.get("segments", []),
                int(seg["start_time"]),
                int(seg["end_time"]),
            )

        Path(f"data/tagged/{video_id}.json").write_text(
            json.dumps({
                "video_id": video_id,
                "title": t["title"],
                "speaker": t["speaker"],
                "youtube_url": t["youtube_url"],
                "segments": segments,
            }, ensure_ascii=False, indent=2)
        )
        print(f"  Tagged {video_id} ({len(segments)} segments)")
        tagged_count += 1

    # Log any untagged videos that never appeared in batch results (partial batch failure).
    missing = [p.stem for p in untagged if p.stem not in seen_ids]
    if missing:
        print(f"  [WARN] {len(missing)} video(s) missing from batch results — logging to failed_tag.txt")
        with open(failed_log, "a") as f:
            for vid in missing:
                f.write(vid + "\n")
        failed_count += len(missing)

    print(f"[03_tag] Done. {tagged_count} tagged, {failed_count} failed.")
    if failed_count:
        print("  Check data/failed_tag.txt for video IDs to retry.")


if __name__ == "__main__":
    main()
