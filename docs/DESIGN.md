# 界面设计规范（参照 DeepSeek Harness）

> 本项目 WebUI 的视觉规范。设计 token 提取自 DeepSeek Harness 的
> `@deepseek-ai/dsh-client-ui-theme`（全量快照见 `dsh-tokens-reference.txt`，403 条）。
> 提取时间 2026-10-08。

## 1. 为什么参照 DSH

它的视觉语言有三个特点，都很适合开发者工具：

1. **中性色偏蓝**（`neutral-bluish` 而非纯灰），在白底上不发黄、不脏
2. **高度靠"发丝描边"而不是重阴影**：`0 0 0 .5px <边框色>`
   —— 层次干净、不糊，是这套设计最关键的一条
3. **毛玻璃面板**：`blur(40px) saturate(150%)` + 半透明底

## 2. 颜色

### 品牌色（DeepSeek 蓝）

| token | 值 | 用途建议 |
|---|---|---|
| `deepseek-50` | `#edf3fe` | 选中态底 |
| `deepseek-100` | `#e4edfd` | 浅色标签底 |
| `deepseek-200` | `#d3e2ff` | 悬停底 |
| `deepseek-300` | `#b7c8fe` | 边框强调 |
| `deepseek-400` | `#7aaaff` | 暗色模式下的强调色 |
| **`deepseek-450`** | **`#5686fe`** | **亮色模式强调色** |
| **`deepseek-500`** | **`#4176e6`** | **主品牌色（按钮/链接）** |
| `deepseek-600` | `#4868b2` | 按下态 |
| `deepseek-800` | `#34415b` | 暗色模式标签底 |
| `deepseek-900` | `#283142` | 暗色模式选中底 |

### 中性色（偏蓝灰阶，核心）

| token | 值 | 典型用途 |
|---|---|---|
| `neutral-bluish-00` | `#ffffff` | 亮色：底层背景 |
| `50 / 60 / 75` | `#f9fafb` `#f5f6f7` `#f1f3f5` | 亮色：分层背景 |
| `100 / 150 / 200` | `#ebeef2` `#e9ecf2` `#e1e5ee` | 亮色：hover / overlay |
| `300 / 400 / 500` | `#cfd3d6` `#adb2b8` `#979da6` | 占位文字、禁用 |
| `600 / 700` | `#81858c` `#61666b` | 次要文字 |
| `750 / 800` | `#43454a` `#353638` | 暗色：边框 |
| **`850`** | **`#2c2c2e`** | **暗色：卡片背景** |
| `875 / 900` | `#232324` `#1b1b1c` | 暗色：分层背景 |
| `950 / 1000` | `#151517` `#0f1115` | 暗色：最底背景 / 主文字(亮色) |

### 边框（亮色模式）

| token | 值 | 用途 |
|---|---|---|
| `border-l1` | `#0000000a` | 最轻分隔 |
| `border-l2` | `#0000001a` | 常规边框 |
| `border-l3` | `#0000001f` | 卡片描边 |
| `border-l4` | `#00000029` | 强调/浮层描边 |

## 3. 圆角

| token | 值 |
|---|---|
| `radius-xs` | 4px |
| `radius-sm` | 8px |
| `radius-md` | 12px |
| `radius-lg` | 16px |
| `radius-xl` | 20px |
| **`radius-panel`** | **28px** ← 大面板用这个，是它的标志性特征 |

另有 `--dsw-corner-shape: superellipse(1.5)`（现代 CSS 圆角，浏览器不支持时自动退化为普通圆角）。

## 4. 高度 / 层次

```css
/* 不用重阴影，用发丝描边勾勒层次 */
--elevation-stroke: 0 0 0 .5px var(--border-l4);   /* 面板、浮层 */
--shadow-lv1:       0 2px 4px 0 #0000000d;
--shadow-lv2:       0 4px 12px 0 #00000005;
--shadow-lv3:       0 0 1px 0 #0003;
```

## 5. 毛玻璃（菜单 / 浮层）

```css
--menu-surface-fill: #f8f9fa94;
--menu-backdrop-filter: blur(40px) saturate(150%);
--hovercard-bg: #2C2C2E;     /* 暗色下 */
```

## 6. 字体

| 用途 | 字体 |
|---|---|
| 正文 | `-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif` |
| 品牌 / 标题 | **Montserrat**（300/400/500，SIL OFL） |
| 代码 | `ui-monospace, SFMono-Regular, "Cascadia Code", Consolas, monospace` |

字号阶：`11 / 12 / 13 / 14 / 16 / 18 / 20 / 24px`（xxxs → xl）。

## 7. 落地到本项目（CSS 变量层）

WebUI 用一层 CSS 变量承接上面的 token，方便日后整体换肤：

```css
:root {
  --brand:          #4176e6;   /* deepseek-500 */
  --brand-hover:    #5686fe;   /* deepseek-450 */
  --brand-soft:     #edf3fe;   /* deepseek-50  */
  --bg:             #ffffff;   /* neutral-00   */
  --bg-layer-1:     #f9fafb;   /* neutral-50   */
  --bg-layer-2:     #f5f6f7;   /* neutral-60   */
  --text:           #0f1115;   /* neutral-1000 */
  --text-2:         #61666b;   /* neutral-700  */
  --text-3:         #81858c;   /* neutral-600  */
  --border-1:       #0000000a;
  --border-2:       #0000001a;
  --border-3:       #0000001f;
  --radius-sm: 8px;  --radius-md: 12px;  --radius-lg: 16px;  --radius-panel: 28px;
  --stroke: 0 0 0 .5px var(--border-3);
  --font-brand: "Montserrat", var(--font-sans);
}
```

暗色模式用同一套变量名，只换值（`--bg: #151517`、`--bg-layer-1: #1b1b1c`、
`--text: #ebeef2`、`--border-1: #ffffff14` 等）。

## 8. 界面结构计划

当前 UI 是单页 Gradio（生成书 / 书库两个页签）。计划：

1. **套用上面的主题**（Gradio 支持 `theme=` + 自定义 CSS）
2. **改成左侧栏 + 主区**的布局：
   - 左栏：书库列表（书名 / 进度条 / 状态点）
   - 主区：当前书的章节列表 + 生成控制 + 实时日志
3. **进度可视化**：整体进度条 + 每章完成状态点（`done` / `running` / `paused`）
4. **日志区**用等宽字体、深色面板，贴近 DSH 的终端风格

> 取舍：**保留 Gradio**（功能已经在跑、改动风险低），只做主题与布局重构；
> 不做完整前端重写。若日后要更贴近 DSH，可换成 FastAPI + 静态前端。
