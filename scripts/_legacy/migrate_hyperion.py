# -*- coding: utf-8 -*-
"""迁移海伯利安旧成品 → 新工具书库（带断点数据）。

旧结构:  output\audiobook\book1_part0005~0012 (序 + 第一~七章)
          output\audiobook\_chunks\book1_part000X.txt__segNNNNN.wav
新结构:  output\海伯利安\
          ├─ 海伯利安_ch01_序.mp3 ...  (已完成章的 mp3)
          ├─ _chunks\海伯利安_chNN_segNNNNN.wav  (断点缓存, 复用旧 chunk)
          ├─ task_state.json   (进度状态)
          └─ _chapters.json    (章节快照, 支持书库直接继续)

注意: 通过对比"新 split_segments 段数"与"旧 chunk 数量"来保证切分一致。
"""
import os
import re
import shutil
import sys

WORKING = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKING)

import engine
import config

# 旧成品目录 & 旧 chunk 目录
OLD_AUDIO = r"output\audiobook"
OLD_CHUNKS = os.path.join(OLD_AUDIO, "_chunks")
# 已清洗的简体章节文本
OLD_TEXTS = r"epub_extract\simplified"

BOOK = "海伯利安"
# part0005~0012 的顺序标题
PART_TITLES = {
    "book1_part0005.txt": "序",
    "book1_part0006.txt": "第一章",
    "book1_part0007.txt": "第二章",
    "book1_part0008.txt": "第三章",
    "book1_part0009.txt": "第四章",
    "book1_part0010.txt": "第五章",
    "book1_part0011.txt": "第六章",
    "book1_part0012.txt": "第七章",
}
ALL_PARTS = sorted(PART_TITLES, key=lambda x: int(re.search(r"part(\d+)", x).group(1)))


def seg_count_of(fname):
    """统计旧 chunk 数。"""
    return len([f for f in os.listdir(OLD_CHUNKS) if f.startswith(fname + "__seg")])


def main():
    target = os.path.join(config.OUTPUT_BASE, engine.safe_name(BOOK))
    os.makedirs(os.path.join(target, "_chunks"), exist_ok=True)

    # 1) 构造 chapters（读已清洗文本）
    chapters = []
    for i, part in enumerate(ALL_PARTS, 1):
        src = os.path.join(OLD_TEXTS, part)
        if not os.path.exists(src):
            # 源哈希文本缺失则跳过
            print(f"!! 缺文本 {src}，跳过")
            continue
        with open(src, encoding="utf-8") as f:
            text = f.read()
        chapters.append({"index": i, "title": PART_TITLES[part], "text": text})

    done_chapters = []   # 完成的章节 index（有序 + 已有 mp3 的）
    for i, ch in enumerate(chapters, 1):
        part = ALL_PARTS[i - 1]
        # 判断该章旧成品/旧 chunk 是否齐全
        old_mp3 = os.path.join(OLD_AUDIO, part + ".mp3")
        old_cnt = seg_count_of(part)
        new_cnt = len(engine.split_segments(ch["text"]))
        print(f"ch{i:02d} {ch['title']}: 旧chunk={old_cnt} 新分段={new_cnt} mp3={'有' if os.path.exists(old_mp3) else '无'}")
        if os.path.exists(old_mp3) and old_cnt == new_cnt:
            done_chapters.append(i)
        elif old_cnt != new_cnt and old_cnt > 0:
            print(f"   !! 切分不一致（旧{old_cnt} vs 新{new_cnt}），该章需重新生成（chunk 将不迁移）")

    # 2) 拷贝完成的 mp3（新命名）
    for i in done_chapters:
        part = ALL_PARTS[i - 1]
        ch = chapters[i - 1]
        src = os.path.join(OLD_AUDIO, part + ".mp3")
        dst = os.path.join(target, f"{engine.safe_name(BOOK)}_ch{i:02d}_{engine.safe_name(ch['title'])}.mp3")
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f">> mp3 迁移: {os.path.basename(dst)}")

    # 3) 迁移旧 chunk（仅切分一致的章），新命名 海伯利安_chNN_segNNNNN.wav
    for i, ch in enumerate(chapters, 1):
        part = ALL_PARTS[i - 1]
        if i not in done_chapters:
            continue
        prefix = f"{engine.safe_name(BOOK)}_ch{i:02d}"
        for f in sorted(os.listdir(OLD_CHUNKS)):
            m = re.match(rf'^{re.escape(part)}__seg(\d+)\.wav$', f)
            if not m:
                continue
            segno = int(m.group(1))
            dst = os.path.join(target, "_chunks", f"{prefix}_seg{segno:05d}.wav")
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(OLD_CHUNKS, f), dst)
        print(f">> chunk 迁移: {prefix}")

    # 4) 构造并写入状态 + 章节快照
    engine.save_chapters(target, chapters)
    st = engine.new_state(BOOK, chapters)
    for i, ch in enumerate(chapters, 1):
        st["chapters"][i - 1]["done"] = i in done_chapters
        if i in done_chapters:
            st["chapters"][i - 1]["mp3"] = (f"{engine.safe_name(BOOK)}_ch{i:02d}_"
                                            f"{engine.safe_name(ch['title'])}.mp3")
    st["status"] = "paused"  # 迁移后待续
    engine.save_state(target, st)
    print(f"\n== 迁移完成: {target}")
    print(f"  已完成 {len(done_chapters)} 章: {[chapters[i-1]['title'] for i in done_chapters]}")
    print(f"  待续: {[chapters[i-1]['title'] for i in range(1, len(chapters)+1) if i not in done_chapters]}")
    print("  书库中显示为「已暂停」，可直接点继续" if done_chapters else "  书库中显示为「未开始」")


if __name__ == "__main__":
    main()