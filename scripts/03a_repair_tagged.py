"""
03a_repair_tagged.py — Recover malformed batch-tagging responses into per-video
tagged lecture files.

Reads data/tagged/tagged.json, attempts to repair near-valid JSON model output,
and writes recovered lectures to data/tagged/{video_id}.json using metadata from
data/transcripts/{video_id}.json.

Also writes:
  - data/malformed_tagged_repair_report.tsv
  - data/unrepaired_tagged_ids.txt
"""
import json
import re
from pathlib import Path


TAGGED_BATCH_PATH = Path("data/tagged/tagged.json")
TRANSCRIPTS_DIR = Path("data/transcripts")
TAGGED_DIR = Path("data/tagged")
REPAIR_REPORT_PATH = Path("data/malformed_tagged_repair_report.tsv")
UNREPAIRED_IDS_PATH = Path("data/unrepaired_tagged_ids.txt")


def extract_video_id(request_text: str) -> str | None:
    match = re.search(r"^VIDEO_ID: (\S+)", request_text)
    return match.group(1) if match else None


def strip_json_fences(raw: str) -> str:
    return re.sub(r"```json|```", "", raw).strip()


def parse_segments(raw: str) -> list[dict] | None:
    data = json.loads(raw)
    segments = data.get("segments")
    return segments if isinstance(segments, list) else None


def is_valid_segment(segment: dict) -> bool:
    required = {
        "start_time",
        "end_time",
        "verse_references",
        "themes",
        "content_type",
        "circle_fit",
        "key_quote",
        "summary",
        "transcript",
    }
    return isinstance(segment, dict) and required.issubset(segment.keys())


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

        candidate = cleaned[obj_start:obj_end + 1]
        candidate = sanitize_json_text(candidate)
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


def write_tagged_doc(video_id: str, segments: list[dict]) -> None:
    transcript_path = TRANSCRIPTS_DIR / f"{video_id}.json"
    with open(transcript_path) as f:
        transcript = json.load(f)

    for seg in segments:
        if "start_time" in seg:
            seg["timestamp_url"] = f"https://youtu.be/{video_id}?t={int(seg['start_time'])}"

    tagged_doc = {
        "video_id": video_id,
        "title": transcript["title"],
        "speaker": transcript["speaker"],
        "youtube_url": transcript["youtube_url"],
        "segments": segments,
    }
    (TAGGED_DIR / f"{video_id}.json").write_text(
        json.dumps(tagged_doc, ensure_ascii=False, indent=2)
    )


def main() -> None:
    if not TAGGED_BATCH_PATH.exists():
        raise SystemExit(f"Missing {TAGGED_BATCH_PATH}")

    TAGGED_DIR.mkdir(parents=True, exist_ok=True)

    with open(TAGGED_BATCH_PATH) as f:
        items = json.load(f)

    repaired_count = 0
    valid_count = 0
    failed_count = 0
    report_lines = ["video_id\tstatus\tdetail"]
    unrepaired_ids: list[str] = []

    for item in items:
        request_text = (item.get("request", {})
                            .get("contents", [{}])[0]
                            .get("parts", [{}])[0]
                            .get("text", ""))
        video_id = extract_video_id(request_text)
        if not video_id:
            failed_count += 1
            report_lines.append("<missing-video-id>\tfailed\tmissing VIDEO_ID in request")
            continue

        if item.get("status"):
            failed_count += 1
            unrepaired_ids.append(video_id)
            report_lines.append(f"{video_id}\tfailed\titem status: {item['status']}")
            continue

        transcript_path = TRANSCRIPTS_DIR / f"{video_id}.json"
        if not transcript_path.exists():
            failed_count += 1
            unrepaired_ids.append(video_id)
            report_lines.append(f"{video_id}\tfailed\tmissing transcript source")
            continue

        candidates = item.get("response", {}).get("candidates", [])
        if not candidates:
            failed_count += 1
            unrepaired_ids.append(video_id)
            report_lines.append(f"{video_id}\tfailed\tno candidates in response")
            continue

        raw_text = (candidates[0].get("content", {})
                                 .get("parts", [{}])[0]
                                 .get("text", ""))
        segments, status = recover_segments(raw_text)
        if segments is None:
            failed_count += 1
            unrepaired_ids.append(video_id)
            report_lines.append(f"{video_id}\tfailed\t{status}")
            continue

        write_tagged_doc(video_id, segments)
        if status == "repaired":
            repaired_count += 1
            report_lines.append(f"{video_id}\trepaired\tstring/control-character cleanup")
        elif status.startswith("salvaged"):
            repaired_count += 1
            report_lines.append(f"{video_id}\trepaired\t{status}")
        else:
            valid_count += 1

    REPAIR_REPORT_PATH.write_text("\n".join(report_lines) + "\n")
    UNREPAIRED_IDS_PATH.write_text("\n".join(unrepaired_ids) + ("\n" if unrepaired_ids else ""))

    print(f"Wrote {valid_count + repaired_count} tagged lecture files")
    print(f"  Already valid: {valid_count}")
    print(f"  Repaired: {repaired_count}")
    print(f"  Still unrepaired: {failed_count}")
    print(f"Repair report: {REPAIR_REPORT_PATH}")
    print(f"Unrepaired IDs: {UNREPAIRED_IDS_PATH}")


if __name__ == "__main__":
    main()
