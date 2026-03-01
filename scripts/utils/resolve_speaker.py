import re
from pathlib import Path

import yaml
from rapidfuzz import process, fuzz

# Project root = two levels up from this file (scripts/utils/resolve_speaker.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# YouTube channel handle → canonical speaker name
# Handles cases where the channel alias doesn't fuzzy-match the canonical name
_ALIASES = {
    "@madhupanditdasaofficial": "HG Madhu Pandit Dasa",
    "@chanchalapathidas": "HG Chanchalapathi Dasa",
}


def load_speakers(path: str | Path | None = None) -> list[str]:
    if path is None:
        path = _PROJECT_ROOT / "config" / "speakers.yaml"
    with open(path) as f:
        return yaml.safe_load(f)["speakers"]


def resolve_speaker(
    title: str,
    canonical: list[str],
    threshold: int = 85,
    log_path: str | Path | None = None,
) -> str | None:
    """
    Splits title on '|', fuzzy-matches each segment against canonical speaker list.
    Returns the canonical name if best score >= threshold, else None.
    Unresolved titles are appended to log_path for review.

    Examples:
      "Core of Spiritual Life | Sri Vasudev Keshava Dasa | SB 6.12.22 | 05.02.2026"
        → matches canonical "Sri Vasudev Keshava Dasa"
      "2015-11-13 | SB 3.18.1 | HG Amitasana Dasa"
        → matches canonical "Amitasana Dasa" (WRatio tolerates honorific prefix)
    """
    if log_path is None:
        log_path = _PROJECT_ROOT / "data" / "unresolved_speakers.txt"
    # Skip segments that are clearly topic titles/questions (contain "?") — not person names
    segments = [s.strip() for s in title.split("|") if "?" not in s]
    best_score, best_match = 0, None
    for segment in segments:
        # Skip historical Thakura composer names — they share tokens ("Dasa", "Srila")
        # with canonical modern speakers and cause false positives
        if re.search(r"thakur", segment, re.IGNORECASE):
            continue
        # Resolve YouTube handle aliases before fuzzy matching
        seg_lower = segment.lower()
        for alias, canonical_name in _ALIASES.items():
            if alias in seg_lower:
                return canonical_name
        result = process.extractOne(segment, canonical, scorer=fuzz.WRatio)
        if result and result[1] > best_score:
            best_score, best_match = result[1], result[0]
    if best_score >= threshold:
        return best_match
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(f"{title}\n")
    return None
