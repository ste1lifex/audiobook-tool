# -*- coding: utf-8 -*-
r"""
有声书生成工具 - Web 后端（FastAPI）

跑法:
    <venv>\Scripts\python.exe server.py                 # http://127.0.0.1:7860
    <venv>\Scripts\python.exe server.py --port 8080
    <venv>\Scripts\python.exe server.py --host 0.0.0.0 --no-browser

与旧 Gradio 版的对应关系：功能一一保留（选书/解析预览/选分册/参考音色/生成/停止/
书库续跑/刷新/打开输出目录），只是把 UI 换成自写前端。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
import traceback

WORKING = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKING)

from fastapi import FastAPI, File, HTTPException, UploadFile      # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse          # noqa: E402
from fastapi.staticfiles import StaticFiles                       # noqa: E402

import config                                                     # noqa: E402
import ebook                                                      # noqa: E402
import engine                                                     # noqa: E402

STATIC_DIR = os.path.join(WORKING, 'static')
BOOK_EXT = ('.epub', '.mobi', '.azw3', '.azw', '.txt')


# ══════════════════════════════════════════════════════════════════
#  任务管理（一次只跑一个；与旧 UI 行为一致）
# ══════════════════════════════════════════════════════════════════
class Job:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self._log: list[str] = []
        self._lock = threading.Lock()
        self.seq = 0
        self.state = 'idle'          # idle | running | done | error | stopped
        self.message = ''
        self.book = ''
        self.chapter = 0
        self.chapter_total = 0
        self.seg_done = 0
        self.seg_total = 0
        self.audio_sec = 0.0
        self.started_at = 0.0

    # ---- 日志 ----
    def log(self, s):
        with self._lock:
            for line in str(s).splitlines() or ['']:
                if line != '__DONE__':
                    self._log.append(line)
                self.seq += 1

    def tail(self, since=0):
        with self._lock:
            return self._log[max(0, since):], self.seq

    # ---- 状态 ----
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def snapshot(self, since=0):
        log, seq = self.tail(since)
        return {
            'state': self.state,
            'message': self.message,
            'book': self.book,
            'chapter': self.chapter,
            'chapter_total': self.chapter_total,
            'seg_done': self.seg_done,
            'seg_total': self.seg_total,
            'audio_sec': self.audio_sec,
            'running': self.running(),
            'elapsed': (time.time() - self.started_at) if self.started_at else 0.0,
            'seq': seq,
            'log': log,
        }

    def start(self, target, args):
        if self.running():
            raise HTTPException(409, '已有任务在运行，请先停止')
        self.stop = threading.Event()
        with self._lock:
            self._log = []
        self.seq = 0
        self.state = 'running'
        self.message = ''
        self.started_at = time.time()
        self.thread = threading.Thread(target=target, args=args, daemon=True)
        self.thread.start()


JOB = Job()


# ══════════════════════════════════════════════════════════════════
#  生成流程（从旧 app.py 移植，逻辑一致）
# ══════════════════════════════════════════════════════════════════
def _progress(ci, done, total, audio):
    JOB.chapter = ci
    JOB.seg_done, JOB.seg_total, JOB.audio_sec = done, total, audio


def _run(book_dir, chapters, name, ref_audio, ref_text, collection=''):
    JOB.chapter_total = len(chapters)
    try:
        full_mp3, _st = engine.synthesize_book(
            book_dir, chapters, ref_audio=ref_audio, ref_text=ref_text,
            stop_event=JOB.stop, progress_cb=_progress,
            collection=collection, book_title=name)
    except Exception as e:                                     # noqa: BLE001
        JOB.log(f'❌ 生成出错: {e}')
        JOB.log(traceback.format_exc())
        JOB.state = 'error'
        JOB.message = str(e)
        return
    if full_mp3 and os.path.exists(full_mp3):
        JOB.log(f'✅ 完成！整本音频: {full_mp3}')
        JOB.log(f'📁 全部产物在: {book_dir}')
        JOB.state = 'done'
        JOB.message = '全部章节已完成'
    else:
        JOB.log(f'⏸️ 已暂停（进度已保存，可在书库继续）: {book_dir}')
        JOB.state = 'stopped'
        JOB.message = '已停止，可续跑'


def _bg_new_book(book_src, book_name, ref_audio, ref_text, volume_filter=''):
    try:
        collection, books = ebook.parse_books(book_src)
        if book_name and book_name.strip():
            collection = book_name.strip()
        wanted = [v.strip() for v in (volume_filter or '').split('|') if v.strip()]
        if wanted:
            books = [b for b in books if b['title'] in wanted]
        if not books:
            JOB.log('❌ 没有匹配的分册（请重新解析并勾选）')
            JOB.state, JOB.message = 'error', '没有匹配的分册'
            return
        JOB.log(f'《{collection}》待生成 {len(books)} 册：{[b["title"] for b in books]}')
        for bi, book in enumerate(books, 1):
            if JOB.stop.is_set():
                JOB.log('⏹ 已停止，后续分册未开始')
                JOB.state, JOB.message = 'stopped', '已停止'
                return
            title, chs = book['title'], book['chapters']
            root = os.path.join(config.OUTPUT_BASE, engine.safe_name(title))
            exist = engine.load_state(root) if os.path.isdir(root) else None
            if exist and exist.get('status') == 'done':
                JOB.log(f'⏭ 跳过《{title}》（已完成 {len(exist["chapters"])} 章）')
                continue
            os.makedirs(root, exist_ok=True)
            ext = os.path.splitext(book_src)[1]
            shutil.copy2(book_src, os.path.join(root, f'source{ext}'))
            total = sum(len(c['text']) for c in chs)
            JOB.book = title
            JOB.log(f'[{bi}/{len(books)}] 开始《{title}》 {len(chs)} 章 / {total:,} 字 → {root}')
            _run(root, chs, title, ref_audio, ref_text, collection=collection)
            if JOB.state in ('error', 'stopped'):
                return
        if JOB.state == 'running':
            JOB.state, JOB.message = 'done', '全部完成'
    except Exception as e:                                     # noqa: BLE001
        JOB.log(f'❌ 出错: {e}')
        JOB.log(traceback.format_exc())
        JOB.state, JOB.message = 'error', str(e)


def _bg_resume(book_dir):
    try:
        chapters = engine.load_chapters(book_dir)
        st = engine.load_state(book_dir)
        if not chapters or not st:
            JOB.log('❌ 该书没有章节快照/状态，无法继续（可能未正常生成过）')
            JOB.state, JOB.message = 'error', '缺少快照'
            return
        name = st.get('book', os.path.basename(book_dir))
        cfg = st.get('config', {})
        ref_audio = cfg.get('ref_audio') or config.DEFAULT_REF_AUDIO
        ref_text = cfg.get('ref_text') or config.DEFAULT_REF_TEXT
        done = sum(1 for c in st['chapters'] if c.get('done'))
        JOB.book = name
        JOB.log(f'继续《{name}》 已完成 {done}/{len(chapters)} 章，恢复音色配置')
        _run(book_dir, chapters, name,
             ref_audio if os.path.exists(ref_audio) else None, ref_text)
    except Exception as e:                                     # noqa: BLE001
        JOB.log(f'❌ 出错: {e}')
        JOB.log(traceback.format_exc())
        JOB.state, JOB.message = 'error', str(e)


# ══════════════════════════════════════════════════════════════════
#  数据查询
# ══════════════════════════════════════════════════════════════════
def list_input_files():
    if not os.path.isdir(config.INPUT_DIR):
        return []
    return [f for f in sorted(os.listdir(config.INPUT_DIR)) if f.lower().endswith(BOOK_EXT)]


def book_rows():
    rows = []
    if not os.path.isdir(config.OUTPUT_BASE):
        return rows
    for d in sorted(os.listdir(config.OUTPUT_BASE)):
        bdir = os.path.join(config.OUTPUT_BASE, d)
        if not os.path.isdir(bdir):
            continue
        st = engine.load_state(bdir)
        if not st:
            continue
        chs = st.get('chapters', [])
        done = sum(1 for c in chs if c.get('done'))
        total = len(chs) or 1
        rows.append({
            'dir': bdir,
            'name': st.get('book', d),
            'collection': st.get('collection', ''),
            'status': st.get('status', ''),
            'done': done, 'total': total,
            'ratio': round(done / total, 4),
            'chars': sum(c.get('chars', 0) for c in chs),
            'created': st.get('created', ''),
            'config': st.get('config', {}),
        })
    return rows


# ══════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════
app = FastAPI(title='有声书生成工具', docs_url=None, redoc_url=None)


@app.get('/', response_class=HTMLResponse)
def index():
    p = os.path.join(STATIC_DIR, 'index.html')
    if not os.path.exists(p):
        return HTMLResponse('<h1>缺少 static/index.html</h1>', status_code=500)
    with open(p, encoding='utf-8') as f:
        return HTMLResponse(f.read())


@app.get('/api/bootstrap')
def bootstrap():
    """首屏一次性拿到：输入目录、输入文件、书库、当前任务状态。"""
    return {
        'input_dir': config.INPUT_DIR,
        'output_dir': config.OUTPUT_BASE,
        'files': list_input_files(),
        'books': book_rows(),
        'default_ref_text': config.DEFAULT_REF_TEXT,
        'default_ref_audio': os.path.basename(config.DEFAULT_REF_AUDIO),
        'job': JOB.snapshot(0),
        'config': {
            'max_chars': config.MAX_CHARS_PER_SEG,
            'seg_version': getattr(config, 'SEG_VERSION', 1),
            'temperature': config.TEMPERATURE,
            'top_p': config.TOP_P,
            'repetition_penalty': config.REPETITION_PENALTY,
            'max_new_tokens': config.MAX_NEW_TOKENS,
            'seg_silence': config.SEG_SILENCE_SEC,
            'ref_audio': os.path.basename(config.DEFAULT_REF_AUDIO),
        },
    }


@app.get('/api/parse')
def api_parse(file: str):
    path = os.path.join(config.INPUT_DIR, os.path.basename(file))
    if not os.path.exists(path):
        raise HTTPException(404, f'文件不存在: {path}')
    try:
        collection, books = ebook.parse_books(path)
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(400, f'解析失败: {e}')
    out = []
    for b in books:
        out.append({
            'title': b['title'],
            'chapters': len(b['chapters']),
            'chars': sum(len(c['text']) for c in b['chapters']),
            'preview': [{'index': c['index'], 'title': c['title'],
                         'chars': len(c['text'])} for c in b['chapters'][:30]],
        })
    return {'collection': collection, 'file': os.path.basename(file),
            'single': len(books) == 1, 'books': out}


@app.post('/api/upload')
async def api_upload(file: UploadFile = File(...)):
    base = os.path.basename(file.filename or 'upload.epub')
    if not base.lower().endswith(BOOK_EXT):
        raise HTTPException(400, f'不支持的格式（{", ".join(BOOK_EXT)}）')
    os.makedirs(config.INPUT_DIR, exist_ok=True)
    dst = os.path.join(config.INPUT_DIR, base)
    with open(dst, 'wb') as f:
        shutil.copyfileobj(file.file, f)
    return {'ok': True, 'file': base, 'files': list_input_files()}


@app.post('/api/upload-ref')
async def api_upload_ref(file: UploadFile = File(...)):
    """参考音色音频：存到 reference_audio/_uploaded/ 并返回路径。"""
    base = os.path.basename(file.filename or 'ref.wav')
    d = os.path.join(os.path.dirname(config.DEFAULT_REF_AUDIO), '_uploaded')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, base)
    with open(dst, 'wb') as f:
        shutil.copyfileobj(file.file, f)
    return {'ok': True, 'path': dst, 'name': base}


@app.post('/api/generate')
async def api_generate(payload: dict):
    file = (payload.get('file') or '').strip()
    if not file:
        raise HTTPException(400, '请先选择电子书')
    path = os.path.join(config.INPUT_DIR, os.path.basename(file))
    if not os.path.exists(path):
        raise HTTPException(404, f'文件不存在: {path}')
    vols = payload.get('volumes') or []
    ref_audio = payload.get('ref_audio') or None
    if ref_audio and not os.path.exists(ref_audio):
        ref_audio = None
    ref_text = payload.get('ref_text') or config.DEFAULT_REF_TEXT
    JOB.start(_bg_new_book, (path, payload.get('book_name') or '',
                             ref_audio, ref_text, '|'.join(vols)))
    return {'ok': True}


@app.post('/api/resume')
async def api_resume(payload: dict):
    bdir = (payload.get('dir') or '').strip()
    if not bdir or not os.path.isdir(bdir):
        raise HTTPException(404, '书目目录不存在')
    # 防目录穿越：必须在 OUTPUT_BASE 下
    if os.path.commonpath([os.path.abspath(bdir), os.path.abspath(config.OUTPUT_BASE)]) \
            != os.path.abspath(config.OUTPUT_BASE):
        raise HTTPException(403, '目录不在书库内')
    JOB.start(_bg_resume, (bdir,))
    return {'ok': True}


@app.post('/api/stop')
def api_stop():
    if JOB.running():
        JOB.stop.set()
        return {'ok': True, 'message': '已发送停止指令，当前段结束后暂停（进度已保存）'}
    return {'ok': True, 'message': '当前没有运行中的任务'}


@app.get('/api/status')
def api_status(since: int = 0):
    return JOB.snapshot(since)


@app.get('/api/books')
def api_books():
    return {'books': book_rows()}


@app.post('/api/open-output')
def api_open_output(payload: dict | None = None):
    d = (payload or {}).get('dir') or config.OUTPUT_BASE
    os.makedirs(d, exist_ok=True)
    try:
        os.startfile(d)                                        # noqa: S606 (Windows)
    except Exception:                                          # noqa: BLE001
        pass
    return {'ok': True, 'dir': d}


@app.get('/api/ref-audio')
def api_ref_audio():
    p = config.DEFAULT_REF_AUDIO
    if not os.path.exists(p):
        raise HTTPException(404, '默认参考音频不存在')
    return FileResponse(p, media_type='audio/wav')


app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


# ══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='有声书生成工具 Web 服务')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=7860)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    import uvicorn
    url = f'http://{args.host if args.host != "0.0.0.0" else "127.0.0.1"}:{args.port}'
    print(f'\n  有声书生成工具\n  界面: {url}\n  输入: {config.INPUT_DIR}\n'
          f'  输出: {config.OUTPUT_BASE}\n', flush=True)
    if not args.no_browser:
        threading.Timer(1.5, lambda: __import__('webbrowser').open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
