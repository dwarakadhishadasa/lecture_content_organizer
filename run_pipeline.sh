#!/bin/bash
# Run inside a screen session to survive SSH disconnects:
#   screen -S pipeline
#   bash run_pipeline.sh 2>&1 | tee pipeline.log
#   Ctrl+A D  →  detach (safe to close SSH)
#   screen -r pipeline  →  reattach next day

set -e

# Ensure 'scripts' package is importable as a top-level module
export PYTHONPATH="$(pwd)"

BATCH_SIZE=50  # lectures per download-transcribe cycle (~3-6 GB audio at a time)
BATCH_NUM=0

echo "=== Pipeline start (BATCH_SIZE=$BATCH_SIZE) ==="

# Phase 1: Download-transcribe loop (keeps disk bounded)
while true; do
    BATCH_NUM=$((BATCH_NUM + 1))
    echo "--- [Batch $BATCH_NUM] Downloading up to $BATCH_SIZE lectures ---"

    set +e
    python scripts/01_download.py --batch-size $BATCH_SIZE
    DOWNLOAD_CODE=$?
    set -e

    echo "--- [Batch $BATCH_NUM] Transcribing ---"
    set +e
    python scripts/02_transcribe.py
    TRANSCRIBE_CODE=$?
    set -e
    if [ $TRANSCRIBE_CODE -ne 0 ]; then
        echo "[WARN] 02_transcribe.py exited with code $TRANSCRIBE_CODE — check logs before continuing"
    fi

    # Checkpoint: commit archive + new transcripts to GitHub after each batch
    # Preserves GPU transcription work and download progress if pod dies mid-run
    echo "--- [Batch $BATCH_NUM] Checkpointing to GitHub ---"
    git add archive.txt 2>/dev/null || true
    git add data/transcripts/*.json data/unresolved_speakers.txt 2>/dev/null || true
    git diff --cached --quiet || git commit -m "checkpoint: batch $BATCH_NUM ($(wc -l < archive.txt) videos archived)"
    git push origin main

    if [ $DOWNLOAD_CODE -eq 0 ]; then
        echo "--- All channels fully downloaded after $BATCH_NUM batches ---"
        break
    fi
done

# Phase 2: Tag all transcripts
echo "=== [2/4] Tagging with Gemini ==="
python scripts/03_tag.py

# Phase 3: Upload to Notion
echo "=== [3/4] Uploading to Notion ==="
python scripts/04_upload_notion.py

# Phase 4: GitHub Final Commit
# audio/ is gitignored (ephemeral); all other data/ files are tracked
echo "=== [4/4] Final GitHub commit ==="
git add archive.txt 2>/dev/null || true
git add config/*.yaml
git add data/transcripts/*.json data/tagged/*.json 2>/dev/null || true
git add data/unresolved_speakers.txt data/uploaded.txt 2>/dev/null || true
git diff --cached --quiet || git commit -m "pipeline complete: $(date +'%Y-%m-%d %H:%M') — $(wc -l < archive.txt) videos processed"
git push origin main

echo "=== Data safely pushed to GitHub. Pipeline complete. ==="

echo "=== Pipeline complete ==="
