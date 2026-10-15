#!/usr/bin/env python3
"""
Transcribe DFLT Phase 3 meeting voice memos using mlx-whisper (Apple Silicon Metal).
Saves detailed timestamped JSON and text transcripts.
"""

import os
import sys
import json
import time
import mlx_whisper

RECORDINGS = [
    {
        "id": "memo_1_road_2",
        "title": "Sher-e-Bangla A K Fazlul Huq Road 2 (Oct 6, 4:20 PM - 1h 35m)",
        "file": "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/voice_memos_input/20261006 162002.m4a"
    },
    {
        "id": "memo_2_road_4",
        "title": "Sher-e-Bangla A K Fazlul Huq Road 4 (Oct 7, 4:23 PM - 1h 44m)",
        "file": "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/voice_memos_input/20261007 162344.m4a"
    },
    {
        "id": "memo_3_road_5",
        "title": "Sher-e-Bangla A K Fazlul Huq Road 5 (Oct 7, 6:45 PM - 27m 39s)",
        "file": "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/voice_memos_input/20261007 184525.m4a"
    }
]

OUT_DIR = "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/voice_transcripts"
MODEL_NAME = "mlx-community/whisper-large-v3-turbo"
PROMPT = (
    "DFLT Phase 3, project methodology, tranches, 9 million, beneficiaries, "
    "training design, rapid design assessment, actionable groups, curriculum, "
    "monitoring, deliverables, milestones, timeline, budget, tasks, evaluation"
)

def format_ts(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    hrs = mins // 60
    mins = mins % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def process_file(rec):
    rec_id = rec["id"]
    title = rec["title"]
    audio_path = rec["file"]

    json_path = os.path.join(OUT_DIR, f"{rec_id}.json")
    txt_path = os.path.join(OUT_DIR, f"{rec_id}.txt")

    if os.path.exists(json_path) and os.path.getsize(json_path) > 1000:
        print(f"[SKIP] {rec_id} already exists at {json_path}")
        return

    print(f"\n=======================================================")
    print(f"STARTING TRANSCRIPTION: {title}")
    print(f"Audio: {audio_path}")
    print(f"=======================================================")

    t0 = time.time()
    try:
        res = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=MODEL_NAME,
            language="ur",
            verbose=False,
            initial_prompt=PROMPT,
            condition_on_previous_text=False
        )
        elapsed = time.time() - t0
        print(f"[DONE] Transcribed {rec_id} in {elapsed:.1f}s ({elapsed/60:.2f} min)")

        # Save JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

        # Save readable timestamped text
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Meeting Recording: {title}\n")
            f.write(f"Source: {audio_path}\n")
            f.write(f"Elapsed Time: {elapsed/60:.2f} minutes\n")
            f.write("=" * 60 + "\n\n")

            segments = res.get("segments", [])
            for seg in segments:
                st = format_ts(seg["start"])
                et = format_ts(seg["end"])
                text = seg["text"].strip()
                if text:
                    f.write(f"[{st} -> {et}] {text}\n")

        print(f"[SAVED] {json_path} and {txt_path}")
    except Exception as e:
        print(f"[ERROR] Failed transcribing {rec_id}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # If specific arg provided, process only that
    target_id = sys.argv[1] if len(sys.argv) > 1 else None

    for rec in RECORDINGS:
        if target_id and rec["id"] != target_id:
            continue
        process_file(rec)

if __name__ == "__main__":
    main()
