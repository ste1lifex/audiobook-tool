# -*- coding: utf-8 -*-
"""
藏书批量挑战：逐个文件尝试解析，识别漫画跳过，记录全部失败原因。

用法:
  python challenge_library.py [根目录] [--limit N]

输出:
  tts_eval\\reports\\library_challenge.json   逐文件明细
  控制台: 分类汇总 + 失败清单
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402
import engine  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tts_eval', 'reports', 'library_challenge.json')

# 忽略的：元数据/图片/压缩包/PDF（用户明确排除 PDF）
SKIP_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.db', '.json', '.opf',
            '.mbs', '.mbp1', '.mbp', '.nfo', '.txt.bak', '.ds_store'}
# 需要单独说明的
SPECIAL_EXT = {'.rar', '.zip', '.7z', '.djvu'}
# 走 EPUB 解析的
EPUB_LIKE = {'.epub', '.original_epub'}

# 漫画判定阈值：图片多 + 文字少
IMG_COUNT_MIN = 12
IMG_BYTES_MIN = 3 * 1024 * 1024
TEXT_CHARS_MAX = 30_000


def zip_stats(path):
    """统计 zip/epub 里的图片数量与字节数。"""
    n_img, b_img = 0, 0
    try:
        with zipfile.ZipFile(path) as z:
            for it in z.infolist():
                low = it.filename.lower()
                if low.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
                    n_img += 1
                    b_img += it.file_size
    except Exception:
        pass
    return n_img, b_img


def quick_text_chars(path, max_files=25, max_bytes=6_000_000):
    """
    廉价文字量预估：只读前若干个 HTML 数去标签后的字符数。
    ⚠ 只靠"图片占体积比"判漫画会**严重误伤轻小说**——轻小说正文 + 十几张彩插，
    图片同样占体积 90%+，但它其实是文字书。必须同时看文字量。
    """
    n, read, nf = 0, 0, 0
    try:
        with zipfile.ZipFile(path) as z:
            for it in z.infolist():
                low = it.filename.lower()
                if not low.endswith(('.html', '.xhtml', '.htm')):
                    continue
                if nf >= max_files or read > max_bytes:
                    break
                data = z.read(it)[:400_000]
                read += len(data)
                nf += 1
                t = re.sub(r'<[^>]+>', ' ', data.decode('utf-8', 'ignore'))
                n += len(re.sub(r'\s+', '', t))
    except Exception:
        pass
    return n


def comic_reason(path, n_img, b_img, chars, size):
    """
    漫画判定。两个信号必须**同时**满足：
      ① 图片占据文件体积的大头（>50%）—— 排除纯文字书
      ② 文字量很少（<2 万字）—— 排除带彩插的轻小说
    """
    if size > 0 and n_img >= 8 and b_img / size >= 0.5 and chars < TEXT_CHARS_MAX:
        return f'图片型电子书（{n_img} 张图 {b_img/1048576:.0f}MB 占 {b_img/size:.0%}，仅 {chars:,} 字）'
    if size > 5 * 1024 * 1024 and chars < 1500:
        return f'疑似漫画/画集（{size/1048576:.0f}MB 仅 {chars} 字）'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default=r'input')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    files = []
    for r, _, fs in os.walk(args.root):
        for f in fs:
            files.append(os.path.join(r, f))
    files.sort(key=lambda p: os.path.getsize(p), reverse=True)
    if args.limit:
        files = files[:args.limit]
    print(f'共 {len(files)} 个文件\n', flush=True)

    rows = []
    t_all = time.time()
    for i, p in enumerate(files, 1):
        ext = os.path.splitext(p)[1].lower()
        name = os.path.relpath(p, args.root)
        size = os.path.getsize(p)
        row = dict(file=name, ext=ext, mb=round(size / 1048576, 2), status='', detail='')
        t0 = time.time()
        try:
            if ext in SKIP_EXT:
                row['status'] = 'skip'
                row['detail'] = '元数据/图片/已排除'
            elif ext in SPECIAL_EXT:
                row['status'] = 'unsupported'
                row['detail'] = f'{ext} 格式暂不支持'
            elif ext == '.cbz':
                n_img, b_img = zip_stats(p)
                row['status'] = 'comic'
                row['detail'] = f'CBZ 漫画（{n_img} 张图）'
            elif ext in EPUB_LIKE or ext in ('.mobi', '.azw3', '.azw'):
                is_zip = ext in EPUB_LIKE or ext in ('.azw3', '.azw')
                n_img, b_img = zip_stats(p) if is_zip else (0, 0)
                row['images'], row['img_mb'] = n_img, round(b_img / 1048576, 2)
                # ① 解析前估文字量 → 判漫画（避免白解析 120MB 的漫画卷）
                #    ⚠ 这里**只能用"图片占比 + 估字数"这一条**。
                #    另一条"体积大而字数少"依赖真实字数，而 est 在非 zip 容器
                #    （KF8 版 .azw3）上拿不到，会误杀（实测把 646 万字的
                #    《永恒的科幻经典》判成了漫画）。那条放到解析后复查。
                est = quick_text_chars(p) if is_zip else 0
                row['est_chars'] = est
                why = None
                if is_zip and est > 0 and n_img >= 8 and size > 0 \
                        and b_img / size >= 0.5 and est < TEXT_CHARS_MAX:
                    why = (f'图片型电子书（{n_img} 张图 {b_img/1048576:.0f}MB '
                           f'占 {b_img/size:.0%}，估 {est:,} 字）')
                if why:
                    row['status'] = 'comic'
                    row['detail'] = why
                else:
                    _n, books = ebook.parse_books(p)
                    nch = sum(len(b['chapters']) for b in books)
                    chars = sum(len(c['text']) for b in books for c in b['chapters'])
                    row.update(books=len(books), chapters=nch, chars=chars)
                    why = comic_reason(p, n_img, b_img, chars, size)     # ② 解析后复查
                    if why:
                        row['status'] = 'comic'
                        row['detail'] = why
                    elif chars < 2000:
                        row['status'] = 'suspicious'
                        row['detail'] = f'正文过少（{chars} 字）'
                    elif chars / max(1, size / 1024) > 5000:
                        row['status'] = 'suspicious'
                        row['detail'] = f'疑似内容重复（{chars:,} 字 / {size/1048576:.1f}MB）'
                    else:
                        row['status'] = 'ok'
                        row['detail'] = f'{len(books)}册 {nch}章 {chars:,}字'
                        row['segs'] = sum(len(engine.split_segments(c['text']))
                                          for b in books for c in b['chapters'])
            elif ext == '.txt':
                _n, books = ebook.parse_books(p)
                chars = sum(len(c['text']) for b in books for c in b['chapters'])
                row.update(books=1, chapters=1, chars=chars,
                           status='ok' if chars > 100 else 'suspicious',
                           detail=f'{chars:,}字')
            else:
                row['status'] = 'skip'
                row['detail'] = '未知类型'
        except Exception as e:                                   # noqa: BLE001
            if row['status'] not in ('comic',):
                row['status'] = 'fail'
                row['detail'] = f'{type(e).__name__}: {e}'[:200]
                row['trace'] = traceback.format_exc()[-600:]
        row['sec'] = round(time.time() - t0, 1)
        rows.append(row)
        if i % 10 == 0 or i == len(files):
            ok = sum(1 for r in rows if r['status'] == 'ok')
            print(f'  {i}/{len(files)}  已成功 {ok}  用时 {time.time()-t_all:.0f}s', flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    # ── 汇总 ──
    from collections import Counter
    cnt = Counter(r['status'] for r in rows)
    print(f'\n{"="*76}')
    print(f'总用时 {time.time()-t_all:.0f}s    结果已存 {OUT}\n')
    print('=== 分类汇总 ===')
    names = {'ok': '✅ 解析成功', 'comic': '🎨 漫画（跳过）',
             'fail': '❌ 解析失败', 'unsupported': '⬜ 格式不支持',
             'skip': '➖ 忽略', 'suspicious': '⚠️ 可疑'}
    for k in ('ok', 'comic', 'suspicious', 'fail', 'unsupported', 'skip'):
        if cnt.get(k):
            print(f'  {names[k]:<16} {cnt[k]:>4} 个')

    ok = [r for r in rows if r['status'] == 'ok']
    if ok:
        print(f'\n=== 成功解析的 {len(ok)} 个（按字数）===')
        for r in sorted(ok, key=lambda x: -x.get('chars', 0))[:15]:
            print(f'  {r["chars"]:>9,}字 {r.get("books",1):>2}册{r["chapters"]:>4}章 '
                  f'{r.get("segs",0):>5}段  {r["file"][:58]}')
        tot = sum(r.get('chars', 0) for r in ok)
        print(f'  --- 合计 {tot:,} 字 / {sum(r.get("segs",0) for r in ok):,} 段')

    for st, label in (('fail', '❌ 解析失败'), ('suspicious', '⚠️ 可疑'), ('unsupported', '⬜ 不支持')):
        sel = [r for r in rows if r['status'] == st]
        if sel:
            print(f'\n=== {label}（{len(sel)} 个）===')
            for r in sel:
                print(f'  [{r["ext"]:<14}] {r["mb"]:>7.2f}MB  {r["file"][:52]}')
                print(f'        {r["detail"]}')

    comic = [r for r in rows if r['status'] == 'comic']
    if comic:
        print(f'\n=== 🎨 识别为漫画并跳过（{len(comic)} 个）===')
        for r in comic:
            print(f'  {r["mb"]:>7.2f}MB  {r["file"][:56]}')
            print(f'        {r["detail"]}')


if __name__ == '__main__':
    main()
