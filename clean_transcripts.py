#!/usr/bin/env python3
"""
Generate clean timestamped transcripts from raw Whisper outputs.
Removes repetition loops, silence hallucinations, and music tags.
"""

import json
import re
import os

TRANSCRIPT_DIR = "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/voice_transcripts"

FILES = [
    ("memo_1_road_2", "Session 1: Sher-e-Bangla A K Fazlul Huq Road 2 (Oct 6, 4:20 PM - 1h 35m)"),
    ("memo_2_road_4", "Session 2: Sher-e-Bangla A K Fazlul Huq Road 4 (Oct 7, 4:23 PM - 1h 44m)"),
    ("memo_3_road_5", "Session 3: Sher-e-Bangla A K Fazlul Huq Road 5 (Oct 7, 6:45 PM - 27m 39s)"),
]

def format_ts(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    hrs = mins // 60
    mins = mins % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def clean_segment_text(text):
    text = text.strip()
    # Remove music tags
    text = re.sub(r'موسیقی', '', text).strip()
    # Remove repeated single letters like c.c.c. or d.d.d.
    text = re.sub(r'(?:[a-zA-Z]\.[\s]*){4,}', '', text).strip()
    # Remove repeated characters / single syllable repetition (like او او او او or امتنو امتنو)
    text = re.sub(r'(\b\S+\b)(?:\s+\1){3,}', r'\1', text).strip()
    text = re.sub(r'(.)\1{5,}', r'\1', text).strip()
    # Clean redundant punctuation/symbols
    text = re.sub(r'^[^\w\s\u0600-\u06FF]+$', '', text).strip()
    return text

def process_file(file_key, title):
    json_path = os.path.join(TRANSCRIPT_DIR, f"{file_key}.json")
    out_clean_path = os.path.join(TRANSCRIPT_DIR, f"{file_key}_clean.txt")

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    segments = data.get('segments', [])
    clean_lines = []

    for seg in segments:
        txt = clean_segment_text(seg.get('text', ''))
        # Filter out trivial fragments or pure junk
        if len(txt) < 3:
            continue
        # Check if words are valid
        words = txt.split()
        if len(words) == 1 and len(words[0]) < 3:
            continue
        ts_str = f"[{format_ts(seg['start'])} -> {format_ts(seg['end'])}]"
        clean_lines.append(f"{ts_str} {txt}")

    with open(out_clean_path, 'w', encoding='utf-8') as f:
        f.write(f"=== {title} ===\n\n")
        f.write("\n".join(clean_lines))
        f.write("\n")

    print(f"Wrote {len(clean_lines)} clean segments to {out_clean_path}")

def main():
    for fkey, title in FILES:
        process_file(fkey, title)

if __name__ == "__main__":
    main()
