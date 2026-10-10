# -*- coding: utf-8 -*-
"""诊断 MOBI/AZW3 文件结构，判断能否自己解析（不依赖 mobi 库）。"""
import os
import struct
import sys

p = os.path.join(r'input', '流浪地球.mobi')
d = open(p, 'rb').read()
print(f'文件 {len(d):,} B')

# ---- PalmDB 头 ----
name = d[0:32].rstrip(b'\x00')
nrec = struct.unpack('>H', d[76:78])[0]
print(f"  PalmDB name = {name!r}")
print(f"  type/creator = {d[60:64]!r} / {d[64:68]!r}")
print(f"  记录数 nrec = {nrec}")

offs = []
for i in range(nrec):
    o = 78 + i * 8
    off, attr, uid = struct.unpack('>IBBH', d[o:o + 8]) if False else (0, 0, 0)
    off = struct.unpack('>I', d[o:o + 4])[0]
    offs.append(off)
print(f'  前 6 个记录偏移: {offs[:6]}')
print(f'  末 3 个记录偏移: {offs[-3:]}')
print(f'  记录偏移是否递增: {all(offs[i] <= offs[i+1] for i in range(len(offs)-1))}')
print(f'  最大偏移 {max(offs):,} vs 文件 {len(d):,}  -> {"OK" if max(offs) < len(d) else "越界!"}')
# 记录长度
lens = [(offs[i+1] if i + 1 < nrec else len(d)) - offs[i] for i in range(nrec)]
print(f'  记录长度: 最小 {min(lens)} 最大 {max(lens)} 合计 {sum(lens):,}')
print(f'  空记录数: {sum(1 for x in lens if x <= 0)}')

# ---- 记录 0 = MOBI 头 ----
r0 = d[offs[0]:offs[1] if nrec > 1 else len(d)]
print(f'\n  record0 长度 {len(r0)}')
if len(r0) >= 24:
    compression, _, textlen, textrecs, recsize, enc, _ = struct.unpack('>HHIHHHH', r0[0:16])
    print(f'   —— PalmDOC 头 ——')
    print(f'   compression  = {compression}  (1=无压缩 2=PalmDOC 17480=HUFF/CDIC)')
    print(f'   textLength   = {textlen:,}')
    print(f'   textRecords  = {textrecs}')
    print(f'   recordSize   = {recsize}')
    print(f'   encryption   = {enc}')
    if r0[16:20] == b'MOBI':
        hlen = struct.unpack('>I', r0[20:24])[0]
        print(f'   —— MOBI 头 ——  长度 {hlen}')
        mobi_type = struct.unpack('>I', r0[24:28])[0] if len(r0) >= 28 else None
        codepage = struct.unpack('>I', r0[28:32])[0]
        print(f'   mobiType = {mobi_type} (2=Mobipocket 4=PalmDoc 232=KF8?)')
        print(f'   codepage = {codepage} (65001=UTF-8 1252=CP1252)')
        ver = struct.unpack('>I', r0[36:40])[0]
        print(f'   fileVersion = {ver}')
        # EXTH
        exth_flags = struct.unpack('>I', r0[0x70:0x74])[0] if len(r0) >= 0x74 else 0
        if exth_flags & 0x40:
            exth_off = 0x80 + hlen
            if r0[exth_off:exth_off+4] == b'EXTH':
                exth_len = struct.unpack('>I', r0[exth_off+4:exth_off+8])[0]
                print(f'   EXTH 存在, 长度 {exth_len}')
                q = exth_off + 12
                end = exth_off + exth_len
                while q + 8 <= end:
                    t, l = struct.unpack('>II', r0[q:q+8])
                    val = r0[q+8:q+l]
                    if t in (100,):
                        print(f'     EXTH 作者: {val.decode("utf-8","ignore")}')
                    q += l

# ---- 第一条文本记录能否 PalmDOC 解压 ----
print('\n  —— 试解第一条文本记录 ——')
tri = textrecs + 1 if len(r0) >= 16 else 1
print(f'   textRecords={textrecs}, 所以文本记录从 index {tri} 开始')
if tri < nrec:
    raw = d[offs[tri]:(offs[tri+1] if tri+1 < nrec else len(d))]
    print(f'   第 {tri} 条记录长度 {len(raw)}  前 24 字节 {raw[:24]!r}')


def palmdoc(data):
    """PalmDOC LZ77 解压"""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        c = data[i]; i += 1
        if 1 <= c <= 8:
            out += data[i:i+c]; i += c
        elif c < 0x80:
            out.append(c)
        elif c >= 0xC0:
            out += b' ' + bytes([c ^ 0x80])
        else:
            if i >= n: break
            c = (c << 8) | data[i]; i += 1
            dist = (c >> 3) & 0x07FF
            ln = (c & 7) + 3
            for _ in range(ln):
                out.append(out[-dist])
    return bytes(out)


try:
    dec = palmdoc(raw)
    print(f'   PalmDOC 解压后 {len(dec):,} B')
    print(f'   前 300 字节: {dec[:300]!r}')
except Exception as e:
    print(f'   解压失败: {e}')
