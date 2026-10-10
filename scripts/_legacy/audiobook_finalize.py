# -*- coding: utf-8 -*-
"""
明早交付脚本: 扫描 audiobook 目录所有 MP3/WAV，汇总时长，合并整本文件，生成播放清单。
用法: envs\cosyvoice\Scripts\python.exe scripts\audiobook_finalize.py
"""
import os, glob, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT, 'output', 'audiobook')
FFMPEG = r"bin\ffmpeg.exe"
FFPROBE = r"bin\ffprobe.exe"


def dur_of(path):
    try:
        r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def main():
    mp3s = sorted(glob.glob(os.path.join(AUDIO_DIR, '*.mp3')))
    wavs = sorted(glob.glob(os.path.join(AUDIO_DIR, '*.wav')))
    if not mp3s:
        print('没有找到 mp3 文件:', AUDIO_DIR)
        return

    rows = []
    total = 0.0
    for m in mp3s:
        d = dur_of(m)
        total += d
        rows.append((os.path.basename(m), d))

    print('=== 各章 MP3 ===')
    for name, d in rows:
        print(f'{name}: {int(d // 60)}分{d % 60:04.1f}秒')
    print(f'总时长: {int(total // 3600)}小时{int((total % 3600) // 60)}分{total % 60:.0f}秒')

    # 合并整本为一个 mp3（方便开车单曲循环/一条龙播放）
    full_mp3 = os.path.join(AUDIO_DIR, '海伯利安_整本合并.mp3')
    list_file = os.path.join(AUDIO_DIR, '_concat_list.txt')
    with open(list_file, 'w', encoding='utf-8') as f:
        for m in mp3s:
            safe = m.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    r = subprocess.run([FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
                        '-c', 'copy', full_mp3], capture_output=True, text=True)
    if os.path.exists(full_mp3):
        d = dur_of(full_mp3)
        print(f'\n整本合并: {full_mp3} ({int(d // 60)}分{d % 60:.0f}秒)')
    else:
        print('\n整本合并失败:', r.stderr[-300:] if r.stderr else 'unknown')

    # 播放清单
    m3u = os.path.join(AUDIO_DIR, '播放清单.m3u')
    with open(m3u, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for name, d in rows:
            f.write(f'#EXTINF:{int(d)}, {name}\n')
            f.write(os.path.join(AUDIO_DIR, name) + '\n')
    print('播放清单:', m3u)


if __name__ == '__main__':
    main()