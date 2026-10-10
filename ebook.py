# -*- coding: utf-8 -*-
"""
电子书解析：把 EPUB / MOBI(AZW3) / TXT 转成章节列表。

章节结构约定（对外统一）:
  chapters = [
      {"index": 1, "title": "序", "text": "..."},
      {"index": 2, "title": "第一章", "text": "..."},
      ...
  ]

EPUB: 按 spine 顺序取 xhtml，正文文件（跳过封面/版权/目录）。
MOBI/AZW3: 用 mobi 库解包出 html 流，再按正文标题切章（尽力而为）。
TXT: 按常见章节标题（第X章/序/楔子/第X卷）切分。
"""
import os
import re
import zipfile
import html as htmlmod

from text_clean import extract_text_from_html, clean_text, strip_watermark_lines


# ============ EPUB ============
_NOISE_KEYWORDS = ('contents', '目录', '封面', 'copyright', '版权', 'colophon',
                   'cover', '扉页', 'title page', '插图', '插圖', '明信片',
                   # 出版信息页（《银河帝国》每个分册都有一页 1090 字左右的 CIP 页）
                   '图书在版编目', 'cip', 'isbn', '责任编辑', '出版发行', '印刷',
                   # 出版社推广/二维码页
                   '扫二维码', '二维码', '公众号', '微信', '读客')


def _looks_like_toc(text):
    """
    判断一段文本是否像"目录页"：行数多、每行短（一行一个条目）。
    用来拦住标题含 contents/目录 但字数超过阈值、从而漏过噪声过滤的目录页
    （实例：《银河帝国1-15全集》的 part0000.html 标题 Contents、449 字、16 行，
     每行 ~28 字，正是目录条目列表）。
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 6:
        return False
    return (sum(len(l) for l in lines) / len(lines)) < 40


def _filter_chapters(chapters):
    """过滤封面/版权/目录等噪声章，合并空壳章，重编号。"""
    filtered = []
    for c in chapters:
        # ① 先用正文首行修正"文件名式"占位标题，再做噪声判定。
        #    顺序很关键：CIP/目录页的文件名往往是 part0065.html 这类，
        #    真正的标题（"图书在版编目（CIP）数据"）只存在于正文首行——
        #    若先判定后改名，这些页就永远识别不出来。
        if (_is_placeholder(c['title'])
                or re.match(r'^(part\d+|ch\d+|chapter\d+|title|x\d|page\d*)', c['title'], re.I)):
            first = [l.strip() for l in c['text'].split('\n') if 0 < len(l.strip()) < 40]
            if first:
                c['title'] = first[0]
        t = c['title'].strip().lower()
        # ② 纯符号标题（如 "*"、"——"）是排版分隔符，不是章节
        if not re.search(r'[0-9A-Za-z\u4e00-\u9fff]', c['title']):
            continue
        # ③ 封面/版权/CIP/目录/出版社推广页
        if any(k in t for k in _NOISE_KEYWORDS) and (
                len(c['text']) < 300 or _looks_like_toc(c['text'])):
            continue
        if len(c['text'].replace('\n', '')) < 50:
            if filtered:
                filtered[-1]['text'] += '\n' + c['text']
            continue
        filtered.append(c)
    if not filtered:
        return chapters
    for i, c in enumerate(filtered):
        c['index'] = i + 1
    return filtered


def _epub_open(path):
    """打开 EPUB：返回 (files, spine_paths, ncx_root)。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.endswith(('.xhtml', '.html', '.htm'))]
        files = {n: z.read(n).decode('utf-8', 'ignore') for n in names}
        container = z.read('META-INF/container.xml').decode('utf-8', 'ignore')
        m = re.search(r'full-path="([^"]+)"', container)
        opf_path = m.group(1) if m else None
        if not opf_path or opf_path not in z.namelist():
            raise ValueError('EPUB 缺少 valid OPF')
        opf = z.read(opf_path).decode('utf-8', 'ignore')
        base = os.path.dirname(opf_path).replace('\\', '/')

        manifest = {}
        for mm in re.finditer(r'<item\b[^>]*>', opf):
            tag = mm.group(0)
            idm = re.search(r'id="([^"]+)"', tag)
            href = re.search(r'href="([^"]+)"', tag)
            if idm and href:
                manifest[idm.group(1)] = (base + '/' + href.group(1)) if base else href.group(1)
        spine = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)
        spine_paths = []
        for sid in spine:
            p = manifest.get(sid)
            if p:
                spine_paths.append(p.replace('\\', '/'))

        # NCX 目录（用于识别合集分册）
        ncx_root = None
        ncx_items = re.findall(r'<item\b[^>]*media-type="application/x-dtbncx\+xml"[^>]*>', opf)
        ncx_path = None
        for it in ncx_items:
            href = re.search(r'href="([^"]+)"', it)
            if href:
                ncx_path = ((base + '/' + href.group(1)) if base else href.group(1)).replace('\\', '/')
                break
        if not ncx_path or ncx_path not in z.namelist():
            for n in z.namelist():
                if n.lower().endswith('.ncx'):
                    ncx_path = n
                    break
        if ncx_path and ncx_path in z.namelist():
            try:
                import xml.etree.ElementTree as ET
                ncx_root = ET.fromstring(z.read(ncx_path).decode('utf-8', 'ignore'))
            except Exception:
                ncx_root = None
    return files, spine_paths, ncx_root


def _match_zip_path(files, ref):
    """把 NCX/OPF 中的相对路径匹配到 zip 内实际路径。"""
    ref = ref.split('#')[0].replace('\\', '/')
    if ref in files:
        return ref
    for k in files:
        if k.endswith('/' + ref) or k == ref:
            return k
    return None


# 无信息量的占位标题：遇到就继续往下找（很多转换工具会给每个 xhtml 写同样的值）
_PLACEHOLDER_TITLES = {
    'unknown', 'untitled', 'title', 'document', 'no title', 'none', 'null',
    '无标题', '未知', '未命名', '没有标题', '正文', 'text', 'page',
}


def _is_placeholder(t):
    """判断标题是否为占位/无信息量值。"""
    t = re.sub(r'\s+', ' ', (t or '')).strip()
    if not t:
        return True
    low = t.lower()
    if low in _PLACEHOLDER_TITLES:
        return True
    # 形如 page1 / part0003 / text_12 / section-2 的纯编号文件名
    return bool(re.fullmatch(r'(page|part|text|section|chapter|ch)[-_ ]?\d{0,5}', low))


def _first_heading_text(xhtml, levels):
    """取第一个非空标题标签的纯文本。"""
    for lv in levels:
        for m in re.finditer(rf'<h{lv}\b[^>]*>(.*?)</h{lv}>', xhtml, re.S | re.I):
            t = htmlmod.unescape(re.sub(r'<[^>]+>', '', m.group(1)))
            t = re.sub(r'\s+', ' ', t).strip()
            if t:
                return t[:40]
    return None


# 章节正文里出现这些词，说明它是目录/版权类页面，不该当章节读
_TOC_WORDS = ('目录', 'contents', '目 录')


def _ncx_title_map(files, ncx_root):
    """
    NCX 全量 navPoint -> {zip内路径: 标题}。
    单本解析时用它兜底（有些书的 <title> 全是占位值，真标题只在 NCX 里）。
    """
    if ncx_root is None:
        return {}
    NS = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    out = {}

    def walk(node):
        for np in node.findall('n:navPoint', NS):
            lab = np.find('n:navLabel/n:text', NS)
            t = (lab.text or '').strip() if lab is not None else ''
            c = np.find('n:content', NS)
            src = c.get('src') if c is not None else None
            if src:
                zp = _match_zip_path(files, src)
                if zp and t and zp not in out:
                    out[zp] = t
            walk(np)

    navmap = ncx_root.find('n:navMap', NS)
    if navmap is not None:
        walk(navmap)
    return out


# 扁平 NCX 判定为合集时，每一册的正文量下限。
# 用来区分"15 个顶层项 = 15 本书"（每册十几万字）与
# "40 个顶层项 = 一本小说的 40 章"（每项几千字），避免把普通小说误切成 40 册。
MIN_VOLUME_CHARS = 20000


def _flat_ncx_volumes(files, spine_paths, top_points, nav_title, nav_src):
    """
    处理"扁平 NCX"：顶层导航点都不带子节点，只是各册的起始位置。
    （典型如《银河帝国1-15全集》：15 个顶层项，各指向该册扉页，正文量很小。）

    做法：把各顶层项的 src 映射到 spine 下标，按顺序把 spine 切段，每段即一册。
    仅当**每一段**正文量都 >= MIN_VOLUME_CHARS 时才认定为合集。
    """
    norm = [_match_zip_path(files, zp) or zp for zp in spine_paths]
    pos, titles = [], []
    for tp in top_points:
        src = nav_src(tp)
        zp = _match_zip_path(files, src) if src else None
        if not zp or zp not in norm:
            continue
        i = norm.index(zp)
        if pos and i <= pos[-1]:
            continue
        pos.append(i)
        titles.append(nav_title(tp))
    if len(pos) < 2:
        return []
    bounds = pos + [len(norm)]
    groups = []
    for k in range(len(pos)):
        seg = norm[bounds[k]:bounds[k + 1]]
        chars = sum(len(clean_text(extract_text_from_html(files[z])).replace('\n', ''))
                    for z in seg if z in files)
        if chars < MIN_VOLUME_CHARS:
            return []
        # 章节标题传 None，交由 _guess_title 从各文件的 h1~h6 / NCX 里取
        groups.append((titles[k], [(None, z) for z in seg]))
    return groups


def _parse_epub_books(path):
    """
    解析 EPUB 合集：把 NCX 顶层导航点视为分册。
    支持两种形态：
      A) 嵌套 NCX —— 顶层项带子节点（子节点为章）；或顶层单项正文量够大（独立短篇/番外）
      B) 扁平 NCX —— 顶层项都不带子节点，只是各册起始位置（见 _flat_ncx_volumes）
    返回 books 列表；若非合集返回 []（交由 _parse_epub 单本处理）。
    """
    files, spine_paths, ncx_root = _epub_open(path)
    if ncx_root is None:
        return []
    NS = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    navmap = ncx_root.find('n:navMap', NS)
    if navmap is None:
        return []

    def nav_title(node):
        lab = node.find('n:navLabel/n:text', NS)
        return (lab.text or '').strip() if lab is not None else ''

    def nav_src(node):
        c = node.find('n:content', NS)
        return c.get('src') if c is not None else None

    def leaves(node, part_prefix=''):
        """递归收集叶子章节，返回 [(title, src)]，中间层（部）作为标题前缀。"""
        kids = node.findall('n:navPoint', NS)
        if not kids:
            t = nav_title(node)
            if part_prefix and part_prefix not in t:
                t = f"{part_prefix}·{t}"
            return [(t, nav_src(node))]
        out = []
        for k in kids:
            kk = k.findall('n:navPoint', NS)
            prefix = nav_title(k) if kk else part_prefix
            out.extend(leaves(k, prefix))
        return out

    top_points = navmap.findall('n:navPoint', NS)

    def group_zps(pairs):
        """取一组章节指向的 zip 路径（去重、排序），用于判断"是不是同一份内容"。"""
        return tuple(sorted({z for z in (_match_zip_path(files, s) for _, s in pairs if s) if z}))

    def group_chars(zps):
        return sum(len(clean_text(extract_text_from_html(files[z])).replace('\n', ''))
                   for z in zps if z in files)

    # 方案 A：嵌套 NCX —— 顶层项带子节点；顶层单项正文量够大也算一册（独立短篇/番外）
    #
    # ⚠ 两道防线（都是被真实藏书打出来的）：
    #   ① 去重：多个顶层项指向**同一个文件**（只是锚点不同）时，那是同一册里的
    #      多个小节，不是多册。实测《Advances in Financial Machine Learning》
    #      244 个顶层项全指向同一个 118,945 字的文件 → 被当成 244 册，正文膨胀 47 倍。
    #   ② 分量：一册的正文量要达到 MIN_VOLUME_CHARS 才算"册"。
    #      实测《豆豆三部曲》44 个顶层项各 7.8k 字（其实是章节）→ 会被拆成 44 册。
    cand = []
    for tp in top_points:
        kids = tp.findall('n:navPoint', NS)
        if kids:
            cand.append((nav_title(tp), leaves(tp)))
        else:
            src = nav_src(tp)
            zp = _match_zip_path(files, src) if src else None
            if zp:
                txt = clean_text(extract_text_from_html(files[zp]))
                if len(txt.replace('\n', '')) >= MIN_VOLUME_CHARS:
                    cand.append((nav_title(tp), leaves(tp)))

    volume_groups, seen_keys = [], set()
    for title, pairs in cand:
        zps = group_zps(pairs)
        key = zps or (title,)
        if key in seen_keys:            # ① 同一份内容只算一册
            continue
        seen_keys.add(key)
        if group_chars(zps) < MIN_VOLUME_CHARS:   # ② 分量不足 = 章节，不是册
            continue
        volume_groups.append((title, pairs))

    # 方案 B：扁平 NCX —— 顶层项都不带子节点，只是各册起始位置
    # （如《银河帝国1-15全集》：15 个顶层项各指向该册扉页，正文量很小）
    if len(volume_groups) < 2:
        volume_groups = _flat_ncx_volumes(files, spine_paths, top_points,
                                          nav_title, nav_src)

    # 方案 C：安全阀 —— 分出来的册必须**覆盖全书绝大部分内容**。
    #
    # ⚠ 这条是被真实事故逼出来的：上面第 ② 条把"不够一册"的顶层项直接 skip 掉了，
    #   而它们的内容**没有任何别的地方会补回来** → 直接丢失。
    #   实测《刘慈欣短篇小说精选》11 个短篇里 5 个 <2 万字被 skip，
    #   正文从 216,393 字掉到 148,829 字，**凭空少了 6.7 万字**。
    #
    #   覆盖率不足 → 判定"这不是合集"→ 返回 [] 让调用方走 _parse_epub
    #   （那条路按 spine 遍历，一个文件都不会漏）。
    if len(volume_groups) >= 2:
        covered = sum(group_chars(group_zps(pairs)) for _, pairs in volume_groups)
        total = sum(len(clean_text(extract_text_from_html(files[zp])).replace('\n', ''))
                    for zp in spine_paths if zp in files)
        if total > 0 and covered < 0.8 * total:
            return []

    if len(volume_groups) < 2:
        return []

    books = []
    for title, ch_pairs in volume_groups:
        chapters = []
        seen = set()
        for c_title, c_src in ch_pairs:
            if not c_src:
                continue
            zp = _match_zip_path(files, c_src)
            if not zp or zp in seen:
                continue
            seen.add(zp)
            txt = clean_text(extract_text_from_html(files[zp]))
            if len(txt) < 20:
                continue
            chapters.append({"index": len(chapters) + 1,
                             "title": c_title or _guess_title(files[zp], os.path.basename(zp)),
                             "text": txt})
        chapters = _filter_chapters(chapters)
        if chapters:
            books.append({"title": title, "chapters": chapters})
    return books if len(books) >= 2 else []


def _parse_epub(path):
    files, spine_paths, ncx_root = _epub_open(path)
    ncx_map = _ncx_title_map(files, ncx_root)
    chapters = []
    seen = set()
    part = ''          # 最近的"篇/部"级标题，用于给子章节加前缀
    for zp in spine_paths:
        if zp in seen:
            continue
        seen.add(zp)
        if zp not in files:
            alt = [k for k in files if k.endswith('/' + zp) or k == zp]
            if not alt:
                continue
            zp = alt[0]
        xhtml = files[zp]
        txt = clean_text(extract_text_from_html(xhtml))
        if len(txt) < 20:
            continue
        # 结构性标题：h2/h3 当"篇"，h4~h6 当"章"。
        # 一个文件只有"篇"没有"章"时，它本身就是篇标题页。
        part_h = _first_heading_text(xhtml, (2, 3))
        chap_h = _first_heading_text(xhtml, (4, 5, 6))
        struct = None
        if part_h and not chap_h:
            if part_h.lower() not in _TOC_WORDS:
                part = part_h
            struct = part_h
        elif chap_h:
            struct = f'{part}·{chap_h}' if part and part not in chap_h else chap_h
        title = _guess_title(xhtml, os.path.basename(zp),
                             prefer=ncx_map.get(zp) or struct)
        chapters.append({"index": len(chapters) + 1,
                         "title": title or f"第{len(chapters)+1}章", "text": txt})
    return _filter_chapters(chapters)


def _guess_title(xhtml, fallback, prefer=None):
    """
    章节标题优先级: NCX 标题(prefer) > <title> > <h1>~<h4> > 文件名。

    注意 <title> 常见于转换工具生成的占位值（如整本书 436 个文件全写 "Unknown"），
    这类值必须先排除再往下找，否则会像以前那样直接返回 "Unknown" 并提前结束，
    永远看不到正文里真正的 h3/h4 标题。
    """
    if prefer and not _is_placeholder(prefer):
        return prefer[:40]
    m = re.search(r'<title[^>]*>(.*?)</title>', xhtml, re.S | re.I)
    if m:
        t = htmlmod.unescape(re.sub(r'<[^>]+>', '', m.group(1)))
        t = re.sub(r'\s+', ' ', t).strip()
        if not _is_placeholder(t):
            return t[:40]
    for lv in (1, 2, 3, 4, 5, 6):
        m = re.search(rf'<h{lv}\b[^>]*>(.*?)</h{lv}>', xhtml, re.S | re.I)
        if m:
            t = htmlmod.unescape(re.sub(r'<[^>]+>', '', m.group(1)))
            t = re.sub(r'\s+', ' ', t).strip()
            if not _is_placeholder(t):
                return t[:40]
    return os.path.splitext(os.path.basename(fallback))[0]


# ============ MOBI / AZW3 ============
def _palmdoc_decompress(data):
    """
    PalmDOC (LZ77) 解压。

    注意：MOBI 的正文是**跨记录连续压缩**的，必须把全部文本记录拼起来当**一整段流**解，
    逐记录独立解压会在遇到反向引用时越界（实测第一条第 2 字节就崩）。
    """
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if 1 <= c <= 8:                       # 原样复制 c 个字节
            out += data[i:i + c]
            i += c
        elif c < 0x80:                        # 单字节字面量
            out.append(c)
        elif c >= 0xC0:                       # 空格 + 字符
            out += b' ' + bytes([c ^ 0x80])
        else:                                 # 反向引用
            if i >= n:
                break
            c = (c << 8) | data[i]
            i += 1
            dist = (c >> 3) & 0x07FF
            ln = (c & 7) + 3
            if dist > len(out):               # 流已结束（后面是填充）
                break
            for _ in range(ln):
                out.append(out[-dist])
    return bytes(out)


def _mobi_palmdoc_html(path):
    """
    自己解析 MOBI7 (PalmDOC)，返回 HTML 字符串。
    失败抛异常，由调用方决定回退策略。
    """
    import struct
    with open(path, 'rb') as f:
        d = f.read()
    if len(d) < 100 or d[60:68] != b'BOOKMOBI':
        raise ValueError('不是 MOBI/PalmDB 文件')
    nrec = struct.unpack('>H', d[76:78])[0]
    if nrec < 2:
        raise ValueError('记录数为 0')
    offs = [struct.unpack('>I', d[78 + i * 8:82 + i * 8])[0] for i in range(nrec)]

    def rec(i):
        return d[offs[i]:(offs[i + 1] if i + 1 < nrec else len(d))]

    r0 = rec(0)
    compression, _, _textlen, textrecs, _recsize, enc, encryption = \
        struct.unpack('>HHIHHHH', r0[0:16])
    if encryption:
        raise ValueError('MOBI 已加密（DRM），无法解析')
    if textrecs <= 0 or textrecs + 1 > nrec:
        raise ValueError(f'文本记录数异常: {textrecs}')
    blob = b''.join(rec(i) for i in range(1, textrecs + 1))
    if compression == 1:
        raw = blob
    elif compression == 2:
        raw = _palmdoc_decompress(blob)
    else:
        raise ValueError(f'不支持的 MOBI 压缩方式 {compression}'
                         f'（17480=HUFF/CDIC 暂不支持）')

    # ⚠ 解出的流会跑过正文、混入 MOBI 头部/EXTH 元数据（实测出现
    #   "calibre (3.35.0) ... EBOK ... kindle:embed"），念出来会串味。
    #   仅当尾部**真的带这些元数据标记**时才截到最后一个闭合标签。
    #   （无条件截会误伤：有些文件解出来的尾部本来就是正常闭合标签。）
    last = None
    for m in re.finditer(rb'</[a-zA-Z][^>]{0,24}>', raw):
        last = m
    if last and last.end() < len(raw):
        tail = raw[last.end():]
        if re.search(rb'MOBI|EXTH|calibre|EBOK|kindle:|FLIS|FCIS|BOUNDARY', tail, re.I):
            raw = raw[:last.end()]

    codec = {65001: 'utf-8', 1252: 'cp1252', 65002: 'utf-16'}.get(enc, 'utf-8')
    html = raw.decode(codec, errors='replace')

    # 文首的「目录」区用的是 <a filepos=...> 内部链接（纯导航，不是正文）。
    # ⚠ 必须**确认那一段真的是目录**才丢：
    #   实测《背叛》的第一个 <mbp:pagebreak> 在全文末尾，按"第一分页前都是目录"
    #   无条件丢会把 100 万字正文全删掉，只剩 56 字节的尾巴。
    m = re.search(r'<mbp:pagebreak', html, re.I)
    if m and m.start() < 50_000 and re.search(
            r'目\s*录|Contents|Table of Contents', html[:m.start()], re.I):
        html = html[m.start():]
    return html


def _mobi_pagebreak_chapters(html):
    """MOBI 用 <mbp:pagebreak> 分页；有它就按它切章。"""
    parts = re.split(r'<mbp:pagebreak[^>]*/?>', html, flags=re.I)
    out = []
    for p in parts:
        t = clean_text(extract_text_from_html(p))
        if len(t.replace('\n', '')) < 50:
            continue
        first = [l.strip() for l in t.split('\n') if l.strip()]
        title = (first[0][:40] if first else f'第{len(out)+1}节')
        out.append({'index': len(out) + 1, 'title': title, 'text': t})
    return out


def _parse_mobi(path):
    """
    解析 MOBI/AZW3，返回 ('epub', 解出的epub路径) 或 ('text', 正文)。

    两条路：
      ① 先试 mobi 库：KF8/AZW3（含大多数现代 .mobi）会解出一个 **EPUB**，
         交给 EPUB 解析器 —— 直接复用 NCX 分册/标题/噪声过滤等全部逻辑。
      ② 库失败（实测某些 MOBI7 会崩）→ 用自带 PalmDOC 解析器兜底。
    """
    try:
        import mobi
        tmpdir, filepath = mobi.extract(path)
        if (isinstance(filepath, str) and filepath.lower().endswith('.epub')
                and os.path.isfile(filepath)):
            return ('epub', filepath)
    except Exception:
        pass
    html = _mobi_palmdoc_html(path)
    txt = clean_text(extract_text_from_html(html))
    # 先用文本模式切（实测更准：能认出「1、刹车时代」这类标题）
    chs = _split_txt_into_chapters(txt)
    if len(chs) >= 2:
        return ('chapters', chs)
    # 再退到 MOBI 自带的分页标记
    chs = _mobi_pagebreak_chapters(html)
    if len(chs) >= 2:
        return ('chapters', chs)
    return ('text', txt)


# ============ TXT ============
# 章节标题行：要求**整行就是标题**（短、不以句末标点结尾），避免误伤正文。
#   第X章 / 第X回 / 1、标题 / Chapter N / 序 / 楔子 / 尾声 …
_CHAPTER_PATTERN = re.compile(
    r'^[ \t]*(?:'
    r'第[一二三四五六七八九十百千万零〇0-9]{1,8}[章回卷节部集篇]'
    r'|[0-9]{1,3}[ \t]*[、.．][ \t]*[^\s。！？；]{1,20}'
    r'|[（(][0-9一二三四五六七八九十]{1,3}[）)][ \t]*[^\s。！？；]{1,20}'
    r'|Chapter[ \t]*[0-9IVXLC]{1,6}'
    r'|Part[ \t]*[0-9IVXLC]{1,6}'
    r'|(?:序章?|楔子|引子|番外|尾声|后记|前言|自序|译序|附录)'
    r'|PROLOGUE|EPILOGUE'
    r')[ \t]*[:：、.．]?[ \t]*[^\s。！？；]{0,24}[ \t]*$',
    re.IGNORECASE,
)


def _split_txt_into_chapters(txt):
    lines = txt.split('\n')
    chapters = []
    cur_title = '序'
    cur_lines = []
    for ln in lines:
        s = ln.strip()
        if s and _CHAPTER_PATTERN.match(s):
            if cur_lines:
                chapters.append({"title": cur_title, "text": '\n'.join(cur_lines)})
            cur_title = s[:40]
            cur_lines = []
        else:
            cur_lines.append(s)
    if cur_lines:
        chapters.append({"title": cur_title, "text": '\n'.join(cur_lines)})
    # 合并过弱章节：忽略 <20 字的
    out = []
    for i, c in enumerate(chapters):
        if len(c['text'].replace('\n', '')) < 20 and i < len(chapters) - 1:
            # 文首的碎片（书名页/目录残余）应**丢弃**，并入下一章会把
            # "目录/书名"粘到正文开头，念出来很出戏。
            if i == 0:
                continue
            chapters[i + 1]['text'] = c['text'] + '\n' + chapters[i + 1]['text']
            continue
        out.append(c)
    for i, c in enumerate(out):
        c['index'] = i + 1
    return out


def _parse_txt(path):
    """
    TXT：不猜章节，**整本当一章**读下去。

    理由：纯文本没有可靠的章节标记，硬猜只会把正文切错
    （把卷首语、正文段落误当标题）。用户明确要求"整本书一直读"。
    阅读器里表现为：1 章 / 全文字数，输出一个整本 mp3。
    """
    enc = None
    for e in ('utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'utf-16', 'big5'):
        try:
            with open(path, 'r', encoding=e) as f:
                txt = f.read()
            enc = e
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if enc is None:                      # 全失败 → 宽容解码，至少不丢内容
        txt = open(path, 'rb').read().decode('utf-8', errors='replace')
    txt = clean_text(txt)
    if not txt.strip():
        return []
    return [{"index": 1, "title": "全文", "text": txt}]


# ============ 入口 ============
_MOBI_EXT = ('.mobi', '.azw3', '.azw')


def parse_books(path):
    """
    解析书籍，返回 (collection_name, books)。
    books = [ {"title": "海伯利安", "chapters": [ {index,title,text}, ... ]}, ... ]

    - EPUB 合集（NCX 顶层存在多个带子节点的分册）→ 按分册拆分，每个分册一本书
    - 普通 EPUB / MOBI / TXT → 单本书（books 长度 1）
    """
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    base_name = os.path.splitext(os.path.basename(path))[0]
    if ext in ('.epub', '.original_epub'):
        books = _parse_epub_books(path)
        if not books:
            chapters = _parse_epub(path)
            books = [{"title": base_name, "chapters": chapters}]
    elif ext in _MOBI_EXT:
        kind, payload = _parse_mobi(path)
        if kind == 'epub':
            # KF8/AZW3 解出来就是 EPUB —— 走 EPUB 解析器，分册/标题/噪声过滤全都复用
            books = _parse_epub_books(payload)
            if not books:
                books = [{"title": base_name, "chapters": _parse_epub(payload)}]
        elif kind == 'chapters':
            books = [{"title": base_name, "chapters": payload}]
        else:
            # 兜底拿到的纯正文：按常见章节标题试着切，切不出来就是一章
            chs = _split_txt_into_chapters(payload)
            if len(chs) < 2:
                chs = [{"index": 1, "title": "全文", "text": payload}]
            books = [{"title": base_name, "chapters": chs}]
    elif ext == '.txt':
        chapters = _parse_txt(path)
        books = [{"title": base_name, "chapters": chapters}]
    else:
        raise ValueError(f"不支持格式: {ext}")
    books = [b for b in books if b.get("chapters")]
    if not books:
        raise ValueError('未解析出任何章节')
    # 最后一步：去掉推广/水印行（压制组、盗版站插进正文的）。
    # 放在这里而不是 clean_text 里，是为了不干扰 EPUB 的目录页/版权页判定。
    for b in books:
        for c in b['chapters']:
            c['text'] = strip_watermark_lines(c['text'])
        b['chapters'] = [c for c in b['chapters'] if c['text'].strip()]
        for i, c in enumerate(b['chapters']):
            c['index'] = i + 1
    books = [b for b in books if b.get("chapters")]
    if not books:
        raise ValueError('去掉水印后没有剩余正文')
    return base_name, books


def parse_book(path):
    """向后兼容：返回 (书名, chapters)。合集时合并所有分册章节。"""
    name, books = parse_books(path)
    if len(books) == 1:
        return books[0]["title"], books[0]["chapters"]
    merged = []
    for b in books:
        for c in b["chapters"]:
            merged.append({"index": len(merged) + 1,
                           "title": f"{b['title']}·{c['title']}", "text": c["text"]})
    return name, merged


if __name__ == '__main__':
    import sys
    name, books = parse_books(sys.argv[1])
    print(f"《{name}》 共 {len(books)} 册")
    for b in books:
        print(f"  [{b['title']}] {len(b['chapters'])} 章, "
              f"{sum(len(c['text']) for c in b['chapters'])} 字")
        for c in b['chapters'][:3]:
            print(f"      {c['index']}. {c['title'][:30]} ({len(c['text'])}字)")