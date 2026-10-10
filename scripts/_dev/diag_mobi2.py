# -*- coding: utf-8 -*-
"""诊断 PalmDOC 解压失败的原因：extraDataFlags / 逐记录 vs 整段流。"""
import os
import struct

p = os.path.join(r'input', '流浪地球.mobi')
d = open(p, 'rb').read()
nrec = struct.unpack('>H', d[76:78])[0]
offs = [struct.unpack('>I', d[78 + i * 8:82 + i * 8])[0] for i in range(nrec)]
rec = lambda i: d[offs[i]:(offs[i + 1] if i + 1 < nrec else len(d))]

r0 = rec(0)
compression, _, textlen, textrecs, recsize, enc, _ = struct.unpack('>HHIHHHH', r0[0:16])
print(f'compression={compression} textLength={textlen:,} textRecords={textrecs} recordSize={recsize}')

# MOBI 头字段（相对 'MOBI' 起点 = 绝对 16）
M = 16
hlen = struct.unpack('>I', r0[M + 4:M + 8])[0]
print(f'MOBI header length = {hlen}')
extra_flags = struct.unpack('>H', r0[M + 0xC0:M + 0xC2])[0] if len(r0) >= M + 0xC2 else 0
print(f'extraDataFlags = 0x{extra_flags:04x}  (bit0=多字节尾  bit1=索引  bit2=索引  ...)')
print(f'  bit1(0x02)=索引  bit2(0x04)=索引  bit3..=各类尾条目')


def trim_tail(data, flags):
    """按 extraDataFlags 裁掉记录尾部的额外数据（参考 kindleunpack 做法）。"""
    if flags == 0 or not data:
        return data
    num = (data[-1] & 3) + 1
    if flags & 1:
        # 多字节尾
        n = num
        if flags & 2:
            pass
        data = data[:-n]
    # 其余条目按需裁（这里只处理最常见的情况）
    return data


def palmdoc_stream(data):
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]; i += 1
        if 1 <= c <= 8:
            out += data[i:i + c]; i += c
        elif c < 0x80:
            out.append(c)
        elif c >= 0xC0:
            out += b' ' + bytes([c ^ 0x80])
        else:
            if i >= n: break
            c = (c << 8) | data[i]; i += 1
            dist = (c >> 3) & 0x07FF
            ln = (c & 7) + 3
            if dist > len(out):
                raise IndexError(f'dist={dist} > out={len(out)} at byte {i}')
            for _ in range(ln):
                out.append(out[-dist])
    return bytes(out)


print('\n=== 方案 A：逐记录独立解压 ===')
try:
    out = b''
    for i in range(1, textrecs + 1):
        r = trim_tail(rec(i), extra_flags)
        out += palmdoc_stream(r)
    print(f'  OK {len(out):,} B')
except Exception as e:
    print(f'  ❌ {type(e).__name__}: {e}')

print('\n=== 方案 B：拼成一段流后整体解压 ===')
try:
    buf = b''.join(trim_tail(rec(i), extra_flags) for i in range(1, textrecs + 1))
    out2 = palmdoc_stream(buf)
    print(f'  OK {len(out2):,} B   与 textLength 匹配: {len(out2)==textlen}')
    print(f'  前 200 字节: {out2[:200]!r}')
except Exception as e:
    print(f'  ❌ {type(e).__name__}: {e}')

print('\n=== 方案 C：不裁尾，整段流 ===')
try:
    buf = b''.join(rec(i) for i in range(1, textrecs + 1))
    out3 = palmdoc_stream(buf)
    print(f'  OK {len(out3):,} B')
    print(f'  前 200 字节: {out3[:200]!r}')
except Exception as e:
    print(f'  ❌ {type(e).__name__}: {e}')

print('\n=== 各文本记录首字节（看是否带长度前缀）===')
for i in range(1, min(5, textrecs + 1)):
    r = rec(i)
    print(f'  记录{i}: len={len(r):>5}  前 8 字节={r[:8].hex()}  末 3 字节={r[-3:].hex()}')
