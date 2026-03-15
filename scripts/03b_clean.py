"""
03b_clean.py — Improve grammar and clarity of tagged transcript segments using
Gemini 2.0 Flash via Vertex AI Batch API.

For each tagged file in data/tagged/:
  - Sends segment transcripts in one or more chunked batch requests
  - Gemini fixes grammar, punctuation, and removes speech/transcription artifacts
  - Updates the transcript field in-place in data/tagged/{video_id}.json
  - Logs completed video IDs to data/cleaned.txt

Idempotent: skips video IDs already listed in data/cleaned.txt.
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
REQUEST_ID: {request_id}
VIDEO_ID: {video_id}
CHUNK_INDEX: {chunk_index}
TOTAL_CHUNKS: {total_chunks}
You are editing transcripts of Vaishnava lectures. Fix the grammar, punctuation, \
and clarity of each segment below. Remove repeated phrases caused by speech \
disfluencies or transcription artifacts (e.g. "He is in him. He is in him."). \
Preserve all Sanskrit terms, devotee names, scripture references, and \
philosophical concepts exactly as given. Do not add, remove, or summarize content.

Return ONLY valid JSON (no markdown fences, no explanations):
[
  {{"idx": <int>, "text": "<cleaned transcript>"}},
  ...
]
Return exactly one object per input segment and preserve every idx unchanged.

Segments:
{segments_json}
"""

MAX_SEGMENTS_PER_REQUEST = 10
MAX_CHARS_PER_REQUEST = 12000


def extract_video_id(request_text: str) -> str | None:
    m = re.search(r"^VIDEO_ID: (\S+)", request_text, re.MULTILINE)
    return m.group(1) if m else None


def extract_request_id(request_text: str) -> str | None:
    m = re.search(r"^REQUEST_ID: (\S+)", request_text, re.MULTILINE)
    return m.group(1) if m else None


def extract_chunk_metadata(request_text: str) -> tuple[int | None, int | None]:
    chunk_match = re.search(r"^CHUNK_INDEX: (\d+)", request_text, re.MULTILINE)
    total_match = re.search(r"^TOTAL_CHUNKS: (\d+)", request_text, re.MULTILINE)
    chunk_index = int(chunk_match.group(1)) if chunk_match else None
    total_chunks = int(total_match.group(1)) if total_match else None
    return chunk_index, total_chunks


def strip_json_fences(raw: str) -> str:
    return re.sub(r"```json|```", "", raw).strip()


def parse_tagged_segments(raw: str, video_id: str) -> list[dict] | None:
    cleaned = strip_json_fences(raw)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        print(f"  [WARN] No tagged JSON block found for {video_id}")
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"  [WARN] Tagged JSON decode error for {video_id}: {e}")
        return None
    segments = data.get("segments")
    if not isinstance(segments, list):
        print(f"  [WARN] Tagged response missing segments for {video_id}")
        return None
    return segments


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


def is_valid_cleaned_item(item: dict) -> bool:
    return (
        isinstance(item, dict)
        and "idx" in item
        and "text" in item
        and isinstance(item["idx"], int)
        and isinstance(item["text"], str)
    )


def parse_cleaned_array(raw: str) -> list[dict] | None:
    data = json.loads(raw)
    return data if isinstance(data, list) else None


def salvage_cleaned_items(cleaned: str) -> tuple[list[dict] | None, str]:
    marker = '"idx"'
    positions = [m.start() for m in re.finditer(marker, cleaned)]
    if not positions:
        return None, "no cleaned item markers found"

    items: list[dict] = []
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
            item = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if is_valid_cleaned_item(item):
            items.append(item)

    if items:
        return items, f"salvaged {len(items)} cleaned items"
    return None, "could not salvage any cleaned items"


def recover_cleaned_segments(raw: str) -> tuple[list[dict] | None, str]:
    cleaned = strip_json_fences(raw)
    if not cleaned:
        return None, "empty response"

    start = cleaned.find("[")
    if start == -1:
        return None, "no JSON array found"
    cleaned = cleaned[start:]

    try:
        items = parse_cleaned_array(cleaned)
        if items is not None:
            return items, "already valid"
        return None, "response was not a JSON array"
    except json.JSONDecodeError as err:
        original_error = str(err)

    repaired = sanitize_json_text(cleaned)
    try:
        items = parse_cleaned_array(repaired)
    except json.JSONDecodeError as err:
        salvaged, salvage_status = salvage_cleaned_items(cleaned)
        if salvaged is not None:
            return salvaged, salvage_status
        return None, f"repair failed: {err}; original: {original_error}"

    return items, "repaired"


def chunk_segments(segments_data: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for entry in segments_data:
        text_len = len(entry["text"])
        should_split = (
            current
            and (
                len(current) >= MAX_SEGMENTS_PER_REQUEST
                or current_chars + text_len > MAX_CHARS_PER_REQUEST
            )
        )
        if should_split:
            chunks.append(current)
            current = []
            current_chars = 0

        current.append(entry)
        current_chars += text_len

    if current:
        chunks.append(current)

    return chunks


def iter_tagged_docs(tagged_paths: list[Path]) -> list[tuple[Path, dict]]:
    docs: list[tuple[Path, dict]] = []

    for path in tagged_paths:
        with open(path) as f:
            data = json.load(f)

        if isinstance(data, dict):
            segments = data.get("segments")
            if isinstance(segments, list):
                docs.append((path, data))
                continue
            print(f"  [WARN] Skipping {path.name}: expected dict with 'segments' list")
            continue

        if not isinstance(data, list):
            print(f"  [WARN] Skipping {path.name}: unsupported JSON shape {type(data).__name__}")
            continue

        for item in data:
            req_text = (item.get("request", {})
                            .get("contents", [{}])[0]
                            .get("parts", [{}])[0]
                            .get("text", ""))
            video_id = extract_video_id(req_text)
            if not video_id:
                print(f"  [WARN] Skipping batch item in {path.name}: VIDEO_ID not found")
                continue

            if item.get("status"):
                print(f"  [WARN] Skipping {video_id} from {path.name}: item-level error: {item['status']}")
                continue

            candidates = item.get("response", {}).get("candidates", [])
            if not candidates:
                print(f"  [WARN] Skipping {video_id} from {path.name}: no candidates in response")
                continue

            raw_text = (candidates[0].get("content", {})
                                     .get("parts", [{}])[0]
                                     .get("text", ""))
            segments = parse_tagged_segments(raw_text, video_id)
            if segments is None:
                continue

            transcript_path = Path(f"data/transcripts/{video_id}.json")
            if not transcript_path.exists():
                print(f"  [WARN] Skipping {video_id}: source transcript not found")
                continue

            with open(transcript_path) as f:
                transcript = json.load(f)

            docs.append((
                Path(f"data/tagged/{video_id}.json"),
                {
                    "video_id": video_id,
                    "title": transcript["title"],
                    "speaker": transcript["speaker"],
                    "youtube_url": transcript["youtube_url"],
                    "segments": segments,
                },
            ))

    return docs


def ensure_bucket(gcs: storage.Client) -> None:
    try:
        gcs.get_bucket(BUCKET_NAME)
        print(f"[GCS] Using existing bucket: gs://{BUCKET_NAME}")
    except NotFound:
        gcs.create_bucket(BUCKET_NAME, location=LOCATION)
        print(f"[GCS] Created bucket: gs://{BUCKET_NAME}")


def build_input_jsonl(
    tagged_docs: list[tuple[Path, dict]]
) -> tuple[str, dict[str, set[str]], dict[str, set[int]]]:
    lines = []
    expected_requests: dict[str, set[str]] = {}
    expected_indices_by_request: dict[str, set[int]] = {}

    for path, t in tagged_docs:
        video_id = path.stem
        segments_data = [
            {"idx": i, "text": seg.get("transcript", "").strip()}
            for i, seg in enumerate(t.get("segments", []))
            if seg.get("transcript", "").strip()
        ]
        if not segments_data:
            continue

        segment_chunks = chunk_segments(segments_data)
        expected_requests[video_id] = set()

        for chunk_index, chunk in enumerate(segment_chunks, 1):
            request_id = f"{video_id}__chunk_{chunk_index:03d}"
            expected_requests[video_id].add(request_id)
            expected_indices_by_request[request_id] = {entry["idx"] for entry in chunk}
            prompt = PROMPT_TEMPLATE.format(
                request_id=request_id,
                video_id=video_id,
                chunk_index=chunk_index,
                total_chunks=len(segment_chunks),
                segments_json=json.dumps(chunk, ensure_ascii=False, indent=2),
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
    return "\n".join(lines), expected_requests, expected_indices_by_request


def upload_jsonl(gcs: storage.Client, content: str, gcs_path: str) -> str:
    blob = gcs.bucket(BUCKET_NAME).blob(gcs_path)
    blob.upload_from_string(content, content_type="application/jsonl")
    uri = f"gs://{BUCKET_NAME}/{gcs_path}"
    print(f"[GCS] Uploaded input to {uri}")
    return uri


def submit_and_wait(input_uri: str, output_prefix: str) -> aiplatform.BatchPredictionJob:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    job = aiplatform.BatchPredictionJob.create(
        job_display_name=f"lecture-cleaner-{ts}",
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


def parse_cleaned_segments(raw: str, video_id: str) -> list[dict] | None:
    cleaned, status = recover_cleaned_segments(raw)
    if cleaned is None:
        print(f"  [WARN] Could not parse cleaned response for {video_id}: {status}")
        return None
    valid = [item for item in cleaned if is_valid_cleaned_item(item)]
    if not valid:
        print(f"  [WARN] No valid cleaned items for {video_id}")
        return None
    if status != "already valid":
        print(f"  [WARN] {video_id}: parsed cleaned response with recovery ({status})")
    return valid


def main():
    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    gcs = storage.Client(project=PROJECT_ID)

    ensure_bucket(gcs)

    all_tagged = sorted(Path("data/tagged").glob("*.json"))
    if not all_tagged:
        print("[03b_clean] No tagged files found in data/tagged/")
        return

    cleaned_log = Path("data/cleaned.txt")
    already_cleaned = set()
    if cleaned_log.exists():
        already_cleaned = set(cleaned_log.read_text().splitlines())

    to_clean_paths = [p for p in all_tagged if p.stem not in already_cleaned]
    if not to_clean_paths:
        print("[03b_clean] All tagged transcripts already cleaned.")
        return

    to_clean = [
        (path, doc)
        for path, doc in iter_tagged_docs(to_clean_paths)
        if path.stem not in already_cleaned
    ]
    if not to_clean:
        print("[03b_clean] No valid tagged lecture files found to clean.")
        return

    print(f"[03b_clean] Cleaning {len(to_clean)}/{len(all_tagged)} transcripts "
          f"via Vertex AI Batch API ({MODEL_NAME})...")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    jsonl_content, expected_requests, expected_indices_by_request = build_input_jsonl(to_clean)
    if not jsonl_content.strip():
        print("[03b_clean] No valid segments to clean.")
        return

    input_uri = upload_jsonl(gcs, jsonl_content, f"batch_clean/{ts}/input.jsonl")
    output_prefix = f"gs://{BUCKET_NAME}/batch_clean/{ts}/output/"

    job = submit_and_wait(input_uri, output_prefix)

    if "FAILED" in job.state.name or "CANCELLED" in job.state.name:
        print(f"[ERROR] Batch job did not succeed: {job.state.name}")
        return

    results = fetch_results(gcs, output_prefix)

    docs_by_video = {path.stem: doc for path, doc in to_clean}
    paths_by_video = {path.stem: path for path, doc in to_clean}
    updated_counts: dict[str, int] = {video_id: 0 for video_id in docs_by_video}
    succeeded_requests: set[str] = set()
    seen_requests: set[str] = set()
    failed_videos: set[str] = set()
    cleaned_count = 0
    failed_count = 0

    for item in results:
        req_text = (item.get("request", {})
                        .get("contents", [{}])[0]
                        .get("parts", [{}])[0]
                        .get("text", ""))
        video_id = extract_video_id(req_text)
        request_id = extract_request_id(req_text)
        chunk_index, total_chunks = extract_chunk_metadata(req_text)

        if not video_id or not request_id:
            print("  [WARN] Skipping item: request metadata not found in request text")
            failed_count += 1
            continue

        seen_requests.add(request_id)

        if item.get("status"):
            print(f"  [WARN] {video_id}: item-level error in {request_id}: {item['status']}")
            failed_videos.add(video_id)
            failed_count += 1
            continue

        candidates = item.get("response", {}).get("candidates", [])
        if not candidates:
            print(f"  [WARN] {video_id}: no candidates in response for {request_id}")
            failed_videos.add(video_id)
            failed_count += 1
            continue

        finish_reason = candidates[0].get("finishReason", "")
        if finish_reason and finish_reason != "STOP":
            print(f"  [WARN] {video_id}: incomplete cleaned response in {request_id} ({finish_reason})")
            failed_videos.add(video_id)
            failed_count += 1
            continue

        raw_text = (candidates[0].get("content", {})
                                 .get("parts", [{}])[0]
                                 .get("text", ""))
        cleaned = parse_cleaned_segments(raw_text, video_id)
        if cleaned is None:
            failed_videos.add(video_id)
            failed_count += 1
            continue

        existing_doc = docs_by_video.get(video_id)
        if existing_doc is None:
            print(f"  [WARN] {video_id}: source tagged data not found, skipping")
            failed_videos.add(video_id)
            failed_count += 1
            continue

        idx_to_text = {
            entry["idx"]: entry["text"]
            for entry in cleaned
            if "idx" in entry and "text" in entry and str(entry["text"]).strip()
        }
        expected_indices = expected_indices_by_request.get(request_id, set())
        if expected_indices and set(idx_to_text) != expected_indices:
            missing_indices = sorted(expected_indices - set(idx_to_text))
            print(
                f"  [WARN] {video_id}: incomplete cleaned items in {request_id}; "
                f"missing idx {missing_indices[:5]}"
            )
            failed_videos.add(video_id)
            failed_count += 1
            continue

        updated = 0
        for i, seg in enumerate(existing_doc.get("segments", [])):
            if i in idx_to_text:
                seg["transcript"] = idx_to_text[i]
                updated += 1

        updated_counts[video_id] += updated
        succeeded_requests.add(request_id)
        chunk_label = ""
        if chunk_index is not None and total_chunks is not None:
            chunk_label = f" chunk {chunk_index}/{total_chunks}"
        print(f"  Cleaned {video_id}{chunk_label} ({updated} segments updated)")

    expected_request_ids = set().union(*expected_requests.values()) if expected_requests else set()
    missing_requests = expected_request_ids - seen_requests
    for request_id in sorted(missing_requests):
        video_id = request_id.split("__chunk_", 1)[0]
        print(f"  [WARN] {video_id}: missing batch result for {request_id}")
        failed_videos.add(video_id)
        failed_count += 1

    for video_id, request_ids in expected_requests.items():
        if video_id in failed_videos or not request_ids.issubset(succeeded_requests):
            continue

        tagged_path = paths_by_video[video_id]
        tagged_path.write_text(json.dumps(docs_by_video[video_id], ensure_ascii=False, indent=2))
        with open(cleaned_log, "a") as f:
            f.write(video_id + "\n")
        cleaned_count += 1
        print(f"  Saved {video_id} ({updated_counts[video_id]} segments updated across {len(request_ids)} chunk(s))")

    print(f"[03b_clean] Done. {cleaned_count} cleaned, {failed_count} failed.")


if __name__ == "__main__":
    main()
