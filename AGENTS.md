# AGENTS.md — 有声书生成工具

> **给在这个仓库里干活的 AI 助手：动手前先读完这一页。**
> 这一页只放"不看就会犯错"的东西。完整背景见 `docs/AGENT_HANDOVER.md`，
> 引擎评测与踩坑全记录见 `tts_eval/README.md`。

---

## 这是什么

把电子书转成**克隆指定音色**的有声书：Qwen3-TTS-0.6B + 段级质检门 + 段缓存断点续跑。
FastAPI 后端（`server.py`）+ 自写前端（`static/`）。

## 仓库布局（仓库根 = `audiobook_tool/`）

```
audiobook_tool/          ← 仓库根，也是 git 根
  server.py              Web 后端（FastAPI，11 个 API）
  static/                前端（index.html / app.css / app.js，无框架）
  engine.py              分段、生成、质检门接线、拼接、状态机
  quality.py             质检门实现（CAM++ 声纹 + 谱指标）
  ebook.py               电子书解析（EPUB/MOBI/AZW3/TXT）
  text_clean.py          文本清洗
  config.py              全部参数与路径解析
  speaker.py             多角色第一层（已实现、已测试，**尚未接入引擎**）
  setup.ps1              一键安装（建 venv / 装依赖 / 下模型 / 自检）
  selfcheck.py           环境自检      selftest_model.py  模型出声测试
  scripts/               对外工具（12 个）
    _dev/                开发期一次性脚本    _legacy/  旧管线脚本
  docs/                  交接文档与设计规范
  tts_eval/              评测框架 + 踩坑记录
  vendor/Qwen3-TTS/      qwen_tts 源码（editable 安装指向这里）
  models/                [gitignore] TTS 权重 + campplus.onnx
  bin/                   [gitignore] ffmpeg / ffprobe
  reference_audio/       [gitignore] 参考音频（版权素材）
  input/ output/ work/   [gitignore] 电子书 / 产物 / 中间件
```

> 仓库根之外只应有 Python 环境（venv 放在仓库外，**移动 venv 会坏**）。
> 项目**不依赖**仓库外的任何路径 —— `config.py` 的解析顺序见文末"路径解析"。

## 跑起来 / 验证

```powershell
$PY = "<你的 venv>\Scripts\python.exe"   # 或设 AUDIOBOOK_PYTHON 环境变量
Set-Location <仓库根>                    # 所有命令都在仓库根执行
& $PY server.py --no-browser                    # http://127.0.0.1:7860
& $PY scripts\verify_parse.py                   # 电子书解析回归（动过 ebook.py 必跑）
& $PY scripts\verify_seg_v2.py                  # 切分算法回归（动过 split_segments 必跑）
& $PY scripts\test_ui_path.py                   # 段缓存指纹回归（动过 chunk_cache_key 必跑）
& $PY scripts\test_speaker_rules.py             # 说话人归属规则（受控用例，10/10）
& $PY scripts\smoke_test_engine.py              # 端到端冒烟测试
& $PY scripts\challenge_library.py <目录>       # 批量挑战整个书库
```

---

## 铁律

### 1. 段缓存指纹：任何影响「文本→音频」对应关系的参数都必须进 `chunk_cache_key()`

段缓存按**段号**命名。分段长度从 205 改到 105 后，同一段号对应的文本完全不同——
复用旧段就是**拿 A 的音频配 B 的文本**。

```python
# engine.chunk_cache_key() 的 parts 必须包含：
chars=        # 每段字数上限
segv=         # ★ 切分算法版本（见下条）
ref=          # 参考音频名+大小
temp/topp/rep/maxnew
```

改动它的回归测试：`scripts/test_ui_path.py`

### 2. 切分算法一改，`config.SEG_VERSION` 必须 +1

否则"上限没变但算法变了" → 指纹不变 → 旧段被静默复用。当前 v2。

### 3. 质检门的四个信号，方向不能搞反

| 信号 | 方向 | 门限 |
|---|---|---|
| `sim` 整体声纹相似度 | 越高越好 | ≥ 0.62 |
| `win_sim` 段内 3s 滑窗最低 | 越高越好 | ≥ 0.45 |
| `flatness` 谱平坦度 | **越低越好** | ≤ 0.08 |
| `hf_tonality` 6-9kHz 平坦度 | **越高越好** | ≥ 0.04 |

`hf_tonality` 是唯一"越低越糟"的高频指标：自然语音该频段含大量非周期成分（气声/齿音），
被过度谐波化后听感即**电音**。真实人声 0.18~0.23，本项目最低 0.052，电音样本 0.027。
**不要为了"减少重生成"调低它。**

### 4. 声纹相似度必须只统计**有声帧**

静音比例会污染结果：同一段语音**只是前后各加 2s 静音**，相似度从 0.7916 涨到 0.8073。
加静音能涨的分（+0.016~0.022）和一次真实音色改进（+0.024）同量级。
另外锚点必须**独立于**被克隆的那段参考音频。

### 5. 噪声页过滤的**顺序**不能反

`ebook._filter_chapters()` 里必须**先**用正文首行修正占位标题，**再**做噪声判定。
反过来就永远识别不出 CIP/目录页（它们的标题在过滤时还是 `part0065` 这种文件名）。

### 6. 去水印必须在**解析链最后**做，不能塞进 `clean_text()`

`clean_text()` 的输出被 EPUB 的目录页/版权页判定依赖（看行数与行长）。
在里面删行会改变那些判定——实测会让 6 个目录页漏过过滤。

---

## 启发式在极端输入上会翻车（本轮 5 个真实 bug）

**这是这个仓库最重要的经验。** 所有"聪明"的启发式都被真实藏书打脸过：

| # | Bug | 症状 | 根因 | 修法 |
|---|---|---|---|---|
| 1 | **内容重复 47 倍** | 《金融机器学习》61 万字报成 **2870 万字 / 244 册** | 244 个目录项指向**同一个文件**（只是锚点不同），被当成 244 册 | 按"指向的文件"去重 |
| 2 | **单本小说拆成 44 册** | 豆豆三部曲每册 7.8k 字 | "顶层项正文 ≥2000 字即为一册"阈值太低，章节也过 | 阈值提到 `MIN_VOLUME_CHARS`=20000 |
| 3 | **MOBI 丢 100 万字** | 正文 102 万字只解出 **56 字节** | "第一个分页标记之前都是目录"——该书分页标记在**全文末尾** | 必须确认那段真含"目录/Contents"且 <5 万字节 |
| 4 | **漫画判定误伤 110 本轻小说** | 成功数 146 → 36 | 只看"图片占体积 >50%"，轻小说彩插同样占 90%+ | 必须**同时**满足图片占体积过半 **且** 文字量 <2 万 |
| 5 | **`.azw3` 被判成漫画** | 646 万字的书被跳过 | KF8 容器不是 zip，廉价探测拿不到字数 → `est=0` 触发"体积大字数少" | 依赖真实字数的规则**只能放在解析之后** |

**结论**：加任何启发式时，必须问"**极端输入下它会怎么错**"，并配一个回归测试。
第 1、2、3 条都是"在正常书上没问题、在怪书上灾难"的典型。

---

## 其他必须知道的

- **MOBI/AZW3 两条路**：先试 `mobi` 库（KF8/AZW3 解出 EPUB → 交给 EPUB 解析器）；
  失败则用自带的 `_mobi_palmdoc_html()`。MOBI 正文**跨记录连续压缩**，
  必须整段流解压，逐记录独立解压必崩。
- **TXT 不猜章节**，整本作为一章（用户明确要求）——纯文本没有可靠章节标记，硬猜会切错。
- **路径解析是可移植的**，`config.py` 里每条外部资源按这个顺序找：
  ① 环境变量 `AUDIOBOOK_MODEL_DIR / REF_AUDIO / REF_TEXT / CAMPPLUS / FFMPEG / FFPROBE / PYTHON`
  ② **项目内** `<仓库根>/models`、`/bin`、`/reference_audio`（默认情形）
  ③ **发行包内** `<包根>/models` 等（打包布局）
  ④ 系统 PATH    ⑤ 可选开发机兜底（设 `AUDIOBOOK_DEV_ROOT` 才启用）
  **不要把绝对路径写回代码。**
- **`transformers` 必须锁 4.57.3**（`qwen_tts` 强依赖）。
- **网络**：`huggingface.co` 不通，走 ModelScope 或 `hf-mirror.com`。
- **未接入**：`speaker.py`（说话人轮次切分 + 规则归属）已实现并通过 10/10 受控测试，
  但**没有接进引擎**。规则归属在真实小说上只覆盖 ~10%（大量用"我/她/女士"这类代词和通称），
  要做真·多角色需要 LLM。详见 `docs/AGENT_HANDOVER.md` 第 10 节。
- **参考音频**是版权素材，**不入库**（`.gitignore` 已排除）。用户需自备。

## 文档地图

| 文件 | 内容 |
|---|---|
| `docs/AGENT_HANDOVER.md` | **交接主文档**：架构、数据契约、不变量、实测基准、评测方法 |
| `docs/DESIGN.md` | 界面设计规范（提取自 DeepSeek Harness 的设计 token） |
| `tts_eval/README.md` | 已否决的 6 个引擎、3 个会让指标骗人的陷阱、评测流程 |
| `tts_eval/reports/baseline.md` | 当前采纳的声音基准（全章 526 段实测数据） |
| `README.md` | 面向使用者的安装与使用说明 |
