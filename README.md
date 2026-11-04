# 漫画生成器（本地 ComfyUI · Z-Image-Turbo · handraw-style）

把一篇小说自动变成一套**可以直接往下滑的快看式彩色条漫**，也可以排版成**一页多格的漫画页**。

上传或粘贴小说 → 自动识别角色与场景 → **按故事气质选画风（365 种 handraw 画风 + 36 种主题色）** →
生成角色人设、主视图/面部特写/单视图/三视图/俯视图 → 导演层拆解分镜 → **按内容编页并自主选择漫画分镜版式（SB-001–SB-071）** →
调用本地 ComfyUI（基础 `image_z_image_turbo_int8` / 画风色彩迁移 `..._style_color`）逐格出图 →
程序画气泡并填字 → **按版式拼成漫画页** → 墨线分格 / 封面 / 话标题 / 尾页 → 条漫长图 + PDF 打包。

核心目标是"漫画成立"而不是"插图好看"：
每格一个 beat、有机位/景别/构图/光线/画幅节奏、台词与旁白分工明确、
气泡不压脸、格子之间有 gutter、整话有钩子与悬念落点；
画风不再只有一套韩漫模板，而是从 `handraw-style/` 的编号化风格库里选，
并且**一话里该长条就长条、该分格页就分格页**。

默认支持「**一键全流程**」：分析 → 选画风 → 人设 → 分镜 → 分页/选版式 → 出图 → 自检/自动返修 →
拼页 → 导出；每一格出图后都会检查白边、文字溢出、多余气泡、全黑等硬伤，
低于阈值自动重画，并保留最高分的一版。用户仍然可以随时停下来改设定，再继续。

> 当前默认策略与能力边界：[docs/项目现状与默认策略.md](docs/项目现状与默认策略.md)
> 创作工坊（表情包/海报/信息图，非漫画）：[docs/创作工坊.md](docs/创作工坊.md)
> 分镜提示词翻译层（shot JSON → 观察式 prompt）：[docs/分镜提示词翻译层.md](docs/分镜提示词翻译层.md)
> 需求与验收：[Z-Image-Turbo 小说漫画生成系统 — 需求说明书](Z-Image-Turbo%20小说漫画生成系统%20—%20需求说明书.md)
> 条漫/分页规则：[条漫叙事与分镜规则.md](条漫叙事与分镜规则.md)

---

## 一、快速开始

```powershell
# 1) 安装依赖（使用项目自带的虚拟环境）
env\Scripts\python.exe -m pip install -r requirements.txt

# 2) 填写 API 密钥（见下方「配置」章节）

# 3) 启动
env\Scripts\python.exe server.py
```

启动后浏览器会自动打开 <http://127.0.0.1:7870>。
若没自动打开，手动访问该地址即可。

> 想先看看界面：即使 ComfyUI / 文本模型还没启动，界面仍可浏览工程、编辑工作流与提示词；
> 真正出图和 AI 分析需要对应的本地服务在线。

### 成品样例

样例不再固定附带图片，运行后会自动生成到 `outputs/<输出文件夹名>/`，避免文档里
残留已经被删除的图片路径。当前一次真实生成的样例：

- `outputs/222/`：15 格 / 4 页，FI-030 + T-15，含分镜图、分格页、条漫长图、PDF
- `outputs/trigger_test/`：同一格分镜的风格触发词 A/B 对比

需要快速查看时，打开 `outputs/222/05_分镜图/页面/` 与 `outputs/222/06_条漫/`。

---

## 二、配置（全部集中在 `config.py`）

### 1. 两套模型服务

| 用途 | 服务 | 默认地址 | 密钥配置项 |
| --- | --- | --- | --- |
| 文本理解（分析 / 人设 / 分镜 / 导演优化） | DeepSeek 官方 API，`deepseek-flash`（DeepSeek-V4.1-Flash-1017） | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| 图像生成 | 本地 ComfyUI（工作流 `image_z_image_turbo_int8`，Z-Image-Turbo int8） | `http://127.0.0.1:8188` | 默认无需密钥（反代鉴权才填 `COMFY_API_KEY`） |

> **关于模型名**：DeepSeek 官方接口用模型 id 路由，当前只接受 `deepseek-flash`
> 与 `deepseek-v4-pro`。`DeepSeek-V4.1-Flash-1017` 是产品展示名，直接当模型 id
> 传会 400。所以 `config.TEXT_MODEL = "deepseek-flash"`，
> `config.TEXT_MODEL_DISPLAY = "DeepSeek-V4.1-Flash-1017"` 只用于界面与日志展示。

密钥可以直接写在 `config.py` 顶部：

```python
DEEPSEEK_API_KEY: str = "sk-你的DeepSeek密钥"
COMFY_API_KEY: str = ""      # 本地 ComfyUI 默认不鉴权，留空即可
```

留空时会自动回退到同名**系统环境变量**。

> ⚠️ `setx` 只把变量写进注册表，**不会**影响已经运行的进程。
> 用环境变量方式时必须**关闭并重新打开终端**再启动程序，否则读不到。

### 2. 常用可调项

| 配置项 | 说明 |
| --- | --- |
| `TEXT_API_HOST` / `TEXT_MODEL` | 文本服务地址与模型 id（默认 `https://api.deepseek.com` / `deepseek-flash`） |
| `TEXT_MAX_TOKENS` | 单次输出上限，默认 49152（含 reasoning token） |
| `TEXT_DISABLE_THINKING` | 默认 `True`：结构化任务关掉思考链，避免 max_tokens 被 reasoning 吃光、content 为空 |
| `COMFY_API_HOST` | ComfyUI 地址（默认 `http://127.0.0.1:8188`） |
| `COMFY_WORKFLOW_NAME` | 使用的工作流名称（默认 `image_z_image_turbo_int8`） |
| `COMFY_WORKFLOW_FILE` / `COMFY_WORKFLOW_DIR` | 显式指定工作流文件 / 本地备份目录（`comfy_workflows/`） |
| `COMFY_APPLY_SAMPLER_SETTINGS` | 是否用 config 覆盖工作流里的步数与 cfg（默认开；cfg=1.4 让负向词生效） |
| `COMFY_FILENAME_PREFIX` | 成图在 ComfyUI output 目录下的文件名前缀 |
| `WEBTOON_MODE` | **条漫模式开关**：开启＝竖幅分格 + 条漫长图；关闭＝回到"一格一张方图" |
| `RENDER_MODE` | **分镜形态**：`webtoon` 原条漫 / `page` 分镜页 / `hybrid` 混排（默认） |
| `PAGE_CANVAS_WIDTH` / `PAGE_DEFAULT_RATIO` | 分格页画布宽度（1536）与默认页宽高比（0.72） |
| `PAGE_MIN_PANELS` / `PAGE_MAX_PANELS` | 每页格数范围（默认 1~6） |
| `PAGE_PANEL_MIN_SIDE` / `PAGE_PANEL_MAX_SIDE` | 单格出图的最小/最大边长（512 / 1280） |
| `PAGE_LAYOUT_REPEAT_PENALTY` | 连续复用同一版式的惩罚（越大越多样，0=不惩罚） |
| `PAGE_CAPTION_ENABLED` / `PAGE_CAPTION_RATIO` | 上图下文版式的字幕栏开关与高度比例 |
| `PAGE_COMPOSE_ON_GENERATE` | 出图后是否自动合成页面（关掉则只在导出/手动时拼版） |
| `HANDRAW_ENABLED` | 是否启用 handraw-style 风格库（关掉回 47 个内置预设，分 8 个家族） |
| `HANDRAW_STYLE_AUTO` / `HANDRAW_STYLE_REC_LLM` | 分析后自动推荐 / 让文本模型参与最终挑选 |
| `HANDRAW_STYLE_SHORTLIST` | 送给模型挑选的候选画风数量（默认 28） |
| `HANDRAW_STYLE_REF_ENABLED` / `HANDRAW_MAX_REFERENCE_SLOTS` | 是否启用画风参考（色彩迁移）/ 参考图上限（3） |
| `HANDRAW_DEFAULT_STYLE` / `HANDRAW_DEFAULT_COLORS` | 默认风格（空=自动）/ 默认主题色（如 `"C-01 + C-12"`） |
| `HANDRAW_PROMPT_INCLUDE_NAME` / `HANDRAW_PROMPT_INCLUDE_AUTHOR` | 是否把风格名/作者名写进图像提示词（默认关，避免被画成标题/签名文字） |
| `HANDRAW_PROMPT_TRIGGER` | 是否注入「漫画分级特征」块：风格家族媒介 + traits 里真实出现的漫画特征词 + 阶调（默认开） |
| `HANDRAW_TONE_ENABLED` / `HANDRAW_TONE_DEFAULT` | 是否按故事气质自动选阶调 / 强制锁定某个 T-01…T-40 |
| `HANDRAW_LAYOUT_LLM` | 是否让文本模型参与分页 + 选版式（关掉只走本地动态规划） |
| `STUDIO_PROMPT_REWRITE` | 创作工坊是否先用文本模型做语义编译（默认开；关掉可做 A/B） |
| `STUDIO_REWRITE_TEMPERATURE` / `STUDIO_REWRITE_MAX_TOKENS` | 改写温度（0.3）/ 输出上限（1200） |
| `COMFY_WORKFLOW_PRESET` | `auto`（有画风参考时用 style_color）/ `base` / `style_color` / `style_ref`（实验）/ `style_img2img` |
| `COMFY_WORKFLOW_STYLE_NAME` / `..._STYLE_REF_NAME` / `..._STYLE_IMG2IMG_NAME` | 三份复杂风格工作流的名称 |
| `COMFY_COLOR_TRANSFER_STRENGTH` | 色彩迁移强度（0~1，默认 0.55） |
| `COMFY_COLOR_TRANSFER_THEME_STRENGTH` | 用户显式选主题色时的迁移强度（默认 0，避免颜色被画风参考盖掉） |
| `COMFY_CFG` / `COMFY_NEGATIVE_ENABLED` | 负面条件 cfg（默认 1.4；1.0=负向词失效）/ 负向词开关 |
| `CHARACTER_POSE_SIZE` / `CHARACTER_POSE_WORKFLOW_NAME` | 姿态总图 1536² / Pose ControlNet 工作流名 |
| `CHARACTER_MAIN_FROM_TRIPLE` | 旧开关，已关闭；主视图改为从姿态总图自动识别正面裁出 |
| `CHARACTER_SINGLE_OUTERWEAR` / `CHARACTER_SAME_SEED` | 只保留一件外层服装 / 主视图与姿态图共用种子 |
| `COMFY_MAX_REFERENCE_SLOTS` | 工作流里最多接几张参考图（按 LoadImage 数量再截断） |
| `WEBTOON_PANEL_WIDTH` / `WEBTOON_PANEL_SIZE_MAP` | 条漫统一宽度，以及按镜头类型分档的画幅 |
| `PANEL_SCALE_FACTOR` | S/M/L/XL 画幅档位的放大倍率 |
| `IMAGE_DRAW_BUBBLES` | **出图时让模型画空白气泡**（默认开）：提示词逐条给出坐标/尺寸/形状/尾巴；关＝备用模式（只预留背景，程序画气泡） |
| `IMAGE_FILL_MODEL_BUBBLES` | 出图后找回模型画的气泡、只填字（默认开，正式链路） |
| `CLEANUP_EXTRA_BALLOONS` / `STRAY_BALLOON_COVER` | 模型多画的气泡按形状补成背景，避免空对话框 |
| `BUBBLE_MATCH_*` / `BUBBLE_ALLOW_PROGRAM_FALLBACK` | 气泡匹配阈值与"一个都没找到"时的兜底开关 |
| `BUBBLE_RESERVE_TOP_RATIO` / `BUBBLE_RESERVE_MAX_RATIO` | 提示词里预留的干净背景带（默认 4.5%~42%） |
| `BUBBLE_RESERVE_PAGE_ENABLED` | 分格页/程序气泡是否还要求模型留白（默认关；开了容易把半格刷成空地） |
| `AUTO_ASSIGN_PROTAGONIST_TO_EMPTY_SHOTS` | 空镜自动补第一主角（屏幕/手机等物件特写不补） |
| `WEBTOON_PANEL_MIN_HEIGHT` | 单格最小高度（默认 1152），保证全部竖幅、不出白边 |
| `PANEL_BORDER_ENABLED` / `PANEL_GUTTER` | 墨线分格边框与纸色 gutter（默认 3px / 28px） |
| `EPISODE_COVER_ENABLED` / `EPISODE_END_CARD_ENABLED` | 话封面、尾页开关 |
| `NARRATION_STYLE` | `caption`＝快看式旁白条（默认）；`plain`＝白字投影 |
| `DIRECTOR_ENABLED` / `DIRECTOR_LLM_ENABLED` | 导演层与"导演重写"开关 |
| `IMAGE_LORA_ENABLED` | 旧角色 LoRA 开关（默认关；LoRA 已归档，不再使用） |
| `DIRECTOR_MAX_TWO_SHOT_RATIO` | 双人同框上限（默认 25%，超出拆成单人斜侧 + 画外音） |
| `DIRECTOR_CLOSEUP_MAX_RATIO` | 近景+特写上限（默认 30%，超出近景降中景） |
| `STORYBOARD_SHOTS_PER_1K_CHARS` / `STORYBOARD_MAX_SHOTS` | 总格数 16 格/千字，夹在 30~120 |
| `AUTO_PIPELINE_ENABLED` / `AUTO_EXPORT_AFTER_GENERATE` | 一键全流程开关；跑完自动导出 |
| `IMAGE_AUTO_REPAIR` / `IMAGE_AUTO_REPAIR_RETRIES` | 出图自检 + 最多返修次数 |
| `IMAGE_QUALITY_PASS_SCORE` / `IMAGE_QUALITY_ACCEPT_SCORE` | 自检通过分（82）/ 可交付底线（70，低于则标记失败） |
| `IMAGE_STYLE_ANCHOR` / `IMAGE_QUALITY_SUFFIX` | 统一画质锚点与收尾约束（**只用正面描述**，见下节） |
| `RULES_DIR` | 规则库目录（默认 `rules/`，程序运行时读取） |
| `PROMPT_MAX_CHARS` | 单张分镜正向提示词上限（默认 1000 字，超了按整段裁剪，不切 handraw 风格块） |
| `PROMPT_REWRITER` | `off` 旧拼接 / `template` 规则翻译层（默认）/ `llm` 用文本模型只重写首句框架 |
| `CHARACTER_DISTINCTNESS_ENABLED` | 是否强制角色跨角色差异化（撞车检测 + 唯一点缀） |
| `IMAGE_SIZE` | **非条漫模式**下的分镜方图分辨率，显存不足时调小 |
| `IMAGE_STEPS` / `IMAGE_GUIDANCE` | 旧采样参数；当前 cfg 用 `COMFY_CFG`（默认 1.4） |
| `MAX_REFERENCE_IMAGES` | 参考图收集上限；实际送几张由工作流的 LoadImage 槽位与 `COMFY_MAX_REFERENCE_SLOTS` 决定 |
| `COMFY_NODE_OVERRIDES` | 节点定位覆盖（一般不用填，程序会自动找提示词/画幅/采样器/保存节点） |
| `IMAGE_NEGATIVE_PROMPT` | 负面词基础表；程序会按镜头/角色/姿态总图/风格再组合，走 `KSampler.negative` |
| `STYLE_PRESETS` / `STYLE_PROMPT_MAP` | 47 个内置画风预设，按 8 个家族分组：电影与综合 / 日系 / 国风与华语 / 欧美 / 欧洲 / 独立与实验 / 美术媒介 / 韩漫与条漫（韩漫只是其中一组；清单见 [docs/画风预设清单.md](docs/画风预设清单.md)） |
| `CHARACTER_IMAGE_SIZE` / `CHARACTER_POSE_SIZE` / `CHARACTER_TOP_SIZE` | 主视图 1024×1536、姿态总图 1536×1536、俯视参考 1024×1024（俯视只接受上传真实参考） |
| `BUBBLE_*` / `NARRATION_*` / `SFX_*` | 气泡、旁白条、拟声词的字体/字号/配色/间距 |
| `OUTPUT_NAME_TEMPLATE` | 输出文件夹命名模板（默认 `漫画_日期时间`） |
| `WEB_PORT` | 界面端口（默认 7870） |

所有配置项都支持用环境变量覆盖（前缀 `COMIC_`），例如
`set COMIC_WEB_PORT=7871`（元组/字典类型请直接改 config.py）。

### 3. 提示词写法（Z-Image-Turbo 的"方言"）

当前默认工作流是 **8 步蒸馏 + cfg=1.4**：`negative` 已接独立的 `CLIPTextEncode`
（标题「负面提示词」），不再用 `ConditioningZeroOut`。这带来两条分工：

- **正向**只写"要什么"（画面/角色/动作/光/风格块），长度 ≤900 字；
- **负向**写"不要什么"（低质/多格/文字/多余人/透明衣料…），走 `KSampler.negative`，
  按镜头/角色/姿态总图/风格组合，只在 `COMFY_CFG>1` 时生效。

| 规矩 | 原因（实测） |
| --- | --- |
| **正向描述只写"要什么"**，把"不要X"放到独立的 negative 节点 | 正向里写"不要文字/不要边框"只会把"文字""边框"喂进模型；负向词则走 `KSampler.negative`，见 §6.3 |
| **不需要气泡的格子一个字都不提气泡** | 只要正向里出现"气泡"二字，没有台词的格子也会多画一个空白气泡 |
| **正向里不要写"分镜 / 一格 / 漫画格"** | 模型会把它理解成"画一张带边框的漫画页"，于是自动加白边黑框，甚至切成两格 |

配套的代码层处理：无角色镜头换用 `SHOT_TYPE_PROMPT_MAP_OBJECT`（避免
"特写=角色面部占据大部分"把空镜变成大脸）；无参考图/基础文生图时正向提示词里
不再出现"第 N 张参考图是某某"。

负向条件已单独实现：四个工作流都有 `CLIPTextEncode`（标题「负面提示词」）接到
`KSampler.negative`，`cfg=1.4` 时开始生效；关闭开关是 `COMFY_NEGATIVE_ENABLED=False`，
只想要官方默认画质可把 `COMFY_CFG` 调回 1.0（此时负向词不生效）。详见
[需求说明书 §6.3](Z-Image-Turbo%20小说漫画生成系统%20—%20需求说明书.md#63-负面条件与-cfg-的取舍)。

---

### 负面条件怎么用

1. 工作流已内置 `CLIPTextEncode`（标题「负面提示词」）→ `KSampler.negative`；
2. 默认 `COMFY_CFG=1.4`，负向词开始生效；
3. 负向词由 `prompts.negative_prompt()` 自动按镜头/角色/姿态总图/背面/风格组合，
   不需要手写；
4. 想关：`COMFY_NEGATIVE_ENABLED=False`；想回官方默认画质：`COMFY_CFG=1.0`
   （此时负向词不生效）；太糊/过曝：先试 1.2；
5. 用「设置 → 检查工作流」确认工作流有 `负面提示词` 节点；如果 ComfyUI 里的同名
   工作流是旧版，程序会优先读 ComfyUI 那份，负向词不会生效。

### 提示词库与回溯（prompts/）

所有阶段的提示词都在 [prompts/](prompts/) 按阶段分目录保存，由
`export_prompts.py` 从代码**实际渲染**生成（不是手抄，改代码后重跑即可）：

| 目录 | 内容 |
| --- | --- |
| `prompts/01_analysis/` | 小说分析、分段合并 |
| `prompts/02_characters/` | 人设生成、撞车重写 |
| `prompts/03_storyboard/` | 分镜拆解、合并、对话拆格、单格补全 |
| `prompts/04_director/` | 导演重拍、单格修镜头 |
| `prompts/05_narration/` | 旁白补全 |
| `prompts/06_image/` | 分镜出图提示词（含气泡坐标） |
| `prompts/07_bubble/` | 气泡绘制指令、检测与回填细则 |
| `prompts/08_repair/` | 自检与返修措辞 |
| `prompts/09_style/` | 绘制风格推荐（365 画风 + 36 主题色） |
| `prompts/10_layout/` | 分页与版式规划（SB-*/WB-*） |

**运行期真实提示词**由 `comicgen/prompt_trace.py` 落盘到：

```
data/projects/<工程id>/prompts/
├── index.md
├── 01_analysis/…        当时发给文本模型的分析提示词
├── 02_characters/…      人设/撞车重写
├── 03_storyboard/…      分镜拆解/合并
├── 04_director/…        导演重拍
├── 05_narration/…       旁白补全
├── 06_image/…           每格出图提示词（含本轮返修要求）
├── 07_bubble/…          每格气泡计划、检测结果、回填布局
├── 09_style/…           风格推荐（候选清单、模型选择、理由）
└── 09_layout/…          分页与版式规划（AI 方案或本地动态规划）
```

重新生成文档：

```powershell
env\Scripts\python.exe export_prompts.py
```

排查一张图时，按 `06_image → 07_bubble → 02_characters` 倒查即可知道
"当时用了哪版人设、气泡坐标、检测到几个、有没有兜底"。

## 三、五个阶段

| 阶段 | 做什么 | 关键点 |
| --- | --- | --- |
| ① 输入文本 | 上传 txt/md 或粘贴文本，可加载示例 | 自动识别编码（UTF-8/GBK/UTF-16…） |
| ② 小说精修与连续故事板 | DeepSeek 先做情节减法（核心戏剧任务 + 8–12 视觉锚点），再扩展 25 格连续故事板 | 只产出文本，不画图；落盘 `core_task / visual_anchors / continuous_cells` |
| ③ 内容分析 | 识别角色、场景、情节，并按故事气质推荐 handraw 画风 + 主题色 | 表格可直接编辑，支持增删角色/场景；风格可手选/锁定 |
| ④ 角色预设与参考资产 | 主视图、面部特写、侧面/背面单视图、三视图、俯视图 | 主视图 + 面部特写锁身份；单视图锁角度；三视图/俯视图只做人工查看与结构补充 |
| ⑤ 分镜拆解 | 把小说拆成镜头脚本，并补每个角色动作 `character_actions` | 整章一次拆；导演层补机位/构图/光线/画幅/气泡槽位 |
| ⑥ 25格分镜 | 把 25 个关键镜头生成 5×5 场景分镜结果 | 与最终出图同一套参考路由/提示词；Qwen T2I 不挂自动姿态；落盘 `25宫格_分镜结果.png` |
| ⑦ 姿态图（可选） | 逐关键镜头生成/上传姿态骨架 | Qwen T2I 自动姿态停用；需要动作锁定时上传自定义姿态；Z-Image 仍可用骨架 |
| ⑧ 出图与打包 | 逐格生成画面（按格子比例）→ 气泡 → 拼页 → 条漫/PDF 导出 | 参考图按景别路由；可暂停/继续/取消、单张重做、跳过、换版式、合成页面、只重新拼条 |

### 工作流工作台（侧栏最上方）

侧栏「工作流」在第一步之上，把 ComfyUI 节点图直接放进项目：

- **默认工作流**：来自 `comfy_workflows/*.json`，不可删除；可复制、编辑、恢复默认版本；
- **自定义工作流**：复制现有流程、从 ComfyUI 导入、粘贴/选择 JSON 文件，保存在
  `data/workflows/`，可以删除；
- **节点图**：完整显示节点、参数和连线；拖动节点、拖动端口连线、点击连线删除、
  滚轮缩放、空白处平移；可从 ComfyUI 节点库添加节点（`＋ 节点`）；
- **应用**：勾选「角色参考图 / 分镜出图 / 姿态图」后保存，下一次对应步骤出图时
  使用该工作流；应用时会校验提示词、采样器、画幅、保存节点是否完整；
- **说明文档**：每个工作流都有一份 Markdown 说明（用途、适用步骤、关键节点、
  模型清单），在界面右侧直接编辑保存；默认说明会自动生成到
  `data/workflows/docs/`。

### 一键全流程（默认推荐）

阶段 1 的「**一键全流程 →**」会按顺序自动执行：

1. **内容分析**：识别角色/场景/情节；已有结果则复用；分析完自动推荐 handraw 画风 + 主题色；
2. **主视图与姿态图**：只补缺失的图，已有角色不重画；
3. **分镜拆解 + 导演层整理 + 分页选版式**：已有分镜则复用并重新整理；新拆的分镜会先自检，
   如果仍偏“小说体”/画外音混入画面，会自动让导演模型重拍一次再检查；
   随后按内容把格子编成页、逐页选 SB-*/WB-* 版式；
4. **逐格出图**：每格按所属版式的格子比例出图 → 程序按说话人位置画气泡并填字 →
   每格自检 → 不合格自动返修 → 出完的页按版式合成；
5. **导出**：条漫长图（页图/单格）、PDF、ZIP 一次生成。

它是**可恢复**的：每个阶段结束都会落盘。用户可以在任意阶段停下来改角色、改分镜、
改画幅或改气泡，再点一次「一键全流程」继续；已完成的阶段和已出图的格子会自动跳过。

### 绘制风格：嵌入 handraw-style（365 种画风 × 36 种主题色）

项目内嵌了 [handraw-style](handraw-style/) 这套编号化画风资产库，并**只读**使用它的
`styles.json` / `colors.json` / `layouts.json` 与参考图，不改动它内部任何文件。

- **画风**：365 种（`FA-001 … FI-034`，旧编号 `001–365` 会自动映射），每种带
  `generation_name`、参考作者/风格名与**正面视觉特征**（线条、笔触、媒介、材质、
  色彩倾向、光影），我们直接把这些特征写进图像提示词。
- **主题色**：36 种（`C-01–C-36`），支持主色 + 点缀色两色搭配。
- **阶调（tone）**：40 种（handraw 原始 `T-01–T-16` + 本地扩展 `T-17–T-40`：高调/低调、硬调/软调、雨夜/月夜/霓虹/煤油灯、逆光/侧逆光/顶光/底光、复古褪色/粗颗粒…）。
  分析完成后按故事气质自动选，例如雨夜悬疑 → `T-15 暗沉调`，复古动画 → `T-14 胶片调`；
  可在工程里锁定某个阶调（`HANDRAW_TONE_DEFAULT`）。
- **漫画分级特征**：把风格家族的媒介词（电影级写实摄影 / 二维赛璐璐动画 / 日式漫画…）
  + 该风格 traits 里**真实出现**的漫画特征词（线稿、网点、平涂、赛璐璐、胶片噪点、
  低饱和、暗调…）+ 阶调，拼成一段「漫画分级特征」，放在提示词最前面。这些词全部来自
  handraw-style 的 group / traits / tones 资产，不是另一套自造标签。
- **触发词**：界面显示的 `触发词` 是"媒介；漫画特征；阶调"的组合，可在生成前手改；
  手改后**原样**作为「漫画分级特征」送进提示词，留空则按资产自动组合。
  实际提示词默认不写英文风格名/作者名（会被 Z-Image 画成标题/签名），
  想试英文激活可开 `HANDRAW_PROMPT_INCLUDE_NAME` / `HANDRAW_PROMPT_INCLUDE_AUTHOR`。
- **自动推荐**：阶段②分析完成后，先用本地关键词打分把 365 种收敛成 20~30 个候选，
  再让文本模型结合题材、时代、情绪、受众做最终判断；模型不可用时直接用本地结果。
  界面阶段②的「✨ 自动推荐」可以随时重跑，「浏览全部风格」可以看缩略图手选。
- **手动锁定**：手动选过风格后 `style_auto=False`，重新分析/一键全流程不会覆盖；
  点「用回内置画风」即清空 handraw 风格，回到 47 个内置预设
  （日系 / 国风与华语 / 欧美 / 欧洲 / 独立与实验 / 美术媒介 / 韩漫，韩漫只是其中一组）。
- **全书统一**：角色主视图/面部特写/单视图、每一格分镜、每一页版式共用同一段风格块
  （漫画分级特征 + 视觉语言 + 主题色 + 阶调），避免"这一格水墨、下一格赛璐璐"。
- **参考图**：默认走 `style_color` 工作流——生成后用 `ColorTransfer` 把画风缩略图的
  **色彩倾向**迁到成图上（只迁色，不迁内容），所以不会把参考图里的人物/构图抄进来。
  提示词里会写明"第 1 张只取色调"。
- **提示词写法**：只取正面特征，自动过滤含「避免 / 不要 / 禁止」的分句，并去掉
  `#018`、`SC-021`、`C-01` 这类编号、《作品名》与**风格名/作者名**——Z-Image-Turbo
  会把 "…Cinema / …Illustration" 这种标题式名字当画面文字画上去（实测会在底部排出片名），
  所以给模型的是"漫画分级特征 + 视觉语言 + 主题色"，名字只留在界面里给人看。

> handraw-style © 2026 yang0，MIT License（附带署名要求），见
> [handraw-style/LICENSE](handraw-style/LICENSE)；本项目只读引用它的风格/版式资产，
> 不修改其内部文件。

对应配置项集中在 `config.py` 的「四-2、绘制风格库」：
```python
HANDRAW_ENABLED = True
HANDRAW_STYLE_AUTO = True          # 分析后自动推荐
HANDRAW_STYLE_REC_LLM = True       # 让文本模型参与最终挑选
HANDRAW_STYLE_SHORTLIST = 28       # 候选画风数量
HANDRAW_STYLE_REF_ENABLED = True   # 是否启用画风参考（色彩迁移）
HANDRAW_PROMPT_TRIGGER = True      # 注入"漫画分级特征 + 阶调"
HANDRAW_TONE_ENABLED = True        # 按故事气质自动选阶调
HANDRAW_TONE_DEFAULT = ""          # 如 "T-15" 锁定暗沉调
HANDRAW_DEFAULT_STYLE = ""         # 空=自动；也可填 FA-001 / 001
HANDRAW_DEFAULT_COLORS = ""        # 如 "C-01 + C-12"
```

### 触发词与阶调怎么用

1. 阶段②分析完成，点「✨ 自动推荐」或「浏览全部风格」选一个编号；
2. 风格卡会显示 `触发词 · 媒介 / 漫画特征 / 阶调`，例如
   `电影级写实摄影与视觉大片 · 低饱和、暗调 · 暗沉调`；
3. 想换阶调：在「阶调」下拉选 `T-01…T-40`（留空=按故事气质自动）；
   想改触发词：直接编辑输入框，点「保存阶调/触发词」；
4. 正常出图即可，系统会自动把「漫画分级特征 + 视觉语言 + 主题色」放在每一格提示词最前面；
5. 想核对实际提示词：看 `01_分析/绘制风格.md` 和 `04_提示词/逐格提示词.jsonl`；
6. 想整块关闭：`HANDRAW_PROMPT_TRIGGER=False`；锁死阶调：`HANDRAW_TONE_DEFAULT="T-15"`；
   想试英文风格名：`HANDRAW_PROMPT_INCLUDE_NAME=True`（有被画成标题文字的风险）。

界面效果见 [screenshots/11_风格触发词.png](screenshots/11_风格触发词.png)；
A/B 实测见 [outputs/trigger_test/README.md](outputs/trigger_test/README.md)。

### 分镜版式：从"一格一张"到"一页多格"

之前的成品是"一格一张图，首尾竖拼"。现在分镜分析会额外产出**叙事功能**，
再据此把连续格子编成页，并**自主选择 handraw-style 的漫画分镜版式**：

```python
RENDER_MODE = "hybrid"   # webtoon 原条漫 / page 分镜页 / hybrid 混排
```

| 形态 | 行为 | 适用 |
| --- | --- | --- |
| `webtoon` | 一格一张图，竖屏无缝长条（旧行为，完整保留） | 强情绪、强冲击、手机一路下滑 |
| `page` | 2~6 格拼成一页，版式从 SB-* 里选 | 对白密集、信息量大、日常叙事 |
| `hybrid` | 按内容自动分流：情绪/冲击格走条漫长格，信息密集段走分镜页 | 默认，最像真实商业漫画 |

**分镜内容分析多了四个字段**（阶段④由模型产出，导演层兜底）：

| 字段 | 取值 | 作用 |
| --- | --- | --- |
| `beat` | 建立/对白/反应/动作/情绪/转场/揭示/蒙太奇/日常/幽默/悬疑/细节 | 这一格的叙事功能，版式匹配的主信号 |
| `pacing` | 慢/中/快 | 页面节奏，避免连续同节奏 |
| `panel_emphasis` | normal / main / splash | 是否该落在大格、满版页上 |
| `caption` | ≤40 字 | "上图下文"版式（SB-069/070/071）的字幕栏文案 |

**版式选择**（`comicgen/pagelayout.py`）：

1. 先用内容信号给每个候选版式打分：对白密度 / 动作冲击 / 情绪停顿 / 建立场景 /
   时间空间跳接 / 悬疑张力 / 喜剧节奏，7 个维度与该版式的 `fit` 做加权；
2. 再加约束：一页最多跨 2 个场景；重点格要落在大格；单格台词过多不能塞进小格；
   单格满版页只留给 hook / splash 格；同一版式连续复用会被扣分；
3. 用**全局动态规划**在"每页格数 × 版式"上求最优覆盖，保证每格都被编进某一页；
4. 文本模型在线时，先让它给一版「页 + 版式 + 字幕」方案，程序严格校验
   （顺序、无遗漏、格数匹配），不合法就整体退回本地规划；离线/无文本模型时走本地规则。

**绘制方式**：每一格仍然是**独立生成的单幅画面**（气泡、自检、自动返修都按格走），
再按版式几何贴进页面画布（等比裁切填格、墨线边框、纸色 gutter、字幕栏、页码）。
好处是"重画某一格"不会牵连整页，也不依赖模型一次画对 4~6 个格子；
坏处是跨格连续人物/背景需要靠同一套风格块与身份卡维持——这已经在提示词层做掉了。

**版式库**：

- `SB-001–SB-071`：handraw-style 的漫画分镜版式（规则网格、大格+小格、正反打、
  对角切割、碎片面板、由远到近、闪回嵌套、跨页、满版冲击、上图下文…）；
- `WB-001–WB-003`：本项目原生的条漫版式（单格 / 双格 / 三格竖排、无页边距），
  保证"原来的条漫类型"仍然是一等公民。
- 机器可读定义在 [rules/data/storyboard_layouts.json](rules/data/storyboard_layouts.json)：
  每个版式登记格数、几何模板、适配度（fit）、强项标签；几何模板用归一化矩形/多边形，
  可热改，不用动代码。

阶段④顶部新增「**分镜形态与版式**」卡片：切换形态、重新规划版式、合成页面；
每页显示版式缩略图、包含哪几格、选择理由，并可在同格数的版式之间手动更换。
阶段⑤的「分格页」卡片显示已拼好的页图，点开可看大图。

### 出图自检 + 自动返修

每一格生成后，`comicgen/quality.py` 会做结构化自检：

| 检查 | 处理 |
| --- | --- |
| 四边白边 | 先自动裁掉；仍有则返修，要求满幅 |
| 文字溢出气泡 | 自动缩小字号；最小字号仍溢出则返修，要求气泡画大 |
| 气泡定位失败 | 程序兜底 + 返修“按坐标画 N 个气泡”（扣 14 分） |
| 气泡数量不符 | 多画的补成背景 + 返修“画面里只有 N 个”（扣 6 分） |
| 气泡兜底 | 模型没画到位、程序补画，记瑕疵并返修 |
| 全黑/全白/过暗 | 返修，要求主体与背景清晰 |
| 有台词没气泡 | 返修，要求补画空白气泡 |
| 有角色没送参考图 | 返修，要求按参考图锁人物 |

默认阈值与重试次数（`config.py`）：

```python
IMAGE_AUTO_REPAIR = True          # 开自检 + 自动返修
IMAGE_AUTO_REPAIR_RETRIES = 2     # 不合格最多再画 2 次
IMAGE_QUALITY_PASS_SCORE = 82     # 80 分以上通过
```

每次返修都会把具体问题写进下一轮提示词（例如“画面上方保留更完整的浅色背景空间”，旧模式则是“气泡画大一号”），
并保留**分数最高的一版**，不会越修越差。界面上每格会显示「自检 92」这样的分数。

> 自检是**结构化检查**，不是视觉大模型：它能自动处理白边、溢出、杂散气泡、全黑这类硬伤；但“脸画错了/手崩了/UI 字乱码”仍建议用「单格重做」人工挑一版。
> 这不影响全流程自动跑完，只是最后抽查时更省事。

### 用户不改设定时的默认质量

默认配置已经包含上一版靠人工补的几处：

```python
IMAGE_DRAW_BUBBLES = True                 # 出图时让模型按坐标画气泡
IMAGE_FILL_MODEL_BUBBLES = True           # 出图后找回气泡、只填字
STRAY_BALLOON_COVER = True                # 多画的气泡按形状补成背景
AUTO_ASSIGN_PROTAGONIST_TO_EMPTY_SHOTS = True  # 空镜自动补主角（物件特写不补）
WEBTOON_PANEL_MIN_HEIGHT = 1152           # 全部竖幅，避免白边/横构图
BUBBLE_TEXT_SPLIT_THRESHOLD = 32          # 30 字左右的长台词不硬拆成两个气泡
```

也就是说：用户完全不改设定，直接点「一键全流程」，得到的就是当前这一版的结构与
排版质量；用户想改角色/分镜/画幅时，原有编辑入口全部保留。

### 导演层：从"原文搬运"到"可拍的镜头"

旧版最大的问题不是画风，而是**分镜不成立**：画面描述像小说、打电话时把电话
另一端也画进同一间屋子、全篇同一个景别/机位、气泡事后乱贴。

阶段 4 现在有两层：

1. **拆解时**：DeepSeek-V4.1-Flash 一次读完整章（≤2.4 万字），按快看条漫规则
   输出 45~80 格镜头脚本。每格自带：
   `shot_type`（景别）、`camera`（机位）、`composition`（构图）、`lighting`（光线）、
   `panel_scale`（S/M/L/XL 画幅）、`transition`（转场）、`focus`（视觉焦点）、
   `action`（动作瞬间）、`expression`（表情）、`char_sides`（左右站位）、
   `silent`（无台词格）、`hook`（章末钩子），以及带 `slot`/`offscreen` 的台词。
   `characters` 只填**画面里看得见**的人；电话另一端用 `offscreen=true`。
2. **整理时**：`comicgen/director.py` 做确定性兜底——清理"小说体"描述、补镜头字段、
   强制建立镜头/打断同景别同机位、保证画幅至少 3 种、锁 180° 轴线、分配气泡槽位、
   补封面文案，并输出可读的自检报告。

界面阶段 4 有两个按钮：

- 「**整理分镜**」：台词归位、按对话拆格、镜头节奏/情绪/场景/旁白，确定性执行；
- 「**导演优化**」：让 DeepSeek 把已有分镜**重拍**一遍（补机位/构图/光线/画幅、
  剔掉画外音误入画面的人物）。已经出过图的工程会**作废旧成品图**并重置为待生成，
  需要到阶段 5 重新出图——这样才能让旧工程真正升级到新分镜。

### 气泡：出图时画进构图，出图后精确定位再填字

气泡不是后期贴图，而是**画面构图的一部分**：

> 例外：使用 `style_color`（色彩迁移）工作流时，气泡会改为**程序绘制**——
> 色彩迁移会把模型画的白色气泡一起染色，导致后续检测/填字失效。
> 此时提示词只留干净背景，气泡位置由 `bubble.plan_layout` 计算。

1. **出图前**：`bubble.plan_layout()` 按台词长度、说话人站位、旁白高度算出每个
   气泡的 `x/y/w/h`（画面百分比）与槽位；`prompts._bubble_block()` 把每个气泡写成
   "气泡 1：位于左上角，外接框左边缘约 2%、上边缘约 6%、宽约 48%、高约 13%，
   圆角矩形，内部纯白无字，尾巴指向画面左侧的某人"。
   同时写死"只有这 N 个、互不重叠、不贴边、纯白底黑描边"。
2. **出图时**：图像模型按这些坐标把空白气泡画进画面。
3. **出图后**：`bubble.detect_blank_bubbles()` 在画面里**按形状+坐标**找回气泡：
   - 候选：近白、实心、带暗描边、不贴边（允许贴顶）、膨胀后包含细尾巴；
   - 匹配：中心距离 + 面积比 + 左右侧别 + IoU 做**一对一**匹配；
   - 填字：只用气泡的**白色主体内接框**，不按计划框硬填，文字不会跑到尾巴/背景上。
4. **数量不符**：
   - 多画 → `bubble_count_mismatch`，多出来的按形状补成背景；
   - 少画/没画 → `bubble_fallback` / `bubble_detect_fail`，程序在空位补画，
     并把"画面里只有 N 个气泡"写进下一轮返修。
5. **画外音/电话**：不再画气泡，转成顶部**旁白条**（`（老港）…`），
   避免模型给气泡画尾巴、却指向画面里的人。

关键改动：

- 气泡提示词从"一句话"升级为**逐气泡坐标表**；
- 检测从"白色连通域 + 最近距离"升级为**形状感知 + 一对一综合匹配**；
- 每个工程的气泡计划、检测结果（`expected/detected/extra/fallback`）都会落盘到
  `data/projects/<id>/prompts/07_bubble/`，可回溯"这一格为什么填错/没填"。

> 备用模式：`IMAGE_DRAW_BUBBLES=False` 时提示词只预留干净背景、气泡全部由程序画，
> 用于模型完全不听话的极端情况。

**真机验证**（ComfyUI 已启动）：

```powershell
# 复制一个工程、只保留指定分镜，真实出图并打印气泡检测结果
env\Scripts\python.exe verify_bubbles.py 931dec78d0 48 49 3
```

2026-10-21 实测 3 格（含"贴左边缘"与"白色背景"两种此前必失败的场景）：
`计划 1 / 定位 1 / 多画 0 / 兜底 0`，文字全部落在模型画的气泡里。

这一版同时收紧了其它几处：

- **空镜自动补主角**：`AUTO_ASSIGN_PROTAGONIST_TO_EMPTY_SHOTS=True` 时，没有列角色的
  空房间/环境镜头会自动补第一主角；**但屏幕/手机/桌面这类物件特写不会补**
  （否则会变成手机屏幕上的脸）。
- **画幅全部竖幅**：`WEBTOON_PANEL_MIN_HEIGHT=1152`，近景/特写不再出现接近方形的
  画幅，减少模型加白边或画成横构图。
- **气泡尾巴朝向说话人**：按"站位 + 景别"估算脸部锚点，不再按槽位写死方向。

### 分格、封面、尾页：像一话漫画

导出时不再把图片首尾相接地贴成一条，而是：

```
封面（首格虚化底 + 标题 + 话数 + 钩子）
→ 话标题条（EPISODE 01 / 标题 / 第几话）
→ 每一格：墨线 3px 边框 + 纸色 28px gutter
→ 尾页（本话完 + 下话预告 + 追更钩子）
```

条漫总高超过 `WEBTOON_STRIP_MAX_HEIGHT` 会自动切成 `条漫_01.png`、`条漫_02.png`…
PDF 按每个分块一页排版，页面尺寸取图片自身尺寸，**不会再把不同高度的格子拉伸变形**。

### 分镜质量自检

阶段 4 的「分镜自检」面板会列出：

| 检查 | 说明 |
| --- | --- |
| 台词还在描述里 | 描述带引号/台词，会被图像模型画进画面 |
| 描述不合格 | 过短/过长/像小说段落，出图会失控 |
| 景别/机位连续 | 连续 3 格同景别或同机位，画面像翻 PPT |
| 新场景无建立镜头 | 读者不知道"这是哪" |
| 近景占比低 | 情绪没有落点 |
| 画幅节奏平 | 全篇只有一种 panel_scale |
| 没有钩子 | 章末没有悬念格 |
| 相邻描述雷同 | 两格画的是同一张图 |
| 画外音混入画面 | 电话另一端被填进 characters |
| 台词超载 | 单格 >3 条或单条 >20 字 |

清零后再出图，成品会稳定很多。

> 完整的条漫叙事规则（含平台官方画布规范、转场配比、镜头节奏、24 条自动校验条件
> 与出处）整理在 [条漫叙事与分镜规则.md](条漫叙事与分镜规则.md)。

### 角色预设与分镜一致性（2026-11 改版）

角色一致性现在由「**预设资产 + 参考图路由 + 文本/人数/年龄关系 + 画幅/姿态**」五层保证，规则全部落在
[`角色分镜一致性资料/`](角色分镜一致性资料/)：

1. **角色预设资产**（阶段 3 自动生成）：
   主视图（单人正面 T2I）+ 面部特写（从主视图裁）+ `views/front|side|back` 单视图 +
   俯视图（有参考时生成，按人脸检测标 `top_down/high_angle`）。
   三视图的正面就是主视图，侧面/背面从主视图生成；姿态总图只做人工查看/姿态库，不进分镜。
2. **信息密度**：身份卡保留性别/年龄/体型/脸型/发型/眼睛/服装/记忆点；
   年龄缺失时从人设补“少年/中年偏老/中年”等年龄带；多人卡也保留年龄，避免“同一个少年每格年龄不同”。
3. **参考图路由**（Qwen 分镜）：
   特写/近景 `face + main`；中景 `main`；侧面 `main + side`；背面 `main + back`；
   正顶视 `main + top`（仅 `top_down`）；全景/远景默认 **0 张**；双人每人一张 `face`；
   3 人 0 张，4 人拆 `3+1` 或 `2+2`。
4. **多人同框**：双人写清左/右、中间留空、只有说话人张嘴；多人把“年龄/身高关系”写进画面内容
   （例如“我十二岁，只到掌柜胸口”），否则少年会被画成成年人。
5. **画幅与姿态**：参考图 pad 到分镜格比例，出图 `fit` 到目标尺寸；特写/近景不挂自动全身骨架，
   ControlNet + 参考图只在需要动作的镜头用。
6. **负向词边界**：Qwen `cfg=1.0` 下负向条件基本不生效，人数/换装/背景路人不靠负向词，
   靠正面人数锁、参考图路由、拆格和后期裁切。

详细方案：
[分镜参考路由与提示词规范](角色分镜一致性资料/03_分镜参考路由与提示词规范.md)、
[工程落地与验收](角色分镜一致性资料/04_工程落地与验收.md)、
[落地实现与测试记录](角色分镜一致性资料/06_落地实现与测试记录.md)。

### 规则库（rules/）

所有"怎么画、怎么写、怎么校验"的规则都集中在 [rules/](rules/) 目录：

| 文件 | 内容 |
| --- | --- |
| [rules/00_诊断报告.md](rules/00_诊断报告.md) | 这次问题的定位、真实产物证据、根因与修复清单 |
| [rules/01_角色一致性规则.md](rules/01_角色一致性规则.md) | 身份卡、辨识度、多人同框、参考图升级路线 |
| [rules/02_分镜与镜头规则.md](rules/02_分镜与镜头规则.md) | 可拍性描述公式、人数政策、台词归属、气泡规则 |
| [rules/03_提示词工程规则.md](rules/03_提示词工程规则.md) | 拼装顺序、长度预算、正/负向、模板、反例 |
| [rules/04_校验与返修规则.md](rules/04_校验与返修规则.md) | 出图前/后门禁、返修措辞、已知限制 |
| [rules/07_风格与分镜版式.md](rules/07_风格与分镜版式.md) | 画风/主题色选择、提示词写法、SB-*/WB-* 版式打分与合成、工作流匹配 |
| `rules/data/*.json` | **程序运行时直接读取**的规则（改这里即生效，不用改代码） |
| `rules/data/storyboard_layouts.json` | 74 个漫画版式的格数、几何模板、适配度（可热改） |

加载器是 `comicgen/rules.py`，自检命令：

```powershell
env\Scripts\python.exe -c "from comicgen import rules; print(rules.self_check())"
```

**已有工程**（人设是旧版生成的、已经出现撞车）可以跑一次整理，预览不写盘：

```powershell
# 预览会改什么（不联网、不调用图像模型）
env\Scripts\python.exe apply_rules.py 931dec78d0
# 确认后写回（自动备份 .bak-时间戳.json）
env\Scripts\python.exe apply_rules.py 931dec78d0 --apply
# 允许 DeepSeek 重写撞车人设，并作废旧成品图
env\Scripts\python.exe apply_rules.py 931dec78d0 --apply --rewrite --invalidate-images
```

诊断脚本（只读工程 JSON，不消耗额度）：

```powershell
env\Scripts\python.exe _diag.py 931dec78d0
env\Scripts\python.exe _prompt_probe.py 931dec78d0 1 48 49
```

---

## 四、输出结果

导出后（默认在项目下的 `outputs/漫画_日期时间/`）：

```
漫画_20260109_120000/
├── 00_输入/         小说原文.txt、工程信息.json
├── 01_分析/         分析结果.json、故事梗概、角色清单、场景清单、绘制风格
├── 02_角色/         角色设定.json、人设卡_*.md、主视图/姿态总图
├── 03_分镜/         分镜脚本.json/md/txt、台词表.csv、分镜统计、分页与版式.json/md
├── 04_提示词/       逐格提示词.jsonl、气泡计划.json、运行期/（每次调用的原文）
├── 05_分镜图/       带气泡成品；原图/ 为无气泡纯画面；页面/ 为分格页
├── 06_条漫/         封面.png、条漫_01.png…（页图或单格 + 墨线 gutter）
├── 07_阅读/         漫画阅读.pdf（每个条漫分块一页）
├── 08_回溯/         质量报告.json、问题格清单、风格/版式规划原样
└── 09_清单/         导出清单.json、工程数据.json、目录说明
```

> 条漫长图改完某几格后不必整包重导：阶段 5 的「只重新拼条漫长图」会按当前成品
> 重新走一遍"封面 → 话标题 → 分格 → 尾页"；有分格页时用页图当图块，
> 没拼好的页会自动跳过，绝不把半页混进长条。关闭 `WEBTOON_MODE` 时回到旧结构
> （`漫画阅读/漫画长条图.png`、一页一图的 PDF）。

同名文件夹会自动追加序号避免覆盖。界面「工程与产物」页也可以直接下载压缩包。

---

## 五、项目结构

```
manhua/
├── config.py              全部配置（模型地址、密钥、画风、版式、气泡、输出…）
├── handraw-style/         画风资产库（365 风格 / 36 主题色 / 167 版式，只读）
│   ├── images/individual/ 风格参考图（<组>/<编号>.webp）
│   └── skills/handdraw-style-prompter/references/  styles.json / colors.json / layouts.json
├── rules/                 规则库（角色一致性 / 分镜 / 提示词 / 校验）
│   ├── 00~08 *.md         人读的规则/诊断/物件格/风格与版式/参考工作流
│   └── data/*.json        机器读的规则（运行时可热改，含 storyboard_layouts.json）
├── prompts/               全流程提示词库（按阶段分目录，代码实际渲染生成）
│   ├── 01_analysis … 10_layout
│   └── README.md          索引 + 重新生成方式
├── server.py              Web 后端（FastAPI，REST + 后台任务）
├── web/                   前端（原生 HTML/CSS/JS，无需构建）
│   ├── index.html
│   ├── style.css
│   └── app.js
├── comicgen/              核心业务包
│   ├── models.py          数据模型（角色/场景/分镜/页/工程，含版式与气泡计划）
│   ├── rules.py           规则加载器（读取 rules/data/*.json）
│   ├── stylelib.py        handraw-style 适配层（风格/主题色/版式目录、推荐、提示词）
│   ├── pagelayout.py      分页 + 选版式（动态规划/AI 校验）+ 合成页图
│   ├── prompt_trace.py    提示词回溯（写 data/projects/<id>/prompts/）
│   ├── prompts.py         各阶段提示词（风格推荐 / 分镜导演 / 版式规划 / 气泡坐标）
│   ├── director.py        导演层：镜头字段兜底、节奏修正、轴线、气泡槽位、自检
│   ├── storyboard.py      旧版分镜整理（台词归位/节奏/情绪/场景/旁白/校验）
│   ├── textclient.py      DeepSeek 文本客户端（reasoning 模型 / JSON 模式）
│   ├── comfyworkflow.py   ComfyUI 工作流读取（界面格式→API、子图拍平、参考图注入）
│   ├── imageclient.py     ComfyUI 出图客户端（参考图上传、排队、轮询、降级、缓存）
│   ├── pipeline.py        五阶段流水线（含自动定风格、分页规划）
│   ├── bubble.py          气泡计划 / 空白气泡检测 / 旁白条 / 拟声词排版
│   ├── episode.py         封面、话标题条、尾页、墨线分格与条漫拼版
│   ├── imggen.py          分镜批量出图 + 页面合成
│   ├── exporter.py        导出打包（目录 / 页面 / PDF / 条漫 / ZIP）
│   ├── textutil.py        文本读取、编码识别、分段
│   └── logger.py          日志
├── comfy_workflows/       ComfyUI 工作流备份（基础 + 两份复杂风格工作流）
├── data/                  运行期中间结果（projects / shots / pages / cache …）
├── outputs/               成品输出目录
├── selftest.py            离线自检（不联网，桩客户端跑通全流程）
├── apply_rules.py         把 rules/ 应用到已有工程（预览/写回/备份/作废旧图）
├── export_prompts.py      把各阶段提示词渲染到 prompts/（文档化）
├── translate_styles.py    生成 365 个 handraw 风格的中文显示名缓存（data/cache/style_zh.json）
├── verify_bubbles.py      真机验证：复制工程、重出指定分镜、打印气泡检测结果
├── _diag.py               一致性/分镜/气泡诊断（只读）
├── _prompt_probe.py       打印某格真实出图提示词（只读）
├── apitest.py             真机联调（调用真实模型走完五阶段）
└── shots.py               界面截图脚本（需要 playwright）
```

---

## 六、自检与排查

```powershell
# 离线自检：不消耗模型额度，验证核心逻辑
env\Scripts\python.exe selftest.py

# 只查风格库/版式库（365 画风、36 主题色、74 版式、参考图是否可读）
env\Scripts\python.exe -c "from comicgen import stylelib; print(stylelib.self_check())"

# 真机联调：真实调用 DeepSeek + 本地 ComfyUI，跑通五阶段
env\Scripts\python.exe apitest.py
```

### 常见问题

**1. 提示"未配置 DeepSeek 密钥"**
在 `config.py` 顶部的 `DEEPSEEK_API_KEY` 填入密钥；
或用环境变量方式设置后**重启终端**再启动程序。

**2. 提示"无法连接 ComfyUI http://127.0.0.1:8188"**
ComfyUI 没启动，或者端口不是 8188。先确认浏览器能打开
<http://127.0.0.1:8188>，再检查 `config.COMFY_API_HOST`。
界面上「设置 → 图像工作流 → 检查工作流」会一次性核对服务、工作流、节点类型和模型文件。

**3. 显存不足（CUDA out of memory）**
系统会自动降级重试（只压画幅，不改提示词）。条漫模式下为了保证所有格子同宽，
降级只压高度，**不会缩小宽度**。仍失败时手动调小 `WEBTOON_PANEL_SIZE_MAP` 里的高度，
或在 ComfyUI 里换成更小的量化模型。

**4. ComfyUI 拒绝执行工作流：节点 xxx Value not in list**
工作流里写的模型文件名在 ComfyUI 的 models 目录下找不到。
`unet_name` / `clip_name` / `vae_name` 三处名字必须和 `ComfyUI/models/` 里实际文件一致
（默认是 `z_image_turbo_int8_convrot.safetensors`、`qwen_3_4b_fp8_mixed.safetensors`、
`ae.safetensors`）。点「检查工作流」会直接告诉你缺哪个。

**5. 生成的图是多格漫画页**
本版已在提示词末尾强调"单幅、不分割画面"，并在正向词里写明"不要分格/不要拼贴"。
若仍出现，可再加强 `STYLE_PROMPT_MAP` 或 `IMAGE_STYLE_ANCHOR` 的措辞。

**6. 画面偏写实照片 / 3D 感，不像条漫**
负向条件已接 `KSampler.negative`，默认 `COMFY_CFG=1.4` 时生效；如果仍无效，
先确认 ComfyUI 里同名工作流有「负面提示词」节点，再确认 `COMFY_CFG>1`。
想加强/减弱：调 `COMFY_CFG`（1.2~1.5，过高会失真）；想回官方画质调到 1.0
（负向词失效）。不要再用 `ConditioningZeroOut`，也不要把负向词写回正向。

**7. 画面里有空气泡 / 两个气泡叠在一起**
默认 `IMAGE_DRAW_BUBBLES=True`：模型按提示词里的**逐气泡坐标**画空白气泡，
程序再按形状+坐标找回并填字。日志里会打印
`气泡：计划 N，定位 K，多画 E，兜底 F`：

- `定位 0` → 模型没按坐标画，会整格兜底并触发返修；
- `多画 >0` → 多出来的按形状补成背景，同时提示“画面里只有 N 个”；
- `兜底 >0` → 有气泡没定位到，程序补画，下一轮会返修。

每一格的 `plan / debug / layouts` 都记录在
`data/projects/<工程id>/prompts/07_bubble/`，可直接看是哪一步出错。

**7.1 气泡压住了脸**
程序画气泡时会用"细节密度 + 肤色网格"避开人物；分格页默认**不向模型要留白**
（避免半格被刷成空地）。若某一格仍压脸，可把 `BUBBLE_RESERVE_PAGE_ENABLED` 打开，
并调 `BUBBLE_RESERVE_TOP_RATIO` / `BUBBLE_RESERVE_MAX_RATIO` 控制留白带，
再重出这一格；或在界面里手动换气泡位置。

**8. 界面打不开**
确认 `server.py` 已在运行，且 `config.WEB_PORT`（默认 7870）未被占用。
日志窗口与 `data/logs/` 下有完整报错信息。

**9. 成品像"一堆无厘头图片"，剧情看不懂**
先在阶段 4 点「整理分镜」，再点「导演优化」，然后去掉「分镜自检」里的全部提示
（尤其是 `台词还在描述里`、`画外音混入画面`、`相邻描述雷同`）。
自检清零后重新出图即可——旧图不会被自动替换，需要重跑阶段 5。

**10. 文本模型报 400：model not found**
DeepSeek 官方接口只接受模型 id `deepseek-flash` / `deepseek-v4-pro`。
`DeepSeek-V4.1-Flash-1017` 是展示名，不能直接填进 `TEXT_MODEL`。
保持 `TEXT_MODEL="deepseek-flash"`，展示名放 `TEXT_MODEL_DISPLAY`。

**11. 单张图生成很慢 / 一直卡在排队**
Z-Image-Turbo int8 在 8GB 显存上：首次要加载 int8 模型 + 4B 文本编码器，
约 60~120 秒；之后单张 8 步约 20~40 秒，实测 1088×1216 稳定在 20 秒上下。
慢的时候先看 ComfyUI 自己的控制台——如果它显示模型被反复卸载/加载，
说明显存不够，把 `WEBTOON_PANEL_SIZE_MAP` 的高度整体调小 15%。
本程序日志里会打出"排队中，前面还有 N 个"，说明 ComfyUI 队列里还有别的任务。

**12. 上传的长篇小说"在框里只显示了一半"**
先确认是不是显示问题，不是丢内容：

- **字数是"字符"，文件大小是 UTF-8 字节**，中文 1 字≈3 字节。
  例如 `xiaoshuo.txt` 14.4 KB = 5143 字 / 181 行，接口返回的就是 5143 字，
  和文件逐字一致（可用「清空文本→再上传」复现，或对比首尾句子）。
- 文本框会**跟着内容自动长高**（上限 72vh），超出后才滚动；框下方会写
  `全文已载入：5143 字 / 181 行 · 已显示到 15%`，滚动时会实时更新百分比。
- 只有**分析**阶段会截断：超过 `MAX_NOVEL_LENGTH`（默认 20 万字）只取前 20 万字，
  文本本身仍完整保留在文本框和工程里（提示行会标黄提醒）。
- 如果上传后提示"检测到 N 个乱码字符"，说明编码识别可能不对，
  用记事本另存为 UTF-8 再传一次。

**13. 风格库显示不可用 / 风格下拉是空的**
确认 `handraw-style/` 目录在项目根目录下，且
`handraw-style/skills/handdraw-style-prompter/references/styles.json` 存在。
界面设置里看 `/api/style/list` 是否返回 `available: true`；
命令行可跑：

```powershell
env\Scripts\python.exe -c "from comicgen import stylelib; print(stylelib.self_check())"
```

风格库不可用时程序会自动回到 47 个内置预设（日系 / 国风与华语 / 欧美 / 欧洲 /
独立与实验 / 美术媒介 / 韩漫），不会中断生成。完整清单见 `docs/画风预设清单.md`。

**14. 画风参考图没有生效 / 提示"工作流没有图像输入"**
默认画风工作流是 `image_z_image_turbo_int8_style_color`：生成后把画风缩略图的
色彩迁到成图上（`ColorTransfer` 节点，ComfyUI 内置），只迁色不迁内容。
如果 ComfyUI 里没有这份工作流，程序会回退到基础文生图，提示词里的参考图说明
会被自动去掉（风格仍由"视觉语言 + 主题色"提示词生效）。
把
[comfy_workflows/image_z_image_turbo_int8_style_color.json](comfy_workflows/image_z_image_turbo_int8_style_color.json)
导入 ComfyUI（或直接放到 userdata/workflows）后，
「设置 → 检查工作流」应显示 `参考图：支持`。

> `image_z_image_turbo_int8_style_ref.json` 是**实验性**的 Z-Image Omni 参考图
> 工作流：用基础 Turbo 权重时它会连参考图的内容/构图一起抄过去（实测会把姿态总图的
> 三个小人排进画面），只在装了 Z-Image Omni 权重时才有意义。不要用 `auto` 之外的
> 方式强制它当默认。

**15. 分格页一直是"待生成"**
分格页要等**这一页的所有格子**都出完图才能拼。看阶段④每页卡片上的
"缺第 N 格成品图"，把缺的格子补完，再点「合成页面」。
如果刚改过分镜顺序/增删过格子，程序会自动重排版式；
也可以在阶段④点「重新规划版式」强制重排。

**16. 想彻底回到原来的条漫**
把 `RENDER_MODE` 改成 `"webtoon"`（或阶段④顶部切成"原条漫"）：
清空所有页面规划，一格一张图、竖屏无缝长条，和旧版完全一致。

---

## 七、说明
- 中间结果全部落盘在 `data/`，随时可以中断、重启后继续调整和重新生成。
- 生成失败的分镜会记录错误并跳过，不会中断整批任务。
- 同一提示词与参考图会命中缓存，避免重复出图浪费时间与显存。

## 八、本次更新记录（handraw-style 接入）

| 项 | 更新 |
| --- | --- |
| 风格资产 | 真正读取 365 画风 / 36 主题色 / 40 阶调 / 74 漫画版式，不再只当缩略图 |
| 分析联动 | 分析文本 → 本地候选收敛 → 文本模型终选风格；同时自动选阶调 |
| 触发词 | 新增「漫画分级特征」块：风格家族媒介 + traits 里真实出现的漫画特征词 + 阶调；存进 `style_tone` / `style_trigger` |
| 界面 | 风格卡显示触发词；新增阶调下拉与触发词输入，可保存后重画；风格名/参考作者全部中英双语（中文主显示，英文原名保留） |
| 风格中文名 | 365 个 handraw 风格新增中文显示名与作者中文名（缓存 `data/cache/style_zh.json`）；风格卡/风格库/下拉框统一“中文主 + 英文副”，缺失时用 traits 自动兜底 |
| 风格切换 | 修“应用风格又把阶调换回暗沉”：同风格应用保留当前阶调；切风格才自动重选；主题色不再被无条件清空 |
| 主题色生效 | 有显式主题色时 `ColorTransfer` 强度归零（`COMFY_COLOR_TRANSFER_THEME_STRENGTH`），中国红/克莱因蓝不再被画风参考图盖掉 |
| 清空重来 | 新增 `/api/images/clear` 与各阶段“清空图片”按钮：可清单格/整页/角色/全部图，小说、人设、分镜文本与页版式全部保留 |
| 重排一致 | 重新规划版式不再自动拿旧图合成；分格页/混排页按当前 `page.cells` 计算出图画幅，不再沿用旧 `shot.panel_size` |
| 实时刷新 | 批量/单格每完成一格，前端立即刷新当前页的分镜卡/页图，不等整批结束 |
| 风格优先 | 提示词第一段改为“视觉语言（本图必须执行）+ 画风执行”，不再用韩漫模板开头；FA 增加 8~9 头身/断线/刮擦等执行词与对应负向 |
| 内置画风 | 扩到 47 个，分 8 个家族：电影与综合、日系漫画、国风与华语、欧美漫画、欧洲漫画、独立与实验、美术媒介、韩漫与条漫；分组下拉展示，分析按题材推荐 |
| 阶调扩展 | handraw 原始 16 阶调 + 本地 `T-17–T-40` 共 40 个（雨夜/月夜/霓虹/煤油灯、逆光/侧逆光/顶光/底光、复古褪色/粗颗粒等） |
| 触发词 | 改为“风格家族 + 该风格真实 traits + 阶调”，365 条触发词全部唯一；旧工程未手改的触发词自动升级为新格式 |
| 风格人设 | 人设生成新增“画风优先”段：按当前画风重新解释五官/比例/服装材质，不套旧模板 |
| 分页 | 分镜分析新增 `beat/pacing/panel_emphasis/caption`；本地 DP + AI 方案严格校验，AI 碎片化方案（单格页 >35%）直接退回 |
| 版式 | 从 handraw SB-001–SB-071 + 原生 WB-* 里按内容选；1 页 1~6 格 |
| 工作流 | 新增 `style_color`（ColorTransfer 只迁色）/ `style_ref`（实验性 Omni）/ `style_img2img` |
| 文字盖图 | 旁白合并为 1 条、旁白宽度 78%→52%、气泡宽度 58%→46%、分格页文字再收一档 |
| 留白 | 分格页默认不再要求模型留白（否则窄长格上半格会被刷成空地）；要留白可开 `BUBBLE_RESERVE_PAGE_ENABLED` |
| 道具冲突 | 角色没眼镜却写"镜片/眼镜"、面部"细疤"，会被画成眼罩；生成前自动改写 |
| 眼部青影 | "眼下淡青影"会被 Z-Image 画成绿色眼影；提示词改写为"眼周自然肤色"，负向加"眼影/绿色眼影/眼下绿色/彩妆" |
| 负面条件 | 四份工作流都接 `CLIPTextEncode → KSampler.negative`（不再用 ConditioningZeroOut）；按镜头/角色/姿态总图/背面/风格组合；默认 cfg=1.4 |
| 角色造型 | 只保留一件外层服装、去掉薄纱/透明描述；无眼镜角色清理眼镜；主视图/姿态总图共用 `character.seed` |
| 服装颜色 | 角色提示词只取【服装风格】段并压掉 keywords 里的第二件外层；分镜追加“服装锁定”；深色外套角色负向加米白/奶油白外套；主题色只作用于背景/环境光 |
| 姿态总图 | 默认一张 Pose ControlNet 多姿态总图；主视图自动识别正面裁出；自定义骨架可按本格出图 |
| 文档 | 需求说明书重写为 Z-Image 版；README/条漫规则/rules 01–08/comfy_workflows 全部补 handraw、分页、触发词、负面条件与角色造型说明 |
| 回溯 | `逐格提示词.jsonl` 每格只留最新一版；`绘制风格.md` 写入阶调/触发词 |
| 验收 | `selftest.py` 全绿；`outputs/222` 为最终成品；`outputs/trigger_test` 为触发词 A/B 对比 |

---

## 九、2026-10 专项：多人撞脸 / 单人乱入

### 9.1 问题拆成两条链路

| 现象 | 直接原因 | 修法 |
| --- | --- | --- |
| 双人撞脸/服装互换 | 两人的特征词挤在一段里，被文本编码器平均化；卡片还会被预算从中间切断 | `castguard` 一人一段 + 94 字短卡 + 具体差异对照 + 左右分区 + 中间留空 |
| 三人/四人画面被压成两人或拆光 | 旧版 `max_characters=2`，`_split_multi_character_shots` 把 2 人以上全拆成单人格 | 单格上限提到 4 人；只拆 5 人以上；3~4 人逐人分块 + 横列留空 + 预算 980/1060 字 |
| 单人凭空多一个人 | 场景里的"街头/酒馆/人群/路人/对方/肩背"被当成补人理由；过肩机位自带第二个人 | 单人场景去人群 + 过肩一律降斜侧 +"唯一的人影"构图锁 |
| 双人画面多出第三个人 | 过肩镜头的"前景肩背"被画成一个独立的人，即使 characters 只有 2 人 | 双人也降斜侧；用门框/桌沿做前景遮挡 |
| 单人前后两张脸 | 姿态骨架 `action_04` 里叠着两个头；"打开"的"打"误触发它 | `poseplan.validate_pose()` 拦截坏骨架 + 动作词上下文匹配；选不到就不挂 |
| 角色卡变病句 | 超预算时按 64/52/42 字切卡，切出"眼睛（画面左侧…" | 双人卡按 94 字整卡生成，只整段丢弃，不切字 |
| 正向写"不要画第二个人" | cfg=1.4 下正向否定会把"第二个人"喂给模型 | 正向只写"只有一位人物/唯一的人影"；否定全部进 negative |

### 9.2 新增文件与入口

- `comicgen/castguard.py`：人数锁、单人场景去人群、双人差异对照、负向清单、导演层诊断；
- `comicgen/poseplan.py`：新增 `validate_pose()`，拦截无头/多头骨架；
- `prompts.shot_image_prompt()`：接入 `castguard`，新增 `isolation` 段落；
- 单格人数上限改为 4（`config.MAX_CHARACTERS_PER_SHOT`），3~4 人保留同框，
  5 人以上才拆；3 人预算 980 字、4 人 1060 字；
- `pipeline.build_shot_prompt()`：不再改写 `shot.description`，不再往正向追加否定句；
- `director.normalize()`：单人召唤词自动清洗、过肩统一改斜侧；
- `director.validate()`：新增 `solo_crowd_words` / `solo_ghost_names` /
  `over_shoulder_risk` / `solo_wide_empty` / `pair_collision` / `pair_no_sides`；
- `selftest.py`：新增人数锁/隔离/坏骨架断言；纯道具格补演示道具图后全流程 22/22 通过。

### 9.3 可直接套用的骨架

**双人**：

> 画面里只有两位人物，就是 A 和 B；画面里出现的其他东西都是环境与道具。
> 画面左侧三分之一是 A：[脸/发/眼/外套/记忆点]；画面右侧三分之一是 B：[…]。
> 两张脸各自独立：A 是…，B 是…；A 穿长款，B 穿短款。
> A 在左三分之一，B 在右三分之一；两人之间留出一臂宽的空隙，背景从空隙里透出来。

**三人/四人**：

> 画面里只有三位人物，就是 A、B、C；画面里出现的其他东西都是环境与道具。
> 左侧前景是 A：[…]；中央中景是 B：[…]；右侧中景是 C：[…]。
> 每张脸各自独立：A 是…；B 是…；C 是…。外套轮廓不同：A 长款，B 短款，C 针织衫。
> A 在左三分之一，B 在中央，C 在右三分之一；相邻两人之间留出半臂宽的空隙。

四人最后一句改为"画面从左到右依次是 A、B、C、D，排成一条错落的横列"。
单格上限 4 人；5 人以上由导演层拆成群像 + 单人反应格。

**单人**：

> 画面里只有一位人物，就是 A；画面里出现的其他东西都是环境与道具。
> 画面里只有 A 一个人物，只画头顶到胸口，腰部以下在画面之外；
> 背景是虚化的空无一人的夜晚街巷，画面里唯一的人影就是 A。

**负向（单人）**：`第二个人, 多余人物, 背景路人, 人群, 围观者, 观众, 半截人影, 克隆人, 双胞胎, 分身, 镜像人, 倒影人, 画面分割, 分屏`。

> Z-Image 不解析 `(独自一人:1.3)` 这类 A1111 权重；用句首人数锁 + 重复"唯一的人影"代替。

### 9.4 实测记录

固定角色（林晚/沈砚/林雨）、固定 seed、832×1216：

| 用例 | 修前 | 修后 |
| --- | --- | --- |
| 单人近景 | 1 人，但全身构图 | 1 人，构图仍偏全身（FD-010 画风本身偏全身，已在提示词加"腰部以下在画面之外"） |
| 单人夜景远景 | 1 人 | 1 人，画面边缘无补人 |
| 双人对峙 | 2 人，脸可区分，但提示词被截断、丢了画面内容 | 2 人，脸/服装/记忆点均可区分，869 字内包含动作与画面内容 |
| 双人过肩 | 2 人 + 左下角多出一个大肩背（第三个人） | 降斜侧后 2 人，无第三个人 |
| 同色系姐妹双人 | 2 人，靠发色区分 | 2 人，脸型/眼型/发型/服装轮廓四项对照，843 字 |
| 三人群像（林晚/沈砚/林雨） | 旧版会被压成 2 人 | 3 人同框，三张脸可区分，928 字 |
| 四人群像（老港/陆鸣/高星/运维） | 旧版会被拆成单人格 | 4 人同框，四张脸可区分，997 字 |
| 真实故障格（货梯走廊） | 前后两张脸（坏骨架导致） | 1 人，无重复脸 |

产物：`outputs/guard_bench/baseline/`（修前）、`guard_final/`（双人/单人）、
`multi/cast3.png` 与 `multi/cast4.png`（三人/四人）、`shot8_fixed.png`（真实故障格修复）。

### 9.5 仍需知道的限制

- 纯提示词双人一致性上限约 70~75%；3~4 人会更低，且越靠后的人越容易被边缘化。要再往上必须上区域控制（Regional/Attention Couple）或角色参考工作流。当前 `style_ref` 工作流实测会出棋盘格伪影，未启用。
- 单格默认上限 **4 人**（`MAX_CHARACTERS_PER_SHOT`，可调）；5 人以上自动拆成群像 + 单人反应格。4 人已接近 Z-Image 纯文生图的稳定上限。
- 单人远景/近景的"半身构图"在 FD-010 这类偏全身的画风下不稳定；提示词已写死"腰部以下在画面之外"，但模型仍可能画全身。必要时在成品层裁切，或换更偏半身的画风。
- `views/` 下多数骨架缺少头部关键点，`validate_pose()` 会拒绝；动态镜头目前只有 `actions/` 里少数几张可用，姿态控制能力比文档早期描述的弱。

---

## 十、2026-11 角色预设 / 分镜一致性改版

这一版把“角色预设”和“分镜参考图”正式拆成两条链：

```text
角色预设：主视图（T2I）→ 面部特写（裁）→ 三视图（正面=主视图）→ 侧/背单视图 → 俯视（质检 top_down/high_angle）
分镜：按景别/视角/人数路由 1~2 张参考图 + 身份卡 + 年龄/身高关系句 + 骨架 + 画幅 pad/fit
```

### 10.1 代码改动

- `comicgen/qwenedit.py`：修复 Qwen 参考图 Autogrow 键（`images.image_N`）；参考图 pad 到分镜格；输出 `fit`；pad 缓存带路径 hash，避免不同角色串图。
- `comicgen/charrefs.py`（新增）：面部特写裁切、三视图切单视图、俯视质量检测、资产刷新。
- `comicgen/models.py`：`Character.face_ref/views/view_quality`；`Shot.view_hint/ref_plan`。
- `comicgen/pipeline.py`：`plan_shot_refs()` 路由；`refresh_character_assets()`；特写/近景清自动骨架；宽景 0 参考；多脸换 seed 兜底；主视图重做时同步重建三视图/单视图。
- `comicgen/prompts.py`：参考图职责句；年龄归一；多人年龄/身高关系句。
- `comicgen/castguard.py`：双人短卡保留年龄；年龄缺失时从人设补年龄带。
- `comicgen/poseutil.py`：中文路径 Unicode 安全读写。
- `config.py`：`QWEN_STORYBOARD_*`、`QWEN_REFERENCE_PAD_TO_PANEL`、`IMAGE_DUPLICATE_*`、`CHARACTER_FACE_FILENAME` 等开关。
- `web/`：阶段 3 改成“角色预设与参考资产”，角色卡显示主视图/面部特写/三视图/俯视图/侧面/背面。
- 文档：新增 `角色分镜一致性资料/`（README + 01–06 + 补丁 + 对比图），并给 `rules/08`、`rules/09`、`docs/角色主视图与姿态图.md` 加更新注。

### 10.2 实测结论

- 单格/双人/三人一致性可用；输出画幅等于分镜格。
- 全景/远景默认 0 参考；`main+back` 会把背影复制成两人。
- 3 人是 Qwen 群像上限；4 人实测输出 5 人，必须拆 3+1/2+2。
- Qwen `cfg=1.0` 下负向词基本不生效；人数/背景靠正面描述、路由、拆格，不靠负向词。

完整记录：[角色分镜一致性资料/06_落地实现与测试记录.md](角色分镜一致性资料/06_落地实现与测试记录.md)。


