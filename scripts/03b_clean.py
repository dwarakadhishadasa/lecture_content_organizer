"""
03b_clean.py — Improve grammar and clarity of tagged transcript segments using
Gemini 2.0 Flash via Vertex AI Batch API.

For each tagged file in data/tagged/:
  - Sends all segment transcripts as a JSON array in a single batch request
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
VIDEO_ID: {video_id}
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

Segments:
{segments_json}
"""


def ensure_bucket(gcs: storage.Client) -> None:
    try:
        gcs.get_bucket(BUCKET_NAME)
        print(f"[GCS] Using existing bucket: gs://{BUCKET_NAME}")
    except NotFound:
        gcs.create_bucket(BUCKET_NAME, location=LOCATION)
        print(f"[GCS] Created bucket: gs://{BUCKET_NAME}")


def build_input_jsonl(tagged_paths: list[Path]) -> str:
    lines = []
    for path in tagged_paths:
        video_id = path.stem
        with open(path) as f:
            t = json.load(f)

        segments_data = [
            {"idx": i, "text": seg.get("transcript", "").strip()}
            for i, seg in enumerate(t.get("segments", []))
            if seg.get("transcript", "").strip()
        ]
        if not segments_data:
            continue

        prompt = PROMPT_TEMPLATE.format(
            video_id=video_id,
            segments_json=json.dumps(segments_data, ensure_ascii=False, indent=2),
        )
        request = {
            "request": {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
            }
        }
        lines.append(json.dumps(request, ensure_ascii=False))
    return "\n".join(lines)


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


def extract_video_id(request_text: str) -> str | None:
    m = re.search(r"^VIDEO_ID: (\S+)", request_text)
    return m.group(1) if m else None


def parse_cleaned_segments(raw: str, video_id: str) -> list[dict] | None:
    raw = re.sub(r"```json|```", "", raw).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        print(f"  [WARN] No JSON array found for {video_id}")
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON decode error for {video_id}: {e}")
        return None
    if not isinstance(data, list):
        print(f"  [WARN] Expected list for {video_id}, got {type(data)}")
        return None
    return data


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

    to_clean = [p for p in all_tagged if p.stem not in already_cleaned]
    if not to_clean:
        print("[03b_clean] All tagged transcripts already cleaned.")
        return

    print(f"[03b_clean] Cleaning {len(to_clean)}/{len(all_tagged)} transcripts "
          f"via Vertex AI Batch API ({MODEL_NAME})...")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    jsonl_content = build_input_jsonl(to_clean)
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

    cleaned_count = 0
    failed_count = 0

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

        if item.get("status"):
            print(f"  [WARN] {video_id}: item-level error: {item['status']}")
            failed_count += 1
            continue

        candidates = item.get("response", {}).get("candidates", [])
        if not candidates:
            print(f"  [WARN] {video_id}: no candidates in response")
            failed_count += 1
            continue

        raw_text = (candidates[0].get("content", {})
                                 .get("parts", [{}])[0]
                                 .get("text", ""))
        cleaned = parse_cleaned_segments(raw_text, video_id)
        if cleaned is None:
            failed_count += 1
            continue

        tagged_path = Path(f"data/tagged/{video_id}.json")
        if not tagged_path.exists():
            print(f"  [WARN] {video_id}: tagged file not found, skipping")
            failed_count += 1
            continue

        with open(tagged_path) as f:
            t = json.load(f)

        idx_to_text = {
            entry["idx"]: entry["text"]
            for entry in cleaned
            if "idx" in entry and "text" in entry and str(entry["text"]).strip()
        }

        updated = 0
        for i, seg in enumerate(t.get("segments", [])):
            if i in idx_to_text:
                seg["transcript"] = idx_to_text[i]
                updated += 1

        tagged_path.write_text(json.dumps(t, ensure_ascii=False, indent=2))

        with open(cleaned_log, "a") as f:
            f.write(video_id + "\n")

        print(f"  Cleaned {video_id} ({updated} segments updated)")
        cleaned_count += 1

    print(f"[03b_clean] Done. {cleaned_count} cleaned, {failed_count} failed.")


if __name__ == "__main__":
    main()
