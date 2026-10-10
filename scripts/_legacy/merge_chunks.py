# -*- coding: utf-8 -*-
"""
合并 CosyVoice 合成的分块音频为完整一章，并转成 mp3。
用法: envs\cosyvoice\Scripts\python.exe scripts\merge_chunks.py [chunk_dir]
"""
import os, glob, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'output', 'book1_ch1')
tag = os.path.basename(CHUNK_DIR.rstrip('\\/'))
OUT_WAV = os.path.join(ROOT, 'output', f'{tag}_full.wav')
OUT_MP3 = os.path.join(ROOT, 'output', f'{tag}_full.mp3')
FFMPEG = r"bin\ffmpeg.exe"


def main():
    chunks = sorted(glob.glob(os.path.join(CHUNK_DIR, 'chunk_*.wav')))
    if not chunks:
        print('没有找到分块音频:', CHUNK_DIR)
        return
    print(f'共 {len(chunks)} 个分块')
    list_file = os.path.join(CHUNK_DIR, 'concat.txt')
    with open(list_file, 'w', encoding='utf-8') as f:
        for c in chunks:
            f.write(f"file '{c.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n")
    subprocess.run([FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
                    '-c', 'copy', OUT_WAV], check=True)
    print('已合并:', OUT_WAV)
    subprocess.run([FFMPEG, '-y', '-i', OUT_WAV, '-codec:a', 'libmp3lame', '-qscale:a', '2',
                    OUT_MP3], check=True)
    print('已转码:', OUT_MP3)


if __name__ == '__main__':
    main()
