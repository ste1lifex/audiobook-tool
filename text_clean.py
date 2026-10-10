# -*- coding: utf-8 -*-
"""
文本清洗：繁体转简体 + 清理脚注标记/空白，返回分章文本。
依赖 opencc（已在 qwen3tts venv 安装）。
"""
import re

from opencc import OpenCC

_cc = OpenCC('t2s')


def clean_text(text: str) -> str:
    """繁体→简体 + 著→着 + 清脚注 + 压空白。

    注意：**不做去水印**。EPUB 的噪声判定（目录页/版权页）依赖清洗后文本的
    行数与行长统计，在这里删行会改变那些判定结果（实测会让 6 个目录页漏过过滤）。
    去水印请用 strip_watermark_lines()，在解析链的最后一步做。
    """
    s = _cc.convert(text)
    s = s.replace("著", "着")  # 台湾用字修正
    s = re.sub(r'^\s*\[\d+\]\s*$', '', s, flags=re.M)  # 整行脚注
    s = re.sub(r'\[\d+\]', '', s)                        # 行内脚注引用
    s = re.sub(r'[ \t\u00a0]+', ' ', s)
    lines = [l.strip() for l in s.split('\n') if l.strip()]
    return '\n'.join(lines)


# ── 电子书里常见的推广/水印文字 ──────────────────────────────
# 这些是压制组/盗版站插进正文的，念出来非常出戏。
# 模式要**足够具体**，避免误伤正常行文（比如小说里出现"微信"两个字不该被删）。
_WATERMARK_PATTERNS = [
    re.compile(p) for p in (
        r'本书由.{0,24}整理',
        r'本电子书由.{0,24}制作',
        r'加小编微信',
        r'小编\s*Q{2}',
        r'Q{2}\s*[:：]\s*\d{5,}',
        r'微信\s*[:：]\s*\S{3,}',
        r'公众号\s*[:：]?\s*\S{2,}',
        r'关注公众号',
        r'扫二维码.{0,12}关注',
        r'更多免费电子书',
        r'免费电子书',
        r'电子书下载',
        r'更多精彩图书',
        r'请勿用于商业用途',
        r'仅供.{0,6}交流',
        r'^\s*https?://\S+\s*$',
        r'^\s*www\.\S+\s*$',
    )
]


def _is_watermark(line: str) -> bool:
    """
    判断一行是否整行都是推广/水印。
    只对**较短**的行生效（正常段落里出现这些词不会被整行删掉）。
    """
    t = line.strip()
    if not t or len(t) > 160:
        return False
    return any(p.search(t) for p in _WATERMARK_PATTERNS)


def strip_watermark_lines(text: str) -> str:
    """
    去掉电子书里的推广/水印行（压制组、盗版站插入的）。

    必须在**解析链最后**调用：EPUB 的目录页/版权页判定依赖清洗后文本的行统计，
    提前删行会让那些判定失效。
    """
    if not text:
        return text
    kept = [l for l in text.split('\n') if not _is_watermark(l)]
    return '\n'.join(kept)


def extract_text_from_html(xhtml: str) -> str:
    """从 xhtml/html 提取纯文本。"""
    content = re.sub(r'<head>.*?</head>', '', xhtml, flags=re.S | re.I)
    content = re.sub(r'<script.*?</script>', '', content, flags=re.S | re.I)
    content = re.sub(r'<style.*?</style>', '', content, flags=re.S | re.I)
    content = re.sub(r'<[^>]+>', '\n', content)
    # unescape 实体
    import html as htmlmod
    content = htmlmod.unescape(content)
    return content