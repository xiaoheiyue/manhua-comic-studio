<div align="center">

# 漫画生成器 · Manhua Comic Studio

**把一篇小说，自动做成一套能往下滑的彩色条漫 / 一页多格漫画页。**

本地 ComfyUI · Z-Image-Turbo / Qwen-Image-2.1 · 365 画风 · 74 种分页版式 · 气泡自动排版 · PDF / ZIP 导出

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/xiaoheiyue/manhua-comic-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaoheiyue/manhua-comic-studio/actions/workflows/ci.yml)
[![Gitee](https://img.shields.io/badge/Gitee-dotmukuro%2Fmanhua--comic--studio-c71d23.svg)](https://gitee.com/dotmukuro/manhua-comic-studio)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](requirements.txt)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#快速开始)
[![Local](https://img.shields.io/badge/inference-100%25%20local-green.svg)](#为什么是本地)

> **国内访问**：GitHub 打不开时用 Gitee 镜像 → <https://gitee.com/dotmukuro/manhua-comic-studio>
> （两个仓库同一份代码，`git remote` 各自推送）

</div>

![25 格分镜](docs/assets/screenshots/panel-grid.png)

> 目标不是"插图好看"，而是**漫画成立**：每格一个 beat，有机位/景别/构图/光线/画幅节奏，
> 台词与旁白分工明确，气泡不压脸，格子之间有 gutter，整话有钩子和悬念落点。

---

## 它做什么

```text
上传/粘贴小说
  → 精修：核心任务 / 视觉锚点 / 连续故事板
  → 内容分析：角色 / 场景
  → 人设：主视图 / 三视图 / 俯视图（可上传真人照提骨架）
  → 分镜拆解（导演层：镜头、节奏、气泡位）
  → 25 格场景分镜（5×5 联系表）
  → 关键镜头姿态 / 道具图
  → 按版式拼页 → 气泡填字 → 条漫长图 + PDF + ZIP
```

可以一键全流程，也可以在任何一步停下来改设定再继续。

### 几个真正花时间的点

| 能力 | 说明 |
|---|---|
| **画风系统** | 接 [handraw-style](https://github.com/yang0/handraw-style)：365 画风 × 36 主题色 × 40 阶调，按故事气质自动推荐 |
| **分页版式** | 74 种漫画分镜版式（SB 系列），按内容编页并自动选版式，避免整话一个版式 |
| **气泡** | 先按逐气泡坐标让模型画空白气泡，再用形状+坐标定位填字；按细节密度与肤色网格避开人脸 |
| **自检返修** | 每格出图后检查白边、文字溢出、多余气泡、全黑，低于阈值自动重画并保留最高分 |
| **角色一致性** | 身份卡 + 主视图参考 + 固定种子 + 单人拆格；同脸仍受模型能力限制，见[已知边界](#已知边界) |
| **本地优先** | 图像推理全在本机 ComfyUI；只有文本分析走可替换的 OpenAI 兼容接口 |

![工作流节点图](docs/assets/screenshots/workflow.png)

---

## 成品展示：一次完整运行

下面全部来自同一个工程（`测试示例`），没换素材、没手动修图：

| 输入 | 角色 | 分镜 | 分页 | 条漫 | 导出 |
|---|---|---|---|---|---|
| 1,053 字短篇 | 3 个（每人 5 张视图） | 25 格 | 7 页 | 2 条长图 | 条漫 + 24.4 MB PDF |

<p align="center">
  <img src="docs/assets/showcase/pages-grid.webp" alt="7 页成品：分格、气泡、页脚话号" width="100%">
</p>
<p align="center"><sub>7 页成品：自动编页选版式、气泡避开人脸、页脚带话号</sub></p>

<p align="center">
  <img src="docs/assets/showcase/page-01.webp" alt="单页成品" width="42%">
  <img src="docs/assets/showcase/characters.webp" alt="三个角色的主视图" width="56%">
</p>
<p align="center"><sub>左：单页细节　右：三视图体系里的主视图（另有侧面 / 背面 / 面部特写 / 姿态图）</sub></p>

<p align="center">
  <img src="docs/assets/showcase/webtoon-01.webp" alt="条漫长图" width="300">
</p>
<p align="center"><sub>同一话自动拼出的条漫长图（1088 × 6562）</sub></p>

<p align="center">
  <img src="docs/assets/showcase/ui-grid25.webp" alt="25 格分镜" width="49%">
  <img src="docs/assets/showcase/ui-output.webp" alt="出图与打包" width="49%">
</p>
<p align="center">
  <img src="docs/assets/showcase/ui-projects.webp" alt="工程与产物" width="49%">
  <img src="docs/assets/showcase/ui-characters.webp" alt="角色与参考资产" width="49%">
</p>

> 演示文本是一篇 1,053 字的短篇；换成你的小说，只要在第 1 步上传即可。

---

## 不止漫画：创作工坊

同一套风格库与工作流，切到「非漫画创作」可以做 7 类图：

**海报 · 社媒图文 · 信息图/长图 · 表情包（3×3 总表）· 插画/封面 · IP/头像 · 电商图**

<p align="center">
  <img src="docs/assets/showcase/studio-poster.webp" width="28%">
  <img src="docs/assets/showcase/studio-emoji.webp" width="28%">
  <img src="docs/assets/showcase/studio-infographic.webp" width="28%">
</p>
<p align="center"><sub>秋分海报（FE 风格 + 主题色）　·　3×3 表情包总表（可单独导出透明底 PNG）　·　信息图长图</sub></p>

<p align="center">
  <img src="docs/assets/showcase/studio-illustration.webp" width="23%">
  <img src="docs/assets/showcase/studio-ip.webp" width="23%">
  <img src="docs/assets/showcase/studio-ecommerce.webp" width="23%">
  <img src="docs/assets/showcase/studio-social.webp" width="23%">
</p>
<p align="center"><sub>插画 / 封面　·　IP 头像与三视图　·　电商图　·　社媒图文</sub></p>

和漫画模式不同的几处处理：

- **中文标题由程序叠加**，不让模型直接写，避免错字与重复；
- **信息图的节点与关系由程序排版**，模型只生成低对比纸感底图，不赌它画对结构；
- 表情包直接出 3×3 总表，同时可导出单贴透明 PNG；
- 参考图会先去背景、保留主体，再按选定画风重画，不是简单贴图。

![创作工坊界面](docs/assets/showcase/ui-studio-gallery.webp)

---

## 快速开始

### 0. 你需要

- [uv](https://docs.astral.sh/uv/)（用它装 Python 3.12 和依赖；下面一条命令即可安装，不用预装 Python）
- 一块 8GB 以上显存的 GPU（int8 量化，1088×1216 约 20 秒/张）
- 本地 [ComfyUI](https://github.com/comfyanonymous/ComfyUI) + 模型权重（**不随仓库分发**，用 `scripts/download_models.py` 国内直连下载，见[模型与权重](#模型与权重)）
- 一个 DeepSeek API Key（或任何 OpenAI 兼容端点）

> **仓库里没有什么**：`env/`、`node_modules/`、模型权重、`data/`、`outputs/` 都不在仓库里。
> 前两个是本地运行环境，后三个是运行产物，全部写进了 `.gitignore`；克隆下来只有一个干净的源码目录。

### 1. 用 uv 装环境与依赖

```powershell
# 1) 安装 uv（只需一次）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) 创建虚拟环境：uv 读取 .python-version，自动下载并使用 Python 3.12
uv venv .venv

# 3) 安装依赖
uv pip install -r requirements.txt

# 4) 激活（可选；后面的命令也可以直接写 .venv\Scripts\python.exe）
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv
uv pip install -r requirements.txt
source .venv/bin/activate
```

> **Python 版本由 uv 管理**：仓库根目录的 `.python-version` 固定为 `3.12`。
> `uv venv` 会读取它，自动下载 CPython 3.12（不需要系统预装），并在 `.venv/` 建环境。
> **不要降到 3.11 及以下**：代码用了 3.12 的多行 f-string 语法，低版本会直接 `SyntaxError`。
> 依赖装进 `.venv` 后，`start.ps1` / `start.sh` 会自动优先使用它。

### 2. 配密钥

```powershell
Copy-Item .env.example .env    # 然后编辑 .env，填 DEEPSEEK_API_KEY
```

`.env` 已被 `.gitignore` 排除；也可以直接用系统环境变量（优先级更高）。

### 3. 拉风格库 + 起 ComfyUI

```powershell
powershell -ExecutionPolicy Bypass -File scripts\fetch_handraw_style.ps1
# ComfyUI 单独启动：python main.py --listen 127.0.0.1 --port 8188
```

### 4. 启动

```powershell
python server.py
# 或（自动优先使用 .venv 里的 Python）
.\start.ps1
```

浏览器打开 <http://127.0.0.1:7870>。端口在 `config.py` 的 `WEB_PORT` 改。

> **没有 GPU / 没装模型也能先看看**：`python selftest.py` 会离线跑完整链路（不调用 DeepSeek、不出真图），
> 验证分段、分析、分镜、版式、气泡、拼页、PDF/ZIP 导出，约 1 分钟。

---

## 模型与权重

权重**不随仓库分发**（最小的也有 317 MB，全量 30.4 GB；GitHub 单文件上限 100 MB）。
但**不需要翻墙**：7 个走 ModelScope 魔搭（约 10 MB/s），2 个走 hf-mirror.com（国内可直连，慢一些）；
脚本带断点续传与 SHA256 校验。

```powershell
python scripts/download_models.py --list   # 看要下什么
python scripts/download_models.py          # 只下必需的 3 个（约 11.3 GB）
python scripts/download_models.py --all    # 下全部 9 个（约 30.4 GB）
```

| 路线 | 需要的权重 | 合计 |
|---|---|---|
| 最小可跑（Z-Image 文生图条漫） | 主模型 + 文本编码器 + VAE | **11.3 GB** |
| 全量（加 Qwen 路线 / ControlNet / 去背景） | 9 个文件 | 30.4 GB |

逐文件的用途、放置目录、下载来源、许可证与 SHA256：[models/README.md](models/README.md)；
机器可读清单：[models/manifest.json](models/manifest.json)。

如果本机已经装过 ComfyUI，也可以直接链接过来（不重复下载）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\link_models.ps1 -ComfyModels "D:\ComfyUI\models"
```

放好后打开界面「设置 → 图像工作流 → 检查工作流」，会逐个核对文件名与节点。

---

## 配置

所有可调项集中在 [`config.py`](config.py)，也支持同名环境变量覆盖。常用几项：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `TEXT_API_HOST` / `TEXT_MODEL` | `https://api.deepseek.com` / `deepseek-flash` | 文本服务，任意 OpenAI 兼容端点均可 |
| `COMFY_API_HOST` | `http://127.0.0.1:8188` | 本地 ComfyUI |
| `IMAGE_FAMILY` | `zimage` | `zimage` / `qwen`，两套工作流都在 `comfy_workflows/` |
| `RENDER_MODE` | `hybrid` | `webtoon` 条漫 / `page` 分镜页 / `hybrid` 混排 |
| `PAGE_MIN_PANELS` / `PAGE_MAX_PANELS` | 1 / 6 | 每页格数范围 |
| `HANDRAW_ENABLED` | `True` | 关掉回 47 个内置预设 |
| `HANDRAW_STYLE_AUTO` | `True` | 分析后自动推荐画风 |
| `COMFY_CFG` | `1.4` | 负面条件生效阈值（1.0 = 负向词失效） |

完整参数见 [手册·完整版](docs/手册_完整版.md) 与 [项目现状与默认策略](docs/项目现状与默认策略.md)。

---

## 项目结构

```text
.
├── server.py              # FastAPI 入口 + 104 个 API 路由
├── config.py              # 唯一配置中心（密钥只从 .env / 环境变量读）
├── selftest.py            # 离线自检：不花 token、不需要 GPU
├── comicgen/              # 核心流水线
│   ├── pipeline.py        #   编排：分析 → 人设 → 分镜 → 出图 → 拼页
│   ├── director.py        #   导演层：镜头、节奏、气泡
│   ├── storyboard.py      #   分镜拆解与自检
│   ├── pagelayout.py      #   74 种版式、编页、拼页
│   ├── bubble.py          #   气泡定位与填字
│   ├── stylelib.py        #   handraw 风格库适配
│   ├── exporter.py        #   条漫长图 / PDF / ZIP
│   ├── textclient.py      #   OpenAI 兼容文本客户端
│   └── imageclient.py     #   ComfyUI 客户端（队列、降级、重试）
├── web/                   # 原生 JS 前端（无构建步骤）
├── rules/                 # 给人看 + 给程序读的规则库
├── prompts/               # 提示词模板与回溯
├── comfy_workflows/       # ComfyUI 工作流 JSON（Z-Image / Qwen）
├── models/                # 模型清单与校验值；weights/ 是本地硬链接（gitignore）
├── docs/                  # 全部文档（assets/showcase 为 README 成品图）
├── scripts/               # 安装 / 拉取风格库 / 链接模型
└── data/  outputs/        # 运行期数据与产物（已 gitignore）
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [模型与权重](models/README.md) | 9 个权重的用途、大小、来源、许可、SHA256 |
| [模型与依赖清单](docs/模型与依赖清单.md) | 需要哪些模型、放哪、从哪下、许可 |
| [项目现状与默认策略](docs/项目现状与默认策略.md) | 当前默认行为与能力边界 |
| [条漫叙事与分镜规则](docs/条漫叙事与分镜规则.md) | 画布、转场配比、gutter、悬念位置（R1–R30） |
| [分镜提示词翻译层](docs/分镜提示词翻译层.md) | 分镜 JSON → 观察式 prompt |
| [创作工坊](docs/创作工坊.md) | 海报 / 信息图 / 表情包等非漫画模式 |
| [Qwen-Image-2.1 本地工作流](docs/Qwen-Image-2.1本地工作流.md) | 三个权重分别是什么 |
| [手册·完整版](docs/手册_完整版.md) | 71KB 全量说明与踩坑记录 |
| [贡献指南](CONTRIBUTING.md) / [路线图](ROADMAP.md) | 怎么参与、接下来做什么 |

---

## 已知边界

这些是实测结论，不是"待修 bug"，写出来避免预期错位：

- **同一张脸不能稳定保持**：单人近景/中景较好，全景仍会年龄感漂移；要严格同脸需 LoRA / IP-Adapter / 换更强模型。
- **背景可能多一个人**：办公室/对话场景里模型会自行补人；双人镜头默认拆成单人格规避。
- **8GB 显存**是当前主战场：32 格 / 8 页可跑通，更高分辨率要降级。
- **Windows 优先**：脚本以 PowerShell 为主，Linux/macOS 可跑但需要自己起 ComfyUI。
- **不是 SaaS**：没有账号、计费、多租户；这是本地工作室，不是"一键商用平台"。

---

## 许可与致谢

- 本仓库代码：[Apache-2.0](LICENSE)。
- 画风库：[handraw-style](https://github.com/yang0/handraw-style) © 2026 **yang0**，MIT + 署名要求。
  本项目在 README、界面「关于」与 [NOTICE](NOTICE) 中保留署名；`handraw-style/` 目录不随仓库提交，用脚本拉取。
- 模型：Z-Image / Z-Image-Turbo、Qwen-Image 均为 Apache-2.0，权重不随仓库分发。
- 其它依赖与生成内容权利见 [THIRD_PARTY.md](THIRD_PARTY.md)。

> 用真人照片提取姿态、把有版权的小说做成漫画并公开传播，责任在使用者。请确保你有权使用输入素材。

---

## 参与

- 有 bug / 想要的功能：开 [Issue](https://github.com/xiaoheiyue/manhua-comic-studio/issues)（请附日志与截图）。
- 想改代码：先看 [CONTRIBUTING.md](CONTRIBUTING.md)，`python selftest.py` 必须全绿。
- 想聊天 / 内测：见 [社区说明](docs/社区/README.md)。

> 仓库同时维护 GitHub 与 Gitee 两个镜像，改完用 `scripts/push_all.ps1`（或 `.sh`）一次推两边。

如果这个项目帮你省下了请画师的钱，点个 ⭐ 就是最好的支持。
