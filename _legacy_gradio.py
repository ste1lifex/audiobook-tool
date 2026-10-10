# -*- coding: utf-8 -*-
# ⚠ 已归档：旧 Gradio 版界面，由 server.py + static/ 取代（2026-10）。
# 保留仅供对照，不再维护。原文如下。
r"""
有声书生成工具 - Web UI（Gradio）v2 联动版
跑法: <venv>\Scripts\python.exe app.py  →  打开 http://127.0.0.1:7860

界面设计:
  「📖 生成书」: 从输入目录(input/)选一本新书 或 上传 → 解析预览 → 开始生成
  「📚 书库」  : 列出全部项目 → 点「继续」续跑未完成的书（从断点、恢复保存的配置）
  「带保存的项目文件」: 每本书目录下 task_state.json(进度/配置) + _chapters.json(章节快照)
"""
import os
import shutil
import sys
import threading
import time

WORKING = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKING)

import gradio as gr
import engine
import ebook
import config

_current = {"thread": None, "stop": threading.Event(), "log": []}
BOOK_EXT = ('.epub', '.mobi', '.azw3', '.azw', '.txt')


# ================= 后台任务（新书 / 续跑共用） =================
def _bg_new_book(book_src, book_name, ref_audio, ref_text, volume_filter=''):
    """从输入文件开始生成。合集时按 volume_filter（分册名，|分隔）只生成选中的分册。"""
    try:
        collection, books = ebook.parse_books(book_src)
        if book_name and book_name.strip():
            collection = book_name.strip()
        wanted = [v.strip() for v in (volume_filter or '').split('|') if v.strip()]
        if wanted:
            books = [b for b in books if b['title'] in wanted]
        if not books:
            _current["log"].append("❌ 没有匹配的分册（请重新解析并勾选）")
            return
        _current["log"].append(f"《{collection}》 待生成 {len(books)} 册: "
                               f"{[b['title'] for b in books]}")
        for bi, book in enumerate(books, 1):
            if _current["stop"].is_set():
                _current["log"].append("⏹ 用户停止，后续分册未开始")
                break
            title = book['title']
            chs = book['chapters']
            root = os.path.join(config.OUTPUT_BASE, engine.safe_name(title))
            # 已完成的分册直接跳过，避免重复跑（想重做请先删除该目录）
            exist = engine.load_state(root) if os.path.isdir(root) else None
            if exist and exist.get('status') == 'done':
                _current["log"].append(f"⏭ 跳过《{title}》（已完成 {len(exist['chapters'])} 章）")
                continue
            os.makedirs(root, exist_ok=True)
            src_ext = os.path.splitext(book_src)[1]
            shutil.copy2(book_src, os.path.join(root, f"source{src_ext}"))
            total_chars = sum(len(c['text']) for c in chs)
            _current["log"].append(f"[{bi}/{len(books)}] 开始《{title}》 {len(chs)} 章 / {total_chars} 字 → {root}")
            _run(root, chs, title, ref_audio, ref_text, collection=collection)
    except Exception as e:
        import traceback
        _current["log"].append(f"❌ 出错: {e}\n{traceback.format_exc()}")
    finally:
        _current["log"].append("__DONE__")


def _bg_resume(book_dir):
    """从书库继续：加载章节快照 + 恢复保存的配置。"""
    try:
        chapters = engine.load_chapters(book_dir)
        st = engine.load_state(book_dir)
        if not chapters or not st:
            _current["log"].append("❌ 该书没有快照/状态，无法继续（可能未正常生成过）")
            return
        name = st.get('book', os.path.basename(book_dir))
        cfg = st.get('config', {})
        ref_audio = cfg.get('ref_audio') or config.DEFAULT_REF_AUDIO
        ref_text = cfg.get('ref_text') or config.DEFAULT_REF_TEXT
        done = sum(1 for c in st['chapters'] if c.get('done'))
        _current["log"].append(f"继续《{name}》 已完成 {done}/{len(chapters)} 章，恢复音色配置 → {book_dir}")
        _run(book_dir, chapters, name, ref_audio if os.path.exists(ref_audio) else None, ref_text)
    except Exception as e:
        import traceback
        _current["log"].append(f"❌ 出错: {e}\n{traceback.format_exc()}")
    finally:
        _current["log"].append("__DONE__")


def _run(book_dir, chapters, name, ref_audio, ref_text, collection=''):
    def progress(ci, done, total, audio):
        _current["log"].append(f"章节 {ci}/{len(chapters)}: {done}/{total} 段, 累计 {audio/60:.1f} 分钟")

    full_mp3, st = engine.synthesize_book(
        book_dir, chapters, ref_audio=ref_audio, ref_text=ref_text,
        stop_event=_current["stop"], progress_cb=progress,
        collection=collection, book_title=name)
    if full_mp3 and os.path.exists(full_mp3):
        _current["log"].append(f"✅ 完成！整本: {full_mp3}")
        _current["log"].append(f"📁 全部音频在: {book_dir}")
    else:
        _current["log"].append(f"⏸️ 已暂停（进度已保存，书库中可继续）: {book_dir}")


# ================= 输入目录 =================
def list_input_files():
    """列出输入目录中的电子书。"""
    files = []
    if os.path.isdir(config.INPUT_DIR):
        for f in sorted(os.listdir(config.INPUT_DIR)):
            if f.lower().endswith(BOOK_EXT):
                files.append(f)
    return files


def save_upload_to_input(file_path):
    """把上传的文件复制进输入目录（若尚不存在）。"""
    if not file_path:
        return None
    base = os.path.basename(file_path)
    dst = os.path.join(config.INPUT_DIR, base)
    if not os.path.exists(dst):
        shutil.copy2(file_path, dst)
    return dst


# ================= 前端事件 =================
def _volume_names(path):
    """返回合集分册名列表；单本返回空列表。"""
    try:
        _, books = ebook.parse_books(path)
        if len(books) > 1:
            return [b['title'] for b in books]
    except Exception:
        pass
    return []


def pick_input(fname):
    """输入目录下拉选择 → 解析预览。"""
    if not fname:
        return "请先在输入目录放入电子书，或直接上传文件。", "", gr.update(choices=[], value=[])
    path = os.path.join(config.INPUT_DIR, fname)
    if not os.path.exists(path):
        return f"❌ 文件不存在: {path}", "", gr.update(choices=[], value=[])
    md, name = _parse(path)
    vols = _volume_names(path)
    return md, name, gr.update(choices=vols, value=vols)


def upload_and_parse(uploaded):
    if not uploaded:
        return "未上传文件", "", gr.update(choices=[], value=[])
    dst = save_upload_to_input(uploaded)
    md, name = _parse(dst)
    vols = _volume_names(dst)
    return md, name, gr.update(choices=vols, value=vols)


def _parse(path):
    """解析：合集会列出分册供勾选；单本直接展示章节。返回 (预览md, 默认书名, 分册选择器更新)。"""
    try:
        name, books = ebook.parse_books(path)
        if len(books) == 1:
            chs = books[0]['chapters']
            md = f"## 《{books[0]['title']}》 共 {len(chs)} 章（单本）\n\n| # | 章节 | 字数 |\n|---|---|---|\n"
            for c in chs[:200]:
                md += f"| {c['index']} | {c['title'][:40]} | {len(c['text'])} |\n"
            if len(chs) > 200:
                md += f"\n（仅展示前 200 章，共 {len(chs)} 章）"
            return md, name
        # 合集
        md = f"# 📚 检测到合集：共 {len(books)} 册\n\n"
        md += f"来源：`{name}`\n\n| # | 分册 | 章节数 | 字数 |\n|---|---|---|---|\n"
        for i, b in enumerate(books, 1):
            total = sum(len(c['text']) for c in b['chapters'])
            md += f"| {i} | **{b['title']}** | {len(b['chapters'])} | {total:,} |\n"
        md += "\n**请在下方勾选要生成的分册**（默认全选；可只选一册先跑）。\n"
        # 展示第一册章节预览
        b0 = books[0]
        md += f"\n<details><summary>《{b0['title']}》章节预览（前 30 章）</summary>\n\n"
        for c in b0['chapters'][:30]:
            md += f"- {c['index']}. {c['title'][:40]} ({len(c['text'])}字)\n"
        md += "\n</details>"
        return md, name
    except Exception as e:
        return f"❌ 解析失败：{e}", ""


def _start_from_input(fname, book_name, selected_vols, ref_audio, ref_text):
    if not fname:
        yield "请选择电子书", ""
        return
    path = os.path.join(config.INPUT_DIR, fname)
    if _current["thread"] and _current["thread"].is_alive():
        yield "⚠️ 已有任务在跑，请先停当前任务。", ""
        return
    vol_filter = '|'.join(selected_vols or [])
    _current["stop"] = threading.Event()
    _current["log"] = []
    t = threading.Thread(target=_bg_new_book,
                         args=(path, book_name, ref_audio, ref_text, vol_filter), daemon=True)
    _current["thread"] = t
    t.start()
    shown = 0
    while t.is_alive():
        time.sleep(1.5)
        if len(_current["log"]) > shown:
            shown = len(_current["log"])
            yield "\n".join(_current["log"][-30:]), ""
    yield "\n".join(_current["log"]).replace("__DONE__", ""), "任务结束，见日志。"


def _resume_from_library(label):
    bdir = _book_map().get(label)
    if not bdir:
        yield "请先在书库选择一本书（点「刷新书库」更新列表）", ""
        return
    if _current["thread"] and _current["thread"].is_alive():
        yield "⚠️ 已有任务在跑，请先停当前任务。", ""
        return
    _current["stop"] = threading.Event()
    _current["log"] = []
    t = threading.Thread(target=_bg_resume, args=(bdir,), daemon=True)
    _current["thread"] = t
    t.start()
    shown = 0
    while t.is_alive():
        time.sleep(1.5)
        if len(_current["log"]) > shown:
            shown = len(_current["log"])
            yield "\n".join(_current["log"][-30:]), ""
    yield "\n".join(_current["log"]).replace("__DONE__", ""), "任务结束，见日志。"


def _stop():
    if _current["thread"] and _current["thread"].is_alive():
        _current["stop"].set()
        return "已发送停止指令，当前段结束后暂停（进度已保存）"
    return "当前没有运行中的任务"


def _book_rows():
    rows = []
    if os.path.isdir(config.OUTPUT_BASE):
        for d in sorted(os.listdir(config.OUTPUT_BASE)):
            bdir = os.path.join(config.OUTPUT_BASE, d)
            if not os.path.isdir(bdir):
                continue
            st = engine.load_state(bdir)
            if not st:
                continue
            done = sum(1 for c in st['chapters'] if c.get('done'))
            status = {'running': '生成中', 'done': '✅ 已完成', 'paused': '⏸️ 已暂停'}.get(st['status'], st['status'])
            name = st.get('book', d)
            coll = st.get('collection', '')
            rows.append([name, status, f"{done}/{len(st['chapters'])}", coll, str(bdir)])
    return rows


def _book_map():
    """label -> bdir 映射，供书库下拉用。label 含状态/进度便于区分。"""
    out = {}
    if os.path.isdir(config.OUTPUT_BASE):
        for d in sorted(os.listdir(config.OUTPUT_BASE)):
            bdir = os.path.join(config.OUTPUT_BASE, d)
            if not os.path.isdir(bdir):
                continue
            st = engine.load_state(bdir)
            if not st:
                continue
            done = sum(1 for c in st['chapters'] if c.get('done'))
            total = len(st['chapters'])
            label = f"{d}（{st['status']} {done}/{total}）"
            out[label] = str(bdir)
    return out


def _book_choices():
    return list(_book_map().keys())


def _open_dir():
    os.makedirs(config.OUTPUT_BASE, exist_ok=True)
    try:
        os.startfile(config.OUTPUT_BASE)
    except Exception:
        pass
    return f"输出目录: {config.OUTPUT_BASE}"


# ============ UI ============
with gr.Blocks(title="有声书生成工具", css="footer{display:none}") as demo:
    gr.Markdown("# 🎧 有声书生成工具\n`input/` 目录放电子书 → 选书开始；书库可断点续跑。")

    with gr.Tab("📖 生成书"):
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown(f"**输入目录**: `{config.INPUT_DIR}`（放入电子书即出现在下方列表）")
                input_dd = gr.Dropdown(label="① 从输入目录选书", choices=list_input_files())
                btn_pick = gr.Button("🔍 解析预览", variant='secondary')
                gr.Markdown("— 或直接上传 —")
                upload_file = gr.File(label="② 上传电子书（将复制进 input/）",
                                      file_types=list(BOOK_EXT))
                btn_upload_parse = gr.Button("🔍 解析上传文件", variant='secondary')
                vol_select = gr.CheckboxGroup(label="③ 选择分册（合集时自动列出，可只选一册）",
                                              choices=[], value=[])
                name_in = gr.Textbox(label="④ 合集/书名（可选，留空用文件名）")
                ref_audio_in = gr.Audio(label="⑤ 参考音色（可选，默认参考音色）", type='filepath')
                ref_text_in = gr.Textbox(label="⑥ 参考音色对应文本", value=config.DEFAULT_REF_TEXT, lines=2)
                with gr.Row():
                    btn_start = gr.Button("▶ 开始生成", variant='primary')
                    btn_stop = gr.Button("⏹ 停止", variant='stop')
            with gr.Column(scale=2):
                preview_out = gr.Markdown("先选书/上传，再点「解析预览」")
                log_out = gr.Textbox(label="进度日志", lines=20, interactive=False)
                status_out = gr.Textbox(label="状态", interactive=False)

        btn_pick.click(fn=pick_input, inputs=[input_dd],
                       outputs=[preview_out, name_in, vol_select])
        btn_upload_parse.click(fn=upload_and_parse, inputs=[upload_file],
                               outputs=[preview_out, name_in, vol_select])
        btn_start.click(fn=_start_from_input,
                        inputs=[input_dd, name_in, vol_select, ref_audio_in, ref_text_in],
                        outputs=[log_out, status_out])
        btn_stop.click(fn=_stop, outputs=[status_out])

    with gr.Tab("📚 书库"):
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("选择项目 → 点「继续」从断点续跑（自动恢复已保存的音色配置）")
                lib_dd = gr.Dropdown(label="选择项目", choices=_book_choices())
                btn_resume = gr.Button("▶ 继续生成", variant='primary')
                btn_stop2 = gr.Button("⏹ 停止", variant='stop')
                with gr.Row():
                    btn_refresh = gr.Button("🔄 刷新书库")
                    btn_open = gr.Button("📂 打开输出目录")
            with gr.Column(scale=2):
                table = gr.Dataframe(headers=["书名", "状态", "进度", "所属合集", "目录"],
                                     datatype=["str", "str", "str", "str", "str"], interactive=False)
                log_lib = gr.Textbox(label="进度日志", lines=18, interactive=False)
                status_lib = gr.Textbox(label="状态", interactive=False)

        def refresh_all():
            return _book_choices(), [list(r) for r in _book_rows()]

        btn_refresh.click(fn=refresh_all, outputs=[lib_dd, table], queue=False, show_progress='hidden')
        btn_open.click(fn=_open_dir, outputs=[status_lib], queue=False, show_progress='hidden')
        btn_resume.click(fn=_resume_from_library, inputs=[lib_dd],
                         outputs=[log_lib, status_lib])
        btn_stop2.click(fn=_stop, outputs=[status_lib])

if __name__ == '__main__':
    demo.launch(server_name='0.0.0.0', server_port=7860, inbrowser=True, show_error=True, quiet=True)
