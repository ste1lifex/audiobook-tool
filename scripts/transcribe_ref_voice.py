# -*- coding: utf-8 -*-
"""用 whisper 转写参考音色完整音频，输出带时间戳的片段清单。"""
import json, os, sys

SRC = r"reference_audio\ref_source_audio.m4a"
OUT_DIR = r"reference_audio"
JSON_PATH = os.path.join(OUT_DIR, "ref_source_audio.json")

if os.path.exists(JSON_PATH):
    d = json.load(open(JSON_PATH, encoding="utf-8"))
    print(f"共 {len(d['segments'])} 段")
    for seg in d["segments"]:
        t = seg["text"].strip()
        dur = seg["end"] - seg["start"]
        print(f"[{seg['start']:6.1f}-{seg['end']:6.1f}] ({dur:4.1f}s) {t}")
else:
    # 未转写过，先转写
    import whisper
    print("loading whisper small ...")
    model = whisper.load_model("small")
    result = model.transcribe(SRC, language="zh", word_timestamps=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"转写完成，共 {len(result['segments'])} 段")
    for seg in result["segments"]:
        t = seg["text"].strip()
        dur = seg["end"] - seg["start"]
        print(f"[{seg['start']:6.1f}-{seg['end']:6.1f}] ({dur:4.1f}s) {t}")
