# -*- coding: utf-8 -*-
"""
漫画生成器 —— 统一配置文件
================================================

本项目所有可调参数都集中在本文件，其他模块只从这里读取配置，不再硬编码。

修改后无需改动任何代码，重新启动程序（或点击界面上的"重载配置"）即可生效。
也可以通过环境变量覆盖，环境变量名 = 变量名
作者注：变量名统一使用大写，分节组织，每节都有说明。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# 直接运行 config.py 自检时，控制台可能是 GBK；统一成 UTF-8，避免状态里的
# emoji/中文把自检打断。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

# =============================================================================
# 〇、API 密钥（只从环境变量 / .env 读取，代码里不留密钥）
# =============================================================================
#
# 本系统用到两套模型服务：
#   · 文本模型：DeepSeek 官方 API（deepseek-flash / DeepSeek-V4.1-Flash-1017）—— 用 DEEPSEEK_API_KEY
#   · 图像模型：本地 ComfyUI（默认 http://127.0.0.1:8188，工作流 image_z_image_turbo_int8）
#     ComfyUI 默认不带鉴权，因此图像侧**不需要密钥**；只有套了反向代理鉴权时才要填
#     COMFY_API_KEY（见第三节）。
#
# 密钥的优先级：系统环境变量 > 项目根目录的 .env 文件 > 空。
# 首次使用：把 .env.example 复制成 .env，填入自己的密钥即可；.env 已被 .gitignore 排除。
# 这样配置文件和公开仓库里都不会出现真实密钥。


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载器：每行 KEY=VALUE，支持 # 注释与成对引号。

    已经存在的系统环境变量优先，不会被 .env 覆盖——方便 CI / 服务器注入。
    不引入 python-dotenv，少一个依赖。
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parent / ".env")

# ==== DeepSeek 官方 API KEY（用于小说分析、人设、分镜拆解等文本任务）====
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _pick(cfg_value: str, *env_names: str) -> str:
    """配置里写了就用配置，否则依次读环境变量。"""
    if cfg_value and cfg_value.strip():
        return cfg_value.strip()
    for name in env_names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def api_key() -> str:
    """图像服务（本地 ComfyUI）鉴权密钥。ComfyUI 默认无鉴权，通常为空。"""
    return _pick(COMFY_API_KEY, "COMFY_API_KEY", "COMFYUI_API_KEY")


def text_api_key() -> str:
    """文本服务（DeepSeek 官方）密钥。"""
    return _pick(DEEPSEEK_API_KEY, "DEEPSEEK_API_KEY", "DEEPSEEK_KEY")


def api_key_status() -> str:
    """图像服务密钥状态（不泄露完整密钥）。"""
    key = api_key()
    if not key:
        return ("✅ 本地 ComfyUI 默认无鉴权，无需 API 密钥。"
                "（若你给 ComfyUI 套了反向代理鉴权，请在 config.py 顶部的 "
                "COMFY_API_KEY 中填写）")
    source = ".env / 环境变量 COMFY_API_KEY" if key else "未配置"
    return f"✅ 已读取 ComfyUI 鉴权密钥（来源：{source}，前缀 {key[:4]}…）"


def text_api_key_status() -> str:
    """文本服务密钥状态（不泄露完整密钥）。"""
    key = text_api_key()
    if not key:
        return ("❌ 未配置 DeepSeek 密钥。请把 .env.example 复制为 .env 并填入 "
                "DEEPSEEK_API_KEY，或设置同名系统环境变量后重启程序。")
    source = ".env / 环境变量 DEEPSEEK_API_KEY" if (DEEPSEEK_API_KEY or "").strip() \
        else "环境变量 DEEPSEEK_API_KEY"
    return f"✅ 已读取（来源：{source}，长度 {len(key)}，前缀 {key[:4]}…）"


# =============================================================================
# 一、项目路径
# =============================================================================

# 项目根目录（本文件所在目录）
BASE_DIR: Path = Path(__file__).resolve().parent

# 运行期数据目录（中间结果全部落盘，方便随时调整和重新生成）
DATA_DIR: Path = BASE_DIR / "data"
UPLOAD_DIR: Path = DATA_DIR / "uploads"          # 上传的小说文本
PROJECT_DIR: Path = DATA_DIR / "projects"        # 每个任务一个 JSON 工程文件
CHARACTER_DIR: Path = DATA_DIR / "characters"    # 角色参考图（三视图/主视觉图）
SHOT_DIR: Path = DATA_DIR / "shots"              # 分镜生成图（含气泡成品）
CACHE_DIR: Path = DATA_DIR / "cache"             # 请求缓存（同参数不重复出图）
LOG_DIR: Path = DATA_DIR / "logs"                # 运行日志
JOB_DIR: Path = DATA_DIR / "jobs"                # 后台任务状态
# 记住"最后一次打开的工程"，服务重启后自动恢复，避免界面显示成空工程。
LAST_PROJECT_FILE: Path = DATA_DIR / "last_project.txt"

# 最终成品输出根目录。项目下的输出文件夹，名称可在界面上自定义。
OUTPUT_ROOT: Path = BASE_DIR / "outputs"

# 品牌资源目录（Logo、背景图等静态素材，由 /assets 路由对外提供）
ASSETS_DIR: Path = BASE_DIR / "assets"

# -----------------------------------------------------------------------------
# 规则库目录（角色一致性 / 分镜 / 提示词 / 校验）
# -----------------------------------------------------------------------------
# 所有"怎么画、怎么写、怎么校验"的规则都放在这里，文档给人看、data/*.json 给程序读。
# 加载器见 comicgen/rules.py；改规则只需要改 rules/，不需要改代码。
RULES_DIR: Path = BASE_DIR / "rules"

# 提示词总长度上限（字符）。超了由 rules.drop_order 决定的顺序裁剪。
# Z-Image 实测 900 左右稳定；Qwen-Image-2.1 的 8B 文本编码器可以吃更长，
# 这里放宽到 1000，保证“年龄/体型/脸型/花白头发/记忆点”身份锁不被裁掉。
PROMPT_MAX_CHARS: int = 1000

# 分镜 JSON → 观察式视觉 prompt 的翻译层：
#   off      = 保持旧版拼接
#   template = 规则模板（零延迟，默认）
#   llm      = 让文本模型只重写“首句框架”，失败自动回退 template
PROMPT_REWRITER: str = "template"

# 是否强制角色跨角色差异化（撞车检测 + 唯一点缀）。关掉只保留身份卡。
CHARACTER_DISTINCTNESS_ENABLED: bool = True

# 提示词回溯：每次真正发给模型的提示词/气泡计划都落盘到
# data/projects/<工程id>/prompts/<阶段>/，方便倒查"这张图当时用的哪版提示词"。
PROMPT_TRACE_ENABLED: bool = True

# 出图后是否尝试人脸检测（需要额外模型；默认关，见 rules/04 第 2.1 节）
IMAGE_FACE_CHECK: bool = False

# 自动创建的目录清单
AUTO_CREATE_DIRS = [
    DATA_DIR, UPLOAD_DIR, PROJECT_DIR, CHARACTER_DIR,
    SHOT_DIR, CACHE_DIR, LOG_DIR, JOB_DIR, OUTPUT_ROOT, ASSETS_DIR,
    BASE_DIR / "comfy_workflows",      # ComfyUI 工作流备份副本
]

# =============================================================================
# 二、文本大模型（DeepSeek 官方 API —— deepseek-flash / DeepSeek-V4.1-Flash）
# =============================================================================
#
# 文本任务（小说分析 / 人设 / 分镜拆解 / 导演优化）走 DeepSeek 官方 API，
# 密钥用 DEEPSEEK_API_KEY（见文件顶部）。图像任务走本地 ComfyUI（见第三节）。
#
# 官方接口当前支持的模型 id 是 deepseek-flash（产品名 DeepSeek-V4.1-Flash，
# 2026-10-17 版本快照即需求里说的 DeepSeek-V4.1-Flash-1017）。接口用 id
# 路由，传 "DeepSeek-V4.1-Flash-1017" 这类展示名会 400，因此这里模型 id 填
# deepseek-flash，展示名单独放 TEXT_MODEL_DISPLAY。

# 文本服务根地址（不要带 /v1，程序会自动拼接）
TEXT_API_HOST: str = "https://api.deepseek.com"

# OpenAI 兼容前缀
TEXT_API_BASE: str = f"{TEXT_API_HOST}/v1"

# 文本模型名称（服务端模型 id；deepseek-flash = DeepSeek-V4.1-Flash 1017 快照）
TEXT_MODEL: str = "deepseek-flash"

# 模型显示名（仅用于界面与日志展示）
TEXT_MODEL_DISPLAY: str = "DeepSeek-V4.1-Flash-1017"

# 备选模型名：若主模型名不可用，按顺序自动尝试
TEXT_MODEL_FALLBACKS: tuple[str, ...] = (
    "deepseek-flash",
    "deepseek-v4-pro",
)

# 本地部署文本服务需要加载模型时用得到（官方 API 无需加载，留空即可）
TEXT_MODEL_PATH: str = ""

# 文本服务是否需要 API 密钥。DeepSeek 官方必须为 True。
TEXT_API_NEEDS_KEY: bool = True

# 是否探测 Unsloth 原生的 /api/inference/status（查文本模型加载状态）。
# 只有 Unsloth 部署才有这个端点；vLLM 等其它 OpenAI 兼容本地服务没有，
# 每轮状态检查都会打出一个 404。当前文本服务是 vLLM（:8088），故关闭。
TEXT_PROBE_UNSLOTH_STATUS: bool = False

# 是否允许程序在发现文本模型未加载时自动加载（仅本地部署场景有意义）
TEXT_AUTO_LOAD: bool = False

# 文本模型加载的最长等待时间（秒）
TEXT_LOAD_TIMEOUT: int = 1800
TEXT_LOAD_POLL_INTERVAL: float = 3.0

# 单次请求超时（秒）。V4.1-Flash 是推理模型，长分镜任务可能跑几分钟。
TEXT_TIMEOUT: int = 600

# 失败重试次数与退避基数（秒）
TEXT_MAX_RETRIES: int = 3
TEXT_RETRY_BACKOFF: float = 2.0

# 采样参数：分析类任务要稳定，温度不宜高
TEXT_TEMPERATURE: float = 0.3
TEXT_TOP_P: float = 0.9
# 输出上限。注意该值包含 reasoning token：分镜 JSON 较长时给足，
# 否则会出现 content 为空（全被推理吃掉）的假失败。
TEXT_MAX_TOKENS: int = 49152

# DeepSeek-V4.1-Flash 支持 thinking（默认开）。分镜/人设这类结构化任务
# 关掉思考链更快、更稳，也不会把 max_tokens 全耗在 reasoning 上。
# 需要更强推理时改成 False。
TEXT_DISABLE_THINKING: bool = True

# 是否给请求带上 stream_options（部分 OpenAI 兼容实现不支持，默认关闭）
TEXT_SEND_STREAM_OPTIONS: bool = False

# 小说分段分析：按字符数切块送入模型，避免超长上下文。
# DeepSeek-V4.1-Flash 支持 1M 上下文，普通章节（≤2 万字）整章一次送入，
# 这样分镜的全局节奏、伏笔与角色一致性才不会在分段处断裂。
CHUNK_SIZE: int = 18000          # 每块目标字符数
CHUNK_OVERLAP: int = 600         # 相邻块重叠字符数，防止情节被切断
CHUNK_CONCURRENCY: int = 3       # 分段分析的并发数

# -----------------------------------------------------------------------------
# 第 2 步「小说精修 + 连续故事板」的长文本策略
# -----------------------------------------------------------------------------
# 精修比分析更耗输出 token（8~12 锚点 + 25 格，每格要带原文依据），
# 因此阈值比 CHUNK_SIZE 小：超过该字数先分段提取"锚点卡 / 候选格"，
# 再用一次合并调用压成 25 格，避免长章节后半段被模型"跳读"。
NOVEL_REFINE_WHOLE_CHARS: int = 14000
NOVEL_REFINE_CHUNK_SIZE: int = 7000
NOVEL_REFINE_CHUNK_OVERLAP: int = 400
NOVEL_REFINE_CHUNK_CONCURRENCY: int = 2
# 结构化输出上限：8~12 锚点 + 25 格；少于该值视为失败，由合并层补齐。
NOVEL_REFINE_MIN_ANCHORS: int = 6
NOVEL_REFINE_CELLS: int = 25

# -----------------------------------------------------------------------------
# 分镜出图提示词的分级预算（字符）。超预算时按级别裁剪，不切半句。
# -----------------------------------------------------------------------------
# 首句框架永远第一且不参与丢弃；身份锁按景别分级；场景/动作/风格各留上限。
PROMPT_BUDGET: dict[str, int] = {
    "frame": 120,          # 媒介/版式/景别/机位/场景/时间/光线
    "cast_establishing": 120,   # 远景建立镜头：只保留轮廓级身份锁
    "cast_wide": 150,
    "cast_full": 190,
    "cast_medium": 280,
    "cast_close": 420,
    "cast_extreme": 520,
    "scene": 140,
    "action": 80,
    "expression": 60,
    "focus": 40,
    "style": 160,
    "composition": 100,
}

# 分镜拆解：原文不超过该字符数时整章一次拆完（不再分批），
# 超过才退回分段 + 合并。整章拆解是分镜节奏稳定的关键。
STORYBOARD_WHOLE_CHAPTER_CHARS: int = 24000
# 超长文本分段拆解时每段的字符数。9000 字约 60~80 格，正好落在
# DeepSeek-V4.1-Flash 单次输出可稳定完成的区间。
STORYBOARD_CHUNK_SIZE: int = 9000

# 分镜拆解时每批处理的原文块数（仅超长文本分段时使用）
STORYBOARD_CHUNK_BATCH: int = 2

# 每个分镜最少/最多格数保护值（防止模型失控或输出过少）
MIN_SHOTS: int = 12
MAX_SHOTS: int = 400

# 分镜拆解时的目标格数下限提示。设为正数时会在提示词里明确要求
# "至少拆出这么多格"，适合需要完整叙事的长篇章节。
# 0 表示不约束，交给模型按内容自行决定。快看单话通常 45~80 格。
STORYBOARD_MIN_SHOTS_HINT: int = 0

# 单张分镜图里最多叠几个对话气泡
MAX_BUBBLES_PER_SHOT: int = 6

# 一个分镜里"建议"的最大对话条数。超过这个数会在分镜拆解后被自动拆细，
# 避免一张图叠太多气泡把画面盖住。0 表示不拆。
# 快看单格通常 ≤3 条；这里收紧到 2，逼出"说话人近景 / 听者反应"的交替。
SHOT_DIALOGUE_SOFT_LIMIT: int = 2

# ---- 分镜整理（第四阶段后处理）----
# 文本模型经常把小说原文整段抄进 description（含引号台词），会连锁造成：
# 台词被喂进图像提示词（图像模型把中文画进画面）、dialogues 为空（成品没气泡、
# 拆格节奏控制失效）、镜头/情绪/场景退化成同一个值。下面这些开关做确定性整理。
STORYBOARD_EXTRACT_DIALOGUE: bool = True      # 从画面描述里抽出台词并归位到 dialogues
STORYBOARD_STRIP_QUOTES_FROM_DESC: bool = True  # 抽完后把描述里的引号内容清掉
STORYBOARD_ENFORCE_RHYTHM: bool = True        # 强制镜头节奏（建立镜头/打断长同镜头段）
STORYBOARD_MAX_SAME_SHOT_RUN: int = 2         # 同一种镜头最多连续几格
STORYBOARD_CLOSEUP_MIN_RATIO: float = 0.22    # 近景+特写至少要占的比例
STORYBOARD_ESTABLISHING_TYPES: tuple[str, ...] = ("远景", "全景")  # 建立镜头
STORYBOARD_DIVERSIFY_MOOD: bool = True        # 情绪去退化
STORYBOARD_MAX_MOOD_RATIO: float = 0.45       # 单一情绪占比超过它就重排
STORYBOARD_DETECT_SCENE_SHIFT: bool = True    # 按原文地点线索识别场景切换
STORYBOARD_DRAFT_NARRATION: bool = True       # 规则兜底生成旁白（时间/地点/心理）
STORYBOARD_NARRATION_PER_SHOTS: int = 5       # 平均每多少格至少要有 1 条旁白
STORYBOARD_NARRATION_MAX_CHARS: int = 28      # 规则生成的旁白最长字数
# 台词抽取时，引号里的内容短于该长度且都是拟声字，则判为音效
STORYBOARD_SFX_MAX_CHARS: int = 6

# 分镜提示词里注入的单角色外貌关键词条数（人设关键词复用，保障一致性）
CHARACTER_KEYWORD_COUNT: int = 6

# ---- 导演层（第四阶段：把"原文搬运"改成"可拍的镜头"）----
# 目标不是把小说插图化，而是产出快看漫画那种"一格格能读下去"的分镜：
# 每格一个 beat、有机位/景别/构图/光线/画幅/转场，台词与旁白分工明确。
DIRECTOR_ENABLED: bool = True                 # 是否启用导演层后处理
DIRECTOR_LLM_ENABLED: bool = True             # 是否让文本模型做一次"导演重写"
# 新工程拆完分镜后，如果仍检测到"小说体描述 / 画外音混入"，自动重拍一次。
# 已出图的工程不会自动重拍，避免作废成品。
DIRECTOR_AUTO_REPAIR: bool = True
# "一键全流程"：从正文自动跑到导出，已有阶段结果会自动跳过（可随时人工修改后继续）
AUTO_PIPELINE_ENABLED: bool = True
AUTO_EXPORT_AFTER_GENERATE: bool = True
# 空镜/屏幕/物件格如果没有列角色，图像模型很容易凭空画一个陌生男人。
# 开启后自动把"第一主角"补进这些格子（并在描述里点明"是画面主体"），
# 靠身份卡锁住性别与形象——当前工作流是纯文生图，没有参考图可用。
# 注意：**屏幕/手机/桌面这类物件特写不会补主角**（否则会变成手机屏幕上的脸，
# 见 rules/00 证据 E、rules/02 P3）。如果你的故事需要纯空镜，可以整体关掉。
AUTO_ASSIGN_PROTAGONIST_TO_EMPTY_SHOTS: bool = True
DIRECTOR_DESCRIPTION_MIN_CHARS: int = 18      # 画面描述少于该长度视为不合格
DIRECTOR_DESCRIPTION_MAX_CHARS: int = 120     # 画面描述上限，防止又写成小说段落
DIRECTOR_MAX_DIALOGUE_CHARS: int = 20         # 单条台词硬上限（快看单气泡 ≤20 字）
DIRECTOR_MAX_DIALOGUES: int = 3               # 单格台词硬上限
DIRECTOR_MAX_NARRATION_CHARS: int = 26        # 单条旁白硬上限
DIRECTOR_SILENT_MIN_RATIO: float = 0.12       # 无台词格至少占比
DIRECTOR_CLOSEUP_MIN_RATIO: float = 0.24      # 近景+特写至少占比
DIRECTOR_CLOSEUP_MAX_RATIO: float = 0.30      # 近景+特写上限（超过就把近景降中景）
DIRECTOR_MAX_TWO_SHOT_RATIO: float = 0.25     # 双人同框上限（超过就拆成单人斜侧 + 画外音）
# 单格最多几个人物：3~4 人用于群像/对峙/建立镜头；超过就拆格。
# 纯文生图 4 人已接近上限，5 人以上必然串脸。
MAX_CHARACTERS_PER_SHOT: int = 3
# Qwen 群像实测：4 人同框会多画一个（尤其有儿童/少年），按 3 人上限用，
# 4 人拆 3+1 或 2+2；默认 STORYBOARD_ONE_PERSON_PER_PANEL=True 时不受影响。
AUTO_SPLIT_MULTI_SHOT: bool = True             # 分页前把超过单格上限（默认 4 人）的镜头拆开
# 一致性优先：默认把所有 2 人以上镜头拆成「一格一人」。
# 如果项目需要 2~3 人群像，可关闭；2 人每人一张面部特写，3 人 0 张+关系句，
# 4 人拆 3+1/2+2（Qwen 实测 4 人会多画）。默认开启，适合角色一致性要求高的项目。
STORYBOARD_ONE_PERSON_PER_PANEL: bool = True
# 分镜总格数：按正文长度换算，16 格/千字，并夹在 30~120 之间
STORYBOARD_SHOTS_PER_1K_CHARS: float = 16.0
STORYBOARD_MAX_SHOTS: int = 120
DIRECTOR_ESTABLISHING_RATIO: float = 1.0      # 每个新场景至少 1 格建立镜头
DIRECTOR_MAX_SAME_ANGLE_RUN: int = 2          # 同一机位最多连续几格

# 镜头机位 / 构图 / 光线 / 转场（模型与本地兜底共用）
SHOT_ANGLES: list[str] = ["平视", "俯视", "仰视", "斜侧", "过肩", "主观", "背影"]
SHOT_COMPOSITIONS: list[str] = [
    "中心构图", "三分构图", "前景遮挡", "对称构图", "对角线构图", "框中框",
]
SHOT_LIGHTINGS: list[str] = [
    "自然光", "侧光", "逆光", "顶光", "低调光", "高调光", "霓虹光", "月光",
]
TRANSITION_TYPES: list[str] = [
    "动作衔接", "主体切换", "场景切换", "氛围铺陈", "时刻分解",
]

# 条漫分格画幅档位：S=快节奏/动作，M=常规对白，L=情绪/冲击，XL=转场/大场面
PANEL_SCALE_OPTIONS: list[str] = ["S", "M", "L", "XL"]
PANEL_SCALE_FACTOR: dict[str, float] = {"S": 0.72, "M": 1.0, "L": 1.3, "XL": 1.62}

# 气泡槽位（由导演层指派，避免压脸；渲染层会做碰撞与避让）
BUBBLE_SLOTS: list[str] = [
    "top-left", "top-right", "top-center",
    "mid-left", "mid-right",
    "bottom-left", "bottom-right",
]
BUBBLE_SLOT_TO_TAIL: dict[str, str] = {
    "top-left": "down-right", "top-right": "down-left", "top-center": "down",
    "mid-left": "down-right", "mid-right": "down-left",
    "bottom-left": "up-right", "bottom-right": "up-left",
}


# =============================================================================
# 三、图像生成服务（本地 ComfyUI，默认 http://127.0.0.1:8188）
# =============================================================================
#
# 图像走 ComfyUI 的 HTTP API：
#   POST /prompt            提交工作流（API 格式的节点图）
#   GET  /history/{id}      轮询执行结果
#   GET  /view              下载成图字节
#   GET  /system_stats      健康检查（显卡、显存、版本）
#
# 工作流按**名称**从 ComfyUI 中读取（GET /api/userdata/workflows/<名称>.json），
# 程序会自动把界面格式（含子图）拍平成 API 格式，并定位"正向提示词 / 画幅 /
# 种子 / 保存节点"，写入本次参数。所以你在 ComfyUI 里改工作流（换模型、改
# 步数、加节点）会直接生效，不需要改代码。
#
# 读取顺序（先命中者为准）：
#   1. COMFY_WORKFLOW_FILE              显式指定的工作流 JSON
#   2. ComfyUI 里的同名工作流           按 COMFY_WORKFLOW_NAME 取名
#   3. COMFY_WORKFLOW_DIR 下的同名文件  本项目自带的备份副本
#   4. 内置兜底模板                     COMFY_INLINE_FALLBACK

# 图像生成后端（目前只有 ComfyUI 一种实现）
IMAGE_BACKEND: str = "comfyui"

# ComfyUI 服务根地址（不要带结尾的 /）
COMFY_API_HOST: str = "http://127.0.0.1:8188"

# ComfyUI 鉴权密钥。ComfyUI 默认不鉴权，留空即可；
# 仅当你用 nginx 之类的反向代理加了 Authorization 校验时才需要填写。
COMFY_API_KEY: str = ""

# 工作流名称（= ComfyUI「工作流」侧栏里的文件名，不带 .json）
# 当前漫画出图后端：zimage / qwen
# zimage：Z-Image-Turbo（ControlNet/姿态）
# qwen  ：Qwen-Image-2.1 Turbo（参考图 Edit / 纯文 T2I）
# 2026-11 角色预设/分镜参考路由按 Qwen 实现；Z-Image 仍可作画风/姿态备用，
# 但 Z-Image 的 style_color 不执行“面部特写/单视图”参考图路由。
IMAGE_FAMILY: str = "zimage"

# Qwen 分镜出图策略：
#   True  = T2I（与第 6 步 25 格同一条路径，构图/场景跟随提示词，推荐）
#   False = 角色参考图 Edit + 自动姿态 ControlNet（锁脸更强，但容易照搬参考图）
# Z-Image 不受此项影响。
QWEN_STORYBOARD_T2I: bool = True

# 纯 T2I 的代价是"每格重新猜脸"。打开下面的开关后，Qwen 分镜会按镜头路由
# 送 1~2 张角色参考图（面部特写/主视图/侧面/背面/俯视），不再整张三视图一起送。
# 详见 角色分镜一致性资料/03_分镜参考路由与提示词规范.md
QWEN_STORYBOARD_USE_CHAR_REFS: bool = True
QWEN_STORYBOARD_REF_MAX: int = 3
# 全景/远景默认不送角色参考：脸占比小，参考收益低，且容易把参考人物再画一遍。
QWEN_STORYBOARD_WIDE_USE_REF: bool = False
# 有面部特写时，特写/近景优先送面部特写；没有就回退主视图。
QWEN_STORYBOARD_FACE_REF: bool = True
# 送参考图前 pad 到分镜格比例，避免 Qwen Edit 输出被第 1 张参考图的比例绑架。
QWEN_REFERENCE_PAD_TO_PANEL: bool = True

# 参考图去背景（角色俯视/道具/创作工坊参考图，姿态图除外）
# 使用 ComfyUI 内置 BiRefNet：LoadBackgroundRemovalModel + RemoveBackground
BG_REMOVAL_ENABLED: bool = True
BG_REMOVAL_MODEL: str = "birefnet.safetensors"
BG_REMOVAL_TIMEOUT: int = 180

COMFY_WORKFLOW_NAME: str = "image_z_image_turbo_int8"

# ---- 复杂风格工作流（嵌入 handraw-style 后新增的三份）----
# 三份都在 comfy_workflows/ 里有备份，同时在 ComfyUI 里放同名工作流即可直接生效。
#   style_color : 生成后按画风参考图做 ColorTransfer（只迁色彩/色调，不迁内容）
#                 ← 默认风格工作流，只用基础 Z-Image Turbo 权重即可
#   style_ref   : Z-Image Omni 参考图条件（1 张画风 + 最多 2 张角色）；LoRA 已停用
#                 ← 实验性：需要 Z-Image Omni 权重；用基础权重会抄参考图内容
#   style_img2img: 参考图 → VAEEncode → 低 denoise 采样（兜底，仍会带构图）
COMFY_WORKFLOW_STYLE_NAME: str = "image_z_image_turbo_int8_style_color"
COMFY_WORKFLOW_STYLE_REF_NAME: str = "image_z_image_turbo_int8_style_ref"
COMFY_WORKFLOW_STYLE_IMG2IMG_NAME: str = "image_z_image_turbo_int8_style_img2img"
# 角色姿态总图：主视图 + 画风参考走 Omni，姿势骨架走 ControlNet
COMFY_WORKFLOW_CHARACTER_POSE_NAME: str = "image_z_image_turbo_int8_character_pose"

# 色彩迁移强度（0~1，0.55 接近画风主色调但保留主体固有色）
COMFY_COLOR_TRANSFER_STRENGTH: float = 0.55
# 用户显式选了主题色时，色彩迁移只保留很弱的一层（0=完全不迁色）。
# 否则 ColorTransfer 会把风格参考图的色分布盖回来，用户选的中国红/克莱因蓝就看不出来。
COMFY_COLOR_TRANSFER_THEME_STRENGTH: float = 0.0

# 选哪份工作流：
#   auto      有画风参考图时用 style_color，否则用基础工作流
#   base      image_z_image_turbo_int8
#   style_color / style_ref / style_img2img  强制指定
COMFY_WORKFLOW_PRESET: str = "auto"

# 参考图上传到 ComfyUI 的 input 子目录（同名文件覆盖）
COMFY_UPLOAD_SUBFOLDER: str = "manhua_refs"
# 上传前把参考图统一成正方形（Z-Image Omni 条件要求多张参考图同尺寸，
# 否则会报 KSampler shape invalid；留边填充而不是拉伸，避免人物变形）
COMFY_REFERENCE_SIZE: int = 768
COMFY_REFERENCE_PAD: tuple[int, int, int] = (128, 128, 128)
# 单次请求最多送几张参考图（按工作流里实际接通的 LoadImage 数量截断）
COMFY_MAX_REFERENCE_SLOTS: int = 3

# 显式指定工作流文件（留空则按上面的名称查找）。两种格式都支持：
#   · API 格式：纯 /prompt 节点图，键是节点 id，值是 {class_type, inputs}
#   · 界面格式：ComfyUI 里 Ctrl+S 存下来的带 nodes/links 的版本（含子图也能转）
COMFY_WORKFLOW_FILE: str = ""

# 本项目自带的工作流备份目录（ComfyUI 连不上时用这里的同名文件）
COMFY_WORKFLOW_DIR: Path = BASE_DIR / "comfy_workflows"

# 工作流缓存时间（秒）。ComfyUI 里的改动最多这么久之后生效，避免每张图都拉一次。
COMFY_WORKFLOW_CACHE_SECONDS: float = 30.0

# 上面几种来源都拿不到时，是否用代码内置的 Z-Image-Turbo 模板兜底
COMFY_INLINE_FALLBACK: bool = True

# 节点定位覆盖：一般不用填，程序会自动查找。
# 只有当工作流构图比较特殊（多个采样器 / 多个保存节点）时才需要指定节点 id。
COMFY_NODE_OVERRIDES: dict[str, str] = {
    "prompt": "",    # 正向提示词节点（要求带 text 输入）
    "negative": "",  # 反向提示词节点（留空则自动识别）
    "latent": "",    # 画幅节点（要求带 width/height 输入）
    "sampler": "",   # 采样器节点（要求带 seed/steps 输入）
    "save": "",      # 保存图片节点（要求带 filename_prefix 输入）
}

# 是否用 config 里的 IMAGE_STEPS/COMFY_CFG 覆盖工作流采样器的步数/cfg。
# 默认 True：8 步不变，但 cfg 需要从 1 提到 1.4 才能让负面条件生效；
# 如果换回未蒸馏的 Z-Image，可把 COMFY_CFG 调到 3~5。
COMFY_APPLY_SAMPLER_SETTINGS: bool = True
# 反向提示词：工作流里必须有一个接到 KSampler.negative 的 CLIPTextEncode 节点，
# 且 COMFY_CFG > 1 才会真正生效（Z-Image-Turbo 蒸馏模型 cfg=1 时反向条件被忽略）。
COMFY_NEGATIVE_ENABLED: bool = True
COMFY_CFG: float = 1.4

# 成图在 ComfyUI output 目录下的文件名前缀（用 / 可以分到子目录）
COMFY_FILENAME_PREFIX: str = "manhua"

# 轮询 /history 的间隔（秒）
COMFY_POLL_INTERVAL: float = 1.0

# 排队中多久打印一次队列位置（秒）
COMFY_QUEUE_LOG_INTERVAL: float = 20.0

# 单张超时后是否顺手中断 ComfyUI 当前任务（避免它继续白占显存）
COMFY_INTERRUPT_ON_TIMEOUT: bool = True

# 当前工作流是纯文生图（没有图像输入）时，是否把提示词里
# "第 N 张参考图是某某"那段说明去掉 —— 否则模型会以为真有图可参考。
COMFY_STRIP_REFERENCE_HINTS: bool = True

# 单张图生成超时（秒）。第一次跑要加载 int8 模型 + 4B 文本编码器，
# 8GB 卡上常要 60~120 秒；之后单张约 20~40 秒。这里给足 900s 兜底。
IMAGE_TIMEOUT: int = 900

IMAGE_MAX_RETRIES: int = 2
IMAGE_RETRY_BACKOFF: float = 3.0

# ---- 出图自检 + 自动返修 ----
# 每一格生成后做结构化自检（白边/文字溢出/多余气泡/文字兜底/全黑等），
# 低于 IMAGE_QUALITY_PASS_SCORE 就带着返修要求重画，最多重试 N 次，保留最高分的一版。
IMAGE_AUTO_REPAIR: bool = True
IMAGE_AUTO_REPAIR_RETRIES: int = 2
# 多画脸检查：检测到的人脸数 > 分镜人数时，换 seed 重跑一次，保留脸数更少的一版。
# 单人/双人/群像都适用；只做 1 次，避免 8GB 上每格都翻倍耗时。
IMAGE_DUPLICATE_CHECK: bool = True
IMAGE_DUPLICATE_RETRY: int = 1
IMAGE_QUALITY_PASS_SCORE: int = 82
# 低于这个分数视为"不可交付"，该格标记失败，避免把全黑/全白图带进成品
IMAGE_QUALITY_ACCEPT_SCORE: int = 70

# 生成分辨率（宽, 高）。显存不足时降低这里。
IMAGE_SIZE: tuple[int, int] = (1024, 1024)

# 角色参考图（主视觉图 / 三视图）分辨率
# 角色 LoRA（Z-Image，8GB 可训可推理；见 rules/05_角色LoRA_8GB方案.md）
IMAGE_LORA_ENABLED: bool = False
IMAGE_LORA_STRENGTH: float = 0.8

CHARACTER_IMAGE_SIZE: tuple[int, int] = (1024, 1536)
# 人设里同时写两件外套时，提示词只保留第一件（否则主视图/姿态图各穿一件）
CHARACTER_SINGLE_OUTERWEAR: bool = True
# 主视图与姿态图使用同一个种子，尽量降低脸/五官漂移
CHARACTER_SAME_SEED: bool = True
# 主视图不再由三视图切割重排；主视图是唯一身份参考
CHARACTER_MAIN_FROM_TRIPLE: bool = False
# 旧三视图保留为兼容字段，不再逐张生成
CHARACTER_TRIPLE_SEPARATE: bool = False
CHARACTER_TRIPLE_SIZE: tuple[int, int] = (1216, 832)
CHARACTER_TRIPLE_VIEW_SIZE: tuple[int, int] = (512, 832)
# 姿态总图：一张图里放多个姿态 + 正面/侧面/背面，用于人工查看与姿态库
CHARACTER_POSE_SIZE: tuple[int, int] = (1536, 1536)
CHARACTER_POSE_FILENAME: str = "姿态图"
CHARACTER_TOP_FILENAME: str = "俯视图"
CHARACTER_TOP_SIZE: tuple[int, int] = (1024, 1024)
# 8GB / Z-Image 当前无法可靠生成真正的正上方顶视；默认只接受用户上传真实俯视参考
CHARACTER_TOP_AUTO: bool = False
CHARACTER_TOP_SKELETON: Path = BASE_DIR / "data" / "pose_library" / "top_down_skeleton.png"
# 默认俯视参考：用户放在 ComfyUI input 里的真实俯视图
CHARACTER_TOP_DEFAULT_REF: Path = Path(r"E:\Comfy-Desktop\ComfyUI-Shared\input\俯视图.png")
# Comfy Desktop 有时把 input 放在 Shared 根目录，两个位置都找
CHARACTER_TOP_DEFAULT_REFS: tuple[Path, ...] = (
    Path(r"E:\Comfy-Desktop\ComfyUI-Shared\input\俯视图.png"),
    Path(r"E:\Comfy-Desktop\ComfyUI-Shared\俯视图.png"),
    Path(r"E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\input\俯视图.png"),
)
CHARACTER_TOP_INSTRUCTION: str = (
    "角色俯视图：镜头在头顶正上方垂直向下（bird's-eye view, top-down view），"
    "只看到头顶的发旋、发缝、发髻与发饰、双肩和外套领口；"
    "头顶位于画面中心，脸被头顶遮住，看不到眼睛鼻子嘴；"
    "双臂向两侧展开，身体向下透视缩短，脚在最远处；"
    "同一角色、同一套服装、同一发型；"
    "白色纯色背景，画面里只有这一个角色；精细线稿和平涂，清晰面部与服装结构。"
)
CHARACTER_MAIN_FILENAME: str = "主视图"
# 角色参考资产：面部特写（特写/近景用）与单视图（侧/背/顶用）
CHARACTER_FACE_FILENAME: str = "面部特写"
CHARACTER_VIEW_DIR: str = "views"
CHARACTER_VIEW_SIZE: tuple[int, int] = (512, 768)
CHARACTER_POSE_WORKFLOW_NAME: str = "image_z_image_turbo_int8_character_pose"
CHARACTER_POSE_REF_WORKFLOW_NAME: str = "image_z_image_turbo_int8_character_pose_ref"
# 姿态工作流只把主视图/画风放进 Omni 参考，姿势骨架图单独走 ControlNet
CHARACTER_POSE_CONTROL_STRENGTH: float = 0.85
# 分镜自动姿态：off / key（仅动态/俯视/过肩）/ all（每格都跑，8GB 很慢）
# 2026-11-04 默认改为 off：内置 pose_library 的骨架没有「肩→髋」躯干段，
# 挂到 Qwen ControlNet 上会把头画进胸口/长衫无头；且竖骨架被按横格中心裁切
# 会切掉头脚。25 格不挂骨架、同一提示词效果正常，因此自动姿态改为显式开启，
# 需要锁动作时请在第 7 步上传自己的骨架（上传图现在会等比补边，不再裁切）。
AUTO_POSE_SHOTS: str = "off"
# 默认姿态骨架：3 个标准视角 + 3 个动作；用户上传自定义骨架时覆盖
CHARACTER_POSE_DEFAULT_SKELETON: Path = BASE_DIR / "data" / "pose_library" / "character_pose_sheet.png"

# 画幅的硬约束（写进工作流的空 Latent 节点）：
#   宽高必须是 IMAGE_DIMENSION_MULTIPLE 的倍数（SD3/Flux 系 latent 要求 16 的
#   倍数，这里统一按 32 走更保险），单边不超过 IMAGE_MAX_SIDE，总像素不超过
#   IMAGE_MAX_PIXELS。所有尺寸都会先经 normalize_image_size() 对齐再进工作流。
IMAGE_DIMENSION_MULTIPLE: int = 32
IMAGE_MAX_SIDE: int = 2048
IMAGE_MAX_PIXELS: int = 4300800

# ---- 条漫画幅（第四/第五步）----
# 条漫 = 竖屏连续长图：所有格子统一宽度，按镜头类型分档决定高度，拼条时无缝相接。
# 关闭 WEBTOON_MODE 即回到"一格一张方图"的旧行为。
WEBTOON_MODE: bool = True

# 条漫统一宽度（必须是 IMAGE_DIMENSION_MULTIPLE 的倍数）
WEBTOON_PANEL_WIDTH: int = 1088

# 按镜头类型分档的画幅（宽, 高）；高度均取 32 的倍数。
# 注意：**一律用竖幅**。实测横矮幅（宽>高）会被出图模型理解成"要在一张图里
# 并排画几格"，自动切出好几条竖画面，成品直接报废；竖幅则稳定输出单幅画面。
WEBTOON_PANEL_SIZE_MAP: dict[str, tuple[int, int]] = {
    "远景": (1088, 1280),   # 环境交代：高幅，留出天空与地面，节奏舒缓
    "全景": (1088, 1280),
    "中景": (1088, 1216),   # 人物动作与互动：中幅
    "近景": (1088, 1216),   # 情绪与台词
    "特写": (1088, 1152),
}

# 未匹配到镜头类型时的兜底画幅（同样是竖幅）
WEBTOON_PANEL_SIZE_DEFAULT: tuple[int, int] = (1088, 1216)

# 单格最大高度。实测 8GB 显存在 1280 高度以上 VAE 解码会明显变慢；
# 1280 是"清晰度 / 速度"比较稳的上限。
WEBTOON_PANEL_MAX_HEIGHT: int = 1280
# 单格最小高度：低于 1152 就容易接近方形，模型会加白边或画成横构图。
WEBTOON_PANEL_MIN_HEIGHT: int = 1152

# 推理步数与引导系数（仅当 COMFY_APPLY_SAMPLER_SETTINGS = True 时才写入工作流）。
# Z-Image-Turbo 是 8 步蒸馏模型，工作流里默认就是 8 步 / cfg=1，一般不用改。
IMAGE_STEPS: int = 8
# Z-Image-Turbo 官方推荐 cfg=1（此时反向条件被数学忽略）。要真正启用负面词，
# 必须把 cfg 提到 1 以上；1.4 是"负面词开始生效、又不至于明显糊/过曝"的折中。
# 官方模板也提醒：cfg 过高会明显失真，宁可 1.2~1.5，不要 3 以上。
IMAGE_GUIDANCE: float = 1.4

# 随机种子。-1 表示每张随机，保证分镜之间有变化
IMAGE_SEED: int = -1

# 负面提示词。注意 Z-Image-Turbo 用 cfg=1 采样（负向条件被零化），
# 这段负面词在当前工作流里**不会生效**，保留它只是为了将来换成 cfg>1 的工作流时仍可用。
# 正向提示词里已经写死了"不要写字、不要画分格"，实际约束靠那一侧。
IMAGE_NEGATIVE_PROMPT: str = (
    "低质量, 模糊, 变形, 多余的手指, 肢体错误, 比例失调, 水印, 签名, "
    "可读文字, 招牌文字, 海报文字, 字幕, 乱码文字, 错别字, 标语, 画框, 边框, 页边距, "
    "眼影, 绿色眼影, 眼下绿色, 眼周绿色, 彩妆, 浓妆, "
    "过度锐化, 脏点, 噪点块, 压缩痕迹"
)
# 分镜格专用：禁止模型把一格画成多格/拼图/画中画
NEGATIVE_SHOT: str = (
    "多格漫画, 分格, 分格线, 漫画格, 拼图, 多张拼合, 并排画面, 多个画面, "
    "竖直分割线, 画中画, 连环画, 四格漫画, 分镜排版"
)
# 角色主视图/姿态图专用：禁止第二个人、额外服装、透明衣料；同时锁定发型与双眼
NEGATIVE_CHARACTER: str = (
    "多余人物, 第二个人, 多个角色, 额外外套, 叠穿外套, 透明衣物, 薄纱, "
    "半透明面料, 透视装, 纱裙, 层叠薄布, 多余肢体, 多只手, 多只脚, "
    "头部被裁切, 脚部被裁切, 半身像, "
    "散发, 披发, 半扎发, 一半扎一半披, 双马尾, 长发披肩, 刘海遮眼, "
    "大小眼, 异色瞳, 眼睛不对称, 瞳孔偏移, 斜视, 斗鸡眼, 单眼, 三只眼"
)
# 俯视图专用：必须是正上方，不能画成正脸/平视
NEGATIVE_TOP: str = (
    "正脸, 五官, 眼睛, 鼻子, 嘴, 平视, 正面站姿, 正面全身, 标准站姿, 侧脸, "
    "半身像, 只画头, 头像"
)
# 姿态总图专用：允许多个姿态，但必须是同一角色；禁止不同发型/不同服装/不同脸
NEGATIVE_POSE: str = (
    "不同角色, 多个人物, 不同脸, 换脸, 不同发型, 不同发色, 不同服装, 换外套, "
    "散发, 披发, 半扎发, 一半扎一半披, 双马尾, 长发披肩, 刘海遮眼, "
    "大小眼, 异色瞳, 眼睛不对称, 瞳孔偏移, 斜视, 斗鸡眼, "
    "透明衣物, 薄纱, 半透明面料, 多余肢体, 多余手指, 脚部裁切, 头部裁切"
)
# 深色外套角色专用：防止模型把冷灰蓝/炭黑/墨绿风衣画成米白/奶油白外套
NEGATIVE_OUTFIT_LIGHT: str = (
    "白色外套, 米白外套, 奶油白外套, 浅色风衣, 换色外套, "
    "白色风衣, 米白色风衣, 奶油色风衣"
)
# 三视图专用：允许三个角度，但禁止三个角度服装不一致
NEGATIVE_TRIPLE: str = (
    "三个角度服装不一致, 正面穿夹克侧面穿大衣, 背面变成大衣, 背面穿长外套, "
    "另一件外套, 外套颜色不一致, 多余角色, 多余人物, "
    "透明衣物, 薄纱, 半透明面料, 透视装, 层叠薄布, 多余肢体"
)
# 三视图背面专用：只允许单人背影，禁止把正面人物一起画进来
NEGATIVE_BACK: str = (
    "双人, 两个人, 正面人物, 正面站姿, 镜像人物, 倒影, 多余人物, "
    "背面变成大衣, 背面穿长外套, 另一件外套"
)
# 二次元/漫画风格：压掉照片感
NEGATIVE_STYLE_PHOTO: str = (
    "写实照片, 真人照片, 摄影, 3D渲染, CG, 塑料皮肤, 过度磨皮, 油腻高光"
)
# 写实/电影风格：压掉低幼卡通感
NEGATIVE_STYLE_CARTOON: str = (
    "低幼卡通, 简笔画, 平涂无阴影, 粗糙的线条, Q版大头, 漫画网点"
)
# 3D/材质风格：不要压 3D，改压平面简笔
NEGATIVE_STYLE_3D: str = "平面简笔画, 纯平涂, 无材质, 无体积感"

# 参考图收集上限。实际送几张由工作流的 LoadImage 槽位决定：
#   style_color 只接 1 路（画风参考），style_ref（Omni）最多 3 路（1 画风 + 2 角色）。
MAX_REFERENCE_IMAGES: int = 10

# 参考图去重：同一角色只留一张参考图（优先三视图，其次主视觉图）
PREFER_TRIPLE_VIEW_AS_REFERENCE: bool = False

# 参考图压缩边长（像素）。留给将来接入图生图/参考图工作流时使用。
REFERENCE_MAX_EDGE: int = 896

# 显存不足时的自动降级策略（按顺序尝试）。
# ComfyUI 报 OOM 时会按这里的画幅重试，只压分辨率，不改提示词。
OOM_RETRY_PLAN: tuple[dict, ...] = (
    {"size": (960, 1088), "label": "画幅降到 960×1088 重试"},
    {"size": (864, 960), "label": "画幅降到 864×960 重试"},
    {"size": (768, 832), "label": "画幅降到 768×832 重试"},
)

# 判定显存不足的关键词
OOM_KEYWORDS: tuple[str, ...] = (
    "out of memory", "cuda oom", "oom", "显存不足", "memoryerror",
    "insufficient memory", "allocation on device", "outofmemoryerror",
)

# 服务健康检查：启动时"等待 ComfyUI 就绪"的轮询参数
STARTUP_PING_ENABLED: bool = True
STARTUP_PING_TIMEOUT: float = 5.0
STARTUP_PING_ATTEMPTS: int = 2
STARTUP_PING_INTERVAL: float = 2.0


# =============================================================================
# 四、画风与提示词模板
# =============================================================================
#
# ⚠️ 写法约定（针对当前 ComfyUI 工作流 image_z_image_turbo_int8）：
#   Z-Image-Turbo 是 8 步蒸馏模型，工作流里 cfg=1，**负向条件被 ConditioningZeroOut
#   零化**，所以"不要文字/不要边框/不要气泡"这类否定句不但没有负向作用，反而会把
#   "文字""边框""气泡"这些词本身喂进模型（实测：写过"不要气泡"，没有台词的格子也会
#   多画一个空白气泡）。因此这里的提示词一律走**正面描述**：
#   想让它画什么就写什么，想让它别画什么，就写成"画面里只有……"。
#   唯一例外是气泡指令本身 —— 那格确实需要气泡，才提"气泡"。

# 整体默认风格：电影感写实插画（不默认任何一种漫画流派，按故事气质推荐）
DEFAULT_STYLE: str = "电影感写实插画"

# 内置画风预设：按"漫画传统 / 媒介"分组，界面下拉用分组展示。
#
# 分组依据是漫画史与出版传统（日漫 / 国漫与港漫 / 美漫 / 欧漫 / 独立 / 媒介），
# 不是题材标签；韩漫与条漫只是其中一组，不再作为默认推荐。
# 每一项都必须在 STYLE_PROMPT_MAP 里有对应正文，selftest 会校验覆盖率。
#
# 参考分类：
#   日系：少年 / 少女 / 青年剧画 / 黑白网点 / 赛璐璐 / 四格 / 恐怖 / 机甲 / 水彩 / 昭和
#   华语：水墨 / 工笔重彩 / 新国风 / 仙侠 / 连环画白描 / 港式武侠 / 港式犯罪
#   欧美：超级英雄 / 黑色电影 / 独立图像小说 / 报纸连载 / 波普 / 奇幻插画
#   欧洲：法比清线 / 法比经典 / 文艺图像小说
#   独立：铅笔 / 木刻 / 炭笔 / 像素 / 剪纸
#   媒介：油画 / 绘本水彩 / 黏土 / 丝网 / 墨线 / 几何
STYLE_PRESET_GROUPS: list[dict] = [
    {"label": "电影与综合", "items": [
        "电影感写实插画",
        "欧美写实电影",
        "赛博朋克霓虹",
    ]},
    {"label": "日系漫画", "items": [
        "日系少年热血漫画",
        "日系少女漫画",
        "日系青年剧画",
        "日系黑白网点漫画",
        "日系赛璐璐动画",
        "日系四格搞笑漫画",
        "日系恐怖漫画",
        "日系机甲科幻漫画",
        "日系水彩轻插画",
        "日系复古昭和漫画",
    ]},
    {"label": "国风与华语漫画", "items": [
        "国风水墨",
        "国风工笔重彩",
        "新国风彩漫",
        "仙侠国风彩漫",
        "连环画白描",
        "港式武侠漫画",
        "港式犯罪电影漫画",
    ]},
    {"label": "欧美漫画", "items": [
        "美式超级英雄漫画",
        "美式黑色电影漫画",
        "美式独立图像小说",
        "美式报纸连载漫画",
        "欧美复古波普漫画",
        "欧美奇幻插画",
    ]},
    {"label": "欧洲漫画", "items": [
        "法比清线漫画",
        "法比经典漫画",
        "欧洲文艺图像小说",
    ]},
    {"label": "独立与实验", "items": [
        "独立铅笔素描漫画",
        "木刻版画漫画",
        "炭笔速写漫画",
        "像素风漫画",
        "剪纸纸雕漫画",
    ]},
    {"label": "美术媒介", "items": [
        "厚涂油画",
        "儿童绘本水彩",
        "3D 黏土动画",
        "复古丝网印刷",
        "黑白墨线漫画",
        "极简几何插画",
    ]},
    {"label": "韩漫与条漫", "items": [
        "彩色韩漫风格",
        "现代都市韩漫",
        "古风韩漫",
        "奇幻韩漫",
        "校园韩漫",
        "悬疑暗调韩漫",
        "治愈温馨韩漫",
    ]},
]

# 扁平清单（保持原有变量名：模型推荐、校验、旧工程兼容都读它）
STYLE_PRESETS: list[str] = [
    name for _group in STYLE_PRESET_GROUPS for name in _group["items"]
]

# 各细分风格对应的提示词正文
#
# 注意 1：这里刻意不写"精致写实的五官"和"照片/CG"这类词——出图模型对它们极敏感，
# 一写就会把插画推成照片质感。统一用"线条/平涂/网点/材质"这套可执行的画风语汇。
# 注意 2：提示词正文会被 _sanitize_prompt 处理，其中 `写实→插画`、`3D→2D手绘`、
# `照片→插画` 会被替换，所以正文里**不要出现这些词**（画风名里的"写实"不受影响）。
STYLE_PROMPT_MAP: dict[str, str] = {
    # ---- 电影与综合 ----
    "电影感写实插画": (
        "电影感画面，真实的皮肤与布料材质，精确的镜头光影与景深，"
        "低饱和电影调色，细腻颗粒，构图克制，画面像一帧电影剧照"
    ),
    "欧美写实电影": (
        "欧美电影镜头语言，自然肤色与真实材质，柔和或硬朗的镜头光，"
        "低饱和青橙或冷灰色调，胶片颗粒，宽银幕构图，画面有叙事张力"
    ),
    "赛博朋克霓虹": (
        "赛博朋克夜景，青蓝与品红霓虹，湿地面反光，密集管线与招牌，"
        "高对比彩色边缘光，电影感透视，画面有雨夜与科技压迫感"
    ),

    # ---- 日系漫画 ----
    "日系少年热血漫画": (
        "日系少年热血漫画，硬朗的钢笔墨线，速度线与冲击线，黑白块面配网点阴影，"
        "人物比例夸张、肢体舒展，动作大开大合，汗水与表情张力强烈"
    ),
    "日系少女漫画": (
        "日系少女漫画，细软流畅的线条，大眼睛与长睫毛，网点与花瓣光斑，"
        "浅粉浅蓝的柔和色调，装饰性的背景与飘落花瓣，情绪化的特写"
    ),
    "日系青年剧画": (
        "日系青年剧画，致密的排线与厚涂阴影，粗粝的墨线，冷硬的城市肌理，"
        "低饱和色彩，沉重的明暗关系，克制的镜头与沉默的情绪"
    ),
    "日系黑白网点漫画": (
        "日系黑白网点漫画，纯黑白，网点纸做灰阶渐变，细密排线，"
        "干净的轮廓线与留白，印刷纸质颗粒，明暗层次清楚"
    ),
    "日系赛璐璐动画": (
        "日系赛璐璐动画，干净黑色描边，平涂色块与硬边阴影，明亮通透的色彩，"
        "背景为手绘动画美术，人物造型简洁，复古动画赛璐璐质感"
    ),
    "日系四格搞笑漫画": (
        "日系四格搞笑漫画，简练圆润的线条，夸张的Q版变形与表情符号，"
        "平涂的明亮色块，大面积留白与拟声字，轻松诙谐的日常感"
    ),
    "日系恐怖漫画": (
        "日系恐怖漫画，极细的钢笔线与密集排线，惨白的脸与深黑眼窝，"
        "扭曲的透视与畸变，冷灰色调配突然的大面积黑，纸面细密噪点"
    ),
    "日系机甲科幻漫画": (
        "日系机甲科幻漫画，精密机械结构与面板分割线，硬边高光与金属反光，"
        "冷蓝与警示橙配色，仰视大透视，速度线与能量光效"
    ),
    "日系水彩轻插画": (
        "日系水彩轻插画，透明水彩晕染与纸纹，淡雅的蓝绿与米色，"
        "边缘自然渗开，轻柔的铅笔线，空气感与日常光影"
    ),
    "日系复古昭和漫画": (
        "日系复古昭和漫画，手绘赛璐璐的旧动画色，偏黄的纸张与颗粒，"
        "粗线条与朴素阴影，复古字体与版式，怀旧的生活气息"
    ),

    # ---- 国风与华语漫画 ----
    "国风水墨": (
        "中国水墨与淡彩，宣纸纹理，毛笔笔触有干湿浓淡，墨色渗化，大量留白，"
        "线条写意，人物与景物以少胜多，雅致含蓄的东方构图"
    ),
    "国风工笔重彩": (
        "国风工笔重彩，细挺的勾线，层层罩染的矿物色，朱砂石青石绿与泥金，"
        "绢本肌理，服饰纹样精到，画面富丽而克制"
    ),
    "新国风彩漫": (
        "新国风彩色漫画，东方造型配现代叙事节奏，墨线与平涂结合，"
        "黛青、朱红与米白的国潮配色，云纹与水墨肌理，年轻化的古风人物"
    ),
    "仙侠国风彩漫": (
        "仙侠国风彩漫，飘动的长发与宽袖，云雾与灵光粒子，青碧与月白的冷色调，"
        "山峦宫阙的远景，人物轻盈、衣袂翻飞"
    ),
    "连环画白描": (
        "连环画白描，单色墨线勾勒，线条均匀挺秀，衣纹与建筑结构清楚，"
        "大量留白，泛黄纸本的旧印刷质感，人物神态含蓄"
    ),
    "港式武侠漫画": (
        "港式武侠漫画，浓墨重笔与飞白，强对比黑白与饱和套色，"
        "夸张的肌肉与爆发姿态，气劲与掌风的速度线，密集动作场面，硬朗江湖气"
    ),
    "港式犯罪电影漫画": (
        "港式犯罪电影漫画，冷蓝与霓虹紫的夜景，雨湿街巷与玻璃反光，"
        "粗黑轮廓与高对比块面，人物侧脸与风衣剪影，低角度压迫构图"
    ),

    # ---- 欧美漫画 ----
    "美式超级英雄漫画": (
        "美式超级英雄漫画，粗壮有力的墨线与交叉排线，Ben-Day 网点，"
        "饱和的红蓝黄，夸张的肌肉解剖与英雄式仰视，动态透视与爆炸冲击"
    ),
    "美式黑色电影漫画": (
        "美式黑色电影漫画，高对比黑白，百叶窗与街灯的硬光，浓重阴影吞掉半张脸，"
        "雨夜与烟雾，粗黑块面与细线并存，冷峻的犯罪氛围"
    ),
    "美式独立图像小说": (
        "美式独立图像小说，松散的手绘线，低饱和的灰绿与土黄，平面化色块，"
        "留白与安静空镜，人物造型朴素，文学性的叙事气质"
    ),
    "美式报纸连载漫画": (
        "美式报纸连载漫画，干净均匀的墨线，简单平涂的有限色，圆润角色造型，"
        "宽松留白与清晰字形，复古印刷网点，轻快的日常幽默"
    ),
    "欧美复古波普漫画": (
        "欧美复古波普漫画，Ben-Day 圆点与粗黑描边，正红明黄钴蓝的高饱和撞色，"
        "扁平色块，海报式强构成，复古套色错位"
    ),
    "欧美奇幻插画": (
        "欧美奇幻插画，古典油画式笔触，戏剧性侧光与体积感，浓郁的金棕与深蓝，"
        "繁复盔甲纹饰与远景城堡，史诗感构图"
    ),

    # ---- 欧洲漫画 ----
    "法比清线漫画": (
        "法比清线漫画，均匀等粗的清晰轮廓线，平涂的明快色块，几乎没有排线，"
        "精确透视与建筑细节，干净天空与留白，克制的清晰叙事"
    ),
    "法比经典漫画": (
        "法比经典漫画，精细的钢笔线稿与手工上色，沉稳的赭石与墨绿，"
        "繁复街景与室内陈设，人物造型扎实，旧欧洲的叙事厚度"
    ),
    "欧洲文艺图像小说": (
        "欧洲文艺图像小说，松弛的毛笔与蜡笔混合线条，克制的双色或三色配色，"
        "大面积留白与静物式构图，人物侧影与沉默空间，文学与记忆气质"
    ),

    # ---- 独立与实验 ----
    "独立铅笔素描漫画": (
        "独立铅笔素描漫画，石墨颗粒与擦痕，保留松散的起稿线，灰阶丰富，"
        "纸面粗糙，人物带速写感，像手工装订的独立小志"
    ),
    "木刻版画漫画": (
        "木刻版画漫画，刀刻的锐利边缘与木纹，纯黑与纸白的强对比，"
        "粗粝的排刀痕，造型高度概括，手工印刷的墨色厚薄"
    ),
    "炭笔速写漫画": (
        "炭笔速写漫画，粗犷的炭条笔触与手抹灰雾，强烈的黑白关系，"
        "边缘松动，纸纹明显，人物动态迅速抓取，现场感与温度"
    ),
    "像素风漫画": (
        "像素风漫画，低分辨率的方形色块，有限的复古调色板，清晰的像素轮廓，"
        "16 位游戏画面感，硬边阴影与抖动渐变"
    ),
    "剪纸纸雕漫画": (
        "剪纸纸雕漫画，多层纸片叠出的轮廓，纸张纤维与投影，硬边色块与镂空，"
        "平面化的层叠空间，温暖的手工质感"
    ),

    # ---- 美术媒介 ----
    "厚涂油画": (
        "厚涂油画/丙烯，笔触与刮刀痕迹清楚，颜料有堆叠厚度，边缘松动，"
        "色彩层次丰富，光源明确，画面有手绘的粗粝与温度"
    ),
    "儿童绘本水彩": (
        "儿童绘本水彩插画，柔和铅笔轮廓，水彩晕染与纸纹，温暖明亮的低饱和配色，"
        "造型圆润概括，画面安静、亲切、有手作感"
    ),
    "3D 黏土动画": (
        "黏土与毛毡的定格动画质感，手作材质与指纹，圆润体积，柔和棚拍光，"
        "微缩布景，浅景深，颜色温暖，像停格动画的一帧"
    ),
    "复古丝网印刷": (
        "复古丝网印刷/孔版海报，有限套色，粗颗粒与套色错位，硬边色块，"
        "纸张纤维，颜色偏旧，版画式概括与强图形感"
    ),
    "黑白墨线漫画": (
        "黑白墨线漫画，粗细变化的钢笔线，密集排线与网点，大面积黑白对比，"
        "人物轮廓清楚，阴影用排线而不是渐变，纸面有墨迹与颗粒"
    ),
    "极简几何插画": (
        "极简几何插画，扁平色块，少量线条与形状，强构成与留白，"
        "低饱和或双色配色，人物与场景高度概括，画面现代、干净、克制"
    ),

    # ---- 韩漫与条漫（全彩条漫传统，只是内置预设里的一组）----
    "彩色韩漫风格": (
        "高质量彩色韩漫条漫插画，2D手绘，干净利落的黑色线稿，赛璐璐上色配柔和渐变，"
        "五官精致的二次元插画，电影感构图，色彩通透，"
        "细腻的布料褶皱与发丝高光，背景有层次和空气感"
    ),
    "现代都市韩漫": (
        "现代都市彩色韩漫条漫插画，2D手绘线稿，赛璐璐上色，都市生活质感，"
        "冷灰蓝与暖橙对比色，玻璃与金属反光，电影感夜景，二次元五官，插画质感"
    ),
    "古风韩漫": (
        "彩色古风韩漫条漫插画，2D手绘，精致考究的古代服饰纹样，水墨与淡彩结合的柔光，"
        "古建筑与器物的细节，雅致低饱和配色，二次元五官，插画质感"
    ),
    "奇幻韩漫": (
        "彩色奇幻韩漫条漫插画，2D手绘线稿，华丽魔法光效与粒子，戏剧性逆光，"
        "浓郁饱和的色彩，史诗感大场面构图，二次元五官，插画质感"
    ),
    "校园韩漫": (
        "彩色校园韩漫条漫插画，2D手绘，清爽明亮的色调，青春气息，柔和的窗边阳光，"
        "干净利落的线条，二次元五官，插画质感"
    ),
    "悬疑暗调韩漫": (
        "彩色悬疑暗调韩漫条漫插画，2D手绘，高对比明暗，冷蓝灰主色调，"
        "大面积阴影与局部冷光，压迫感构图，二次元五官，插画质感"
    ),
    "治愈温馨韩漫": (
        "彩色治愈温馨韩漫条漫插画，2D手绘，温暖色调，柔和散射光，细腻柔美的线条，"
        "舒适放松的氛围，二次元五官，插画质感"
    ),
}

# 所有画风共用的画质锚点，追加在每张分镜提示词最前面。
# 注意不要写"分镜/一格/漫画格"这类词：实测模型会把它理解成"画一张带边框的漫画页"，
# 于是自动加白边、黑框，甚至切成两格。
IMAGE_STYLE_ANCHOR: str = (
    "单幅完整插画，构图与景深明确，材质与光线统一，细节丰富，画面干净"
)

# 每个分镜提示词的最后一道质量约束（图像模型侧）。
# 注意：**不提"气泡/对话框"**——不需要气泡的格子提了反而会多画一个。
# 只用正面描述，不写"不要文字/不要边框"之类的否定。
IMAGE_QUALITY_SUFFIX: str = (
    "画面满版出血到四边，构图完整占满画布，人物比例与透视正确，"
    "背景与人物是同一套画风，整张图是一幅干净的完成稿插画"
)

# -----------------------------------------------------------------------------
# 气泡：由图像模型在出图时画出来，程序只负责"找回气泡 + 填字"
# -----------------------------------------------------------------------------
# ⚠️ 2026-10-21 按需求改回：气泡必须是**画面构图的一部分**。
# 出图提示词会逐个给气泡的位置/尺寸/形状/尾巴方向（见 IMAGE_BUBBLE_SUFFIX），
# 出图后 detect_blank_bubbles() 在画面里精确定位这些气泡，再按气泡的实际
# 白色区域填字。程序只在"一个气泡都没找到"时才兜底画气泡。
# 是否让图像模型按坐标画空白气泡（出图后程序只填字）。
# 注意：使用 style_color（ColorTransfer）工作流时会自动改为程序画气泡——
# 色彩迁移会把白色气泡一起染色，检测阈值会失效（见 pipeline.generate_one_shot）。
IMAGE_DRAW_BUBBLES: bool = True

# 是否允许"找回模型气泡并填字"（开 = 正式链路）。
IMAGE_FILL_MODEL_BUBBLES: bool = True

# 模型多画的气泡：用邻近背景补掉（尽力而为，复杂背景会留淡块）。
# 同时会在自检里报 bubble_count_mismatch，触发"只画 N 个气泡"的返修。
CLEANUP_EXTRA_BALLOONS: bool = True
STRAY_BALLOON_COVER: bool = True

# 气泡匹配阈值：计划框与检测框的 IoU / 中心距离 / 侧别要求
BUBBLE_MATCH_MIN_IOU: float = 0.05
BUBBLE_MATCH_MAX_DIST: float = 0.55
BUBBLE_MATCH_REQUIRE_SIDE: bool = True
# 检测不到时允许程序兜底画气泡（只在"整张图一个都没找到"时启用）
BUBBLE_ALLOW_PROGRAM_FALLBACK: bool = True

# 预留区（程序兜底/旁白条用；模型画气泡时也会按这个范围给坐标）
BUBBLE_RESERVE_TOP_RATIO: float = 0.045
BUBBLE_RESERVE_MAX_RATIO: float = 0.42
# 分格页里是否还向模型要"顶部干净背景"。
# 默认关：实测窄长格要求留白时，模型会把上半格直接刷成空地，画面重心被抽走；
# 程序气泡本身不大，直接压在墙面/天空上更划算。
BUBBLE_RESERVE_PAGE_ENABLED: bool = False
# 空白气泡的提示词。**必须写清楚**：数量、每个的槽位、外接框百分比、
# 宽高、形状、尾巴朝向、内部纯白无字。越具体，检测越准。
IMAGE_BUBBLE_SUFFIX: str = (
    "画面中画出 {count} 个空白对话气泡，只有这 {count} 个，气泡之间互不重叠、不贴画面边缘；"
    "气泡是纯白底、3 像素黑色描边、内部完整留白不出现任何文字或图案；"
    "每个气泡带一个细小的尾巴（画外音气泡除外），尾巴指向说话人；"
    "气泡落在干净的浅色背景上，位置避开人物脸部与眼睛"
)

# 不画气泡时改为"预留干净背景"（备用模式，IMAGE_DRAW_BUBBLES=False 时用）
IMAGE_BUBBLE_RESERVE_PROMPT: str = (
    "画面从上到下约 {top}% 到 {bottom}% 的区域保持干净的浅色背景"
    "（墙面、天空、虚化的远景或空地面），这一带不安排人物脸部、手部与关键道具，"
    "构图留出完整的横向空间"
)
# 有气泡时的质量约束（沿用"按位置画气泡"，只用于旧模式）
IMAGE_QUALITY_SUFFIX_WITH_BUBBLES: str = (
    "画面满版出血到四边，构图完整占满画布，人物比例与透视正确，"
    "背景与人物是同一套画风；按上面的位置画出空白对话气泡，气泡内部纯白留空"
)
# 无角色镜头（屏幕/物件/空镜）专用。
# 注意：**不能写"不要出现人物/人脸"**——那等于把"人物""人脸"喂给模型，
# 实测空镜就是这么凭空冒出一个人的。改成正面描述"画面里只有物件与空间"。
IMAGE_QUALITY_SUFFIX_NO_CHARACTER: str = (
    "画面主体是环境、屏幕或桌面上的物件本身，画面里只有物件与它所在的空间；"
    "构图干净，透视正确，背景与整体画风统一"
)

# 镜头类型（分镜可选值）与其构图提示词。
# 注意：这组是"有人物"时用的，里面带"角色/人物"字样。
# 纯物件/空镜格必须用下面的 SHOT_TYPE_PROMPT_MAP_OBJECT，
# 否则"特写镜头，角色面部占据画面大部分"会把一张空镜变成大脸特写。
SHOT_TYPES: list[str] = ["远景", "全景", "中景", "近景", "特写"]
SHOT_TYPE_PROMPT_MAP: dict[str, str] = {
    "远景": "远景镜头，广阔视野，人物在环境中较小，强调场景与空间关系",
    "全景": "全景镜头，完整展现人物全身与所在环境",
    "中景": "中景镜头，取人物腰部以上，兼顾人物动作与环境",
    "近景": "近景镜头，取人物胸部以上，突出表情与手部动作",
    "特写": "特写镜头，角色面部占据画面大部分，强调情绪细节",
}

# 纯物件 / 屏幕 / 空镜格用的镜头提示词（不含任何人物字样）
SHOT_TYPE_PROMPT_MAP_OBJECT: dict[str, str] = {
    "远景": "远景，广阔视野，强调环境与空间关系",
    "全景": "全景，完整展现空间与主体物件",
    "中景": "中景，取主体物件与它周围的环境",
    "近景": "近景，贴近主体物件，突出表面细节",
    "特写": "微距特写，主体物件占据画面大部分，强调质感与细节",
}

# 情绪氛围可选值
MOOD_OPTIONS: list[str] = [
    "紧张", "温馨", "悲伤", "搞笑", "愤怒", "平静", "悬疑", "激动", "压抑", "浪漫",
]

# 条漫构图指令：单幅竖构图、满版出血。
# 注意用词：**不要**写"条漫分镜/分格"之类字眼，实测会被出图模型理解成
# "要在一张图里排几格"，于是自动切出多条并排竖画面，成品报废。
# 用"主体"而不是"人物"：同一句话要同时适用于有人物和纯物件/空镜的格子。
WEBTOON_COMPOSITION_PROMPT: str = (
    "单幅竖向构图：整张图是一个画面、一层景、一个镜头，"
    "竖向长方形取景，主体与场景按垂直方向排列，"
    "上方留出天空或环境顶部，下方延伸到主体下部或地面，"
    "满版出血，构图饱满，具有电影镜头的纵深感"
)

# 条漫收尾约束（目前未接入 shot_image_prompt，保留给需要"再加一记强调"的场合）。
# 同样只写正面描述。
WEBTOON_TAIL_PROMPT: str = (
    "整张图是一个连续画面：一个镜头、一层景、单幅构图，"
    "画面满版出血到四边，人物比例正确，画风统一"
)

# 姿态总图生成时的固定附加指令。
# 一张图里同时给人看：正面/侧面/背面 + 行走/回眸/坐姿等，用于姿态库与人工检查。
CHARACTER_POSE_INSTRUCTION: str = (
    "角色姿态总图：同一角色，白色纯色背景，多人姿态总览，"
    "上方并排三个全身标准姿态（正面、侧面、背面），"
    "下方若干全身动作（行走、回眸、侧身站立、坐姿、抬手、低头），"
    "再下一排为表情头像（平静、警觉、疲惫、侧目）。"
    "所有姿态必须是同一张脸、同一发型、同一套服装、同一身高比例；"
    "【发型锁定】严格按身份卡的发型、发长、发色执行；身份卡没有写的马尾、发髻、"
    "发饰不要自行添加，也不要把短发画成长发；"
    "【眼睛锁定】双眼对称、瞳孔居中、看向同一方向。"
    "全身完整入镜，脚部不裁切，人物之间留白清楚，画面里只有这一个角色的多个姿态。"
)

# 主视图生成时的固定附加指令（唯一身份参考，必须最清晰）
MAIN_VISUAL_INSTRUCTION: str = (
    "角色主视图：该角色的全身正面站姿标准形象，头顶到脚底完整入镜，脚部不裁切，"
    "双脚自然分开与肩同宽，双臂自然下垂，表情中性偏冷静，正面平视镜头。"
    "【五官锁定】脸型、眼型、双眼间距、鼻梁、唇形与身份卡逐项一致；"
    "瞳孔居中、看向镜头；左右耳饰位置正确。"
    "【发型锁定】严格按身份卡的发型、发长、发色、发际线执行；"
    "身份卡没有写的马尾、发髻、发饰不要自行添加，也不要把短发画成长发。"
    "造型锁定：全身只穿同一套服装，外层只有一件，面料厚实不透明，"
    "衣领、袖口、下摆、腰带结构清楚；不要添加第二件外套、围巾、帽子或包。"
    "背景为纯白或极浅中性灰，画面里只有这个角色；"
    "精细线稿、干净平涂、柔和高光、布料褶皱与发丝细节清楚，"
    "人物比例准确，头身比稳定。"
)

# 旧三视图指令仅保留给旧工程读取；新流程不再调用。
TRIPLE_VIEW_INSTRUCTION: str = (
    "角色设定三视图：同一角色从左到右并排展示正面、侧面、背面三个角度的全身像。"
    "三个角度的角色完全一致（脸型、发型、发色、服装、配饰、身高比例、站姿统一），"
    "同一站姿、同一光影，三个全身像完整入镜、脚部完整。"
    "造型锁定：三个角度都只穿同一套服装，外层只有一件，面料厚实不透明，"
    "衣领、袖口与下摆结构清楚；背面视图也是同一件外层服装的背面结构"
    "（同色、同长度、同下摆），不能替换成另一件长外套。"
    "背景为简洁中性纯色，画面里只有这三个角色形象。"
)

# 三视图切分：是否自动把一张三视图切成三张单视图
AUTO_SPLIT_TRIPLE_VIEW: bool = True

# 切分比例（正面、侧面、背面各占宽度比例），总和必须为 1.0
TRIPLE_VIEW_SPLIT_RATIOS: tuple[float, float, float] = (1 / 3, 1 / 3, 1 / 3)

# 三视图自动切分时，在按比例均分的附近搜索最佳分割线的横向像素范围
SPLIT_SEARCH_WINDOW: int = 60


# =============================================================================
# 四-2、绘制风格库（嵌入 handraw-style）
# =============================================================================
#
# handraw-style 是一套"编号化画风资产库"：365 种视觉风格（FA-001 … FI-034，旧编号
# 001–365 会自动映射）、36 种主题色（C-01–C-36）、16 种影调（T-01–T-16）、
# 167 种排版图型（其中 SB-001–SB-071 是漫画分镜版式）。
#
# 本项目的用法：
#   1. 阶段②分析完成后，按故事气质自动推荐一组【风格 + 主题色】，用户可改；
#   2. 角色主视觉/三视图、每格分镜、整页版式共用同一段风格提示词，保证全书统一；
#   3. 分镜页按 SB-* 版式拼版，版式由分镜内容分析结果自主选择（见四-3）。
#
# 目录结构（只读，不要改 handraw-style 内部文件）：
#   handraw-style/skills/handdraw-style-prompter/references/styles.json
#   handraw-style/skills/handdraw-style-prompter/references/colors.json
#   handraw-style/skills/handdraw-style-prompter/references/tones.json
#   handraw-style/skills/handdraw-style-prompter/references/layouts.json
#   handraw-style/images/individual/<组>/<风格编号>.webp   ← 风格参考图
#   handraw-style/images/layouts/comic-storyboards/          ← 版式缩略图

HANDRAW_ROOT: Path = BASE_DIR / "handraw-style"
HANDRAW_REF_DIR: Path = HANDRAW_ROOT / "skills" / "handdraw-style-prompter" / "references"
HANDRAW_IMAGE_DIR: Path = HANDRAW_ROOT / "images"

# 总开关：关掉后完全回到内置画风预设（STYLE_PRESETS，47 种 / 8 个家族）
HANDRAW_ENABLED: bool = True

# 分析完成后自动推荐风格/主题色（用户仍可在阶段②手动改）
HANDRAW_STYLE_AUTO: bool = True
# 是否让文本模型参与"选风格"（关掉只走本地关键词打分，更快但没那么准）
HANDRAW_STYLE_REC_LLM: bool = True
# 送给文本模型挑选的候选风格数量（从 365 种里先按关键词收敛，再让模型定夺）
HANDRAW_STYLE_SHORTLIST: int = 28
# 风格参考图：默认用于 style_color 工作流的色彩迁移；纯文生图会自动跳过
HANDRAW_STYLE_REF_ENABLED: bool = True
HANDRAW_STYLE_REF_MAX_EDGE: int = 768
# 核心风格特征最多取多少字（过长会挤掉人数/身份卡，反而画不准）
HANDRAW_STYLE_TRAITS_MAX_CHARS: int = 150

# 默认风格（留空 = 每次由内容分析自动推荐；填 FA-001 / 旧编号 001 都认）
HANDRAW_DEFAULT_STYLE: str = ""
# 默认主题色（可写 "C-01" 或 "C-01 + C-12"；留空 = 自动搭配）
HANDRAW_DEFAULT_COLORS: str = ""
# 图片提示词里是否写风格名/作者名。
# Z-Image-Turbo 会把 "…Cinema / …Illustration" 这类标题式名字当画面文字画上去
# （实测 FI-030 会在底部排出片名），所以默认只写"视觉语言 + 主题色"。
HANDRAW_PROMPT_INCLUDE_NAME: bool = False
# 是否写"参考作者"（同样可能被画成签名/标题文字，默认关）
HANDRAW_PROMPT_INCLUDE_AUTHOR: bool = False
# 是否注入"漫画分级特征"（风格家族媒介 + traits 里真实出现的漫画特征词 + 阶调）
HANDRAW_PROMPT_TRIGGER: bool = True

# 创作工坊：先用文本模型把中文主题编译成 Qwen 能解析的观察式描述。
# 失败时自动回退到本地模板，不阻塞出图。
STUDIO_PROMPT_REWRITE: bool = True
STUDIO_REWRITE_TEMPERATURE: float = 0.3
STUDIO_REWRITE_MAX_TOKENS: int = 1200
STUDIO_INFOGRAPHIC_NODE_MAX: int = 5
# 阶调（T-01…T-16）：默认按故事气质自动选，填 T-03 则全篇锁定
HANDRAW_TONE_ENABLED: bool = True
HANDRAW_TONE_DEFAULT: str = ""
# 参考图数量上限（当前 Z-Image Omni 条件节点最多吃 3 张：1 张画风 + 2 张角色）
HANDRAW_MAX_REFERENCE_SLOTS: int = 3


# =============================================================================
# 四-3、分镜版式（漫画页 / 混排）
# =============================================================================
#
# 原来的条漫是"一格一张图、首尾竖拼"，现在多了一种形态：**一页多格**。
# 版式来自 handraw-style 的 SB-001–SB-071，另有三种"本项目原生"的条漫版式：
#
#   RENDER_MODE = "webtoon"  原条漫：一格一张图，竖屏无缝长条（旧行为，完整保留）
#   RENDER_MODE = "page"     分镜页：若干格拼成一页，版式由分镜内容自主选择
#   RENDER_MODE = "hybrid"   混排：情绪/冲击/转场格用原生条漫长格，信息密集处用分镜页
#
# 版式数据在 rules/data/storyboard_layouts.json（几何 + 适配度，可热改）。
RENDER_MODE: str = "hybrid"

# 页面画布宽度（像素）。合成页时用这个宽度，导出条漫时再等比缩到 WEBTOON_PANEL_WIDTH。
PAGE_CANVAS_WIDTH: int = 1536
# 版式没写页面比例时的默认值（宽/高）。0.72 ≈ 竖版漫画页。
PAGE_DEFAULT_RATIO: float = 0.72
# 页边距与格间距（像素，画布坐标）
PAGE_MARGIN: int = 16
PAGE_GUTTER: int = 12
PAGE_BG: tuple[int, int, int, int] = (246, 245, 241, 255)
PAGE_BORDER_ENABLED: bool = True
PAGE_BORDER_WIDTH: int = 4
PAGE_BORDER_COLOR: tuple[int, int, int, int] = (20, 20, 24, 255)
# 页面底部的页码/话数小字是否画出来
PAGE_NUMBER_ENABLED: bool = True
PAGE_NUMBER_FONT_RATIO: float = 0.018

# 生成每个格子的最小/最大边长（合成页时，格子太小会糊，太大浪费显存）
PAGE_PANEL_MIN_SIDE: int = 512
PAGE_PANEL_MAX_SIDE: int = 1280

# 每页格数范围（版式自带的格数超出这个范围时不会被选中）
PAGE_MIN_PANELS: int = 1
PAGE_MAX_PANELS: int = 6

# 是否让文本模型参与"分页 + 选版式"（关掉只走本地动态规划）
HANDRAW_LAYOUT_LLM: bool = True
# 本地动态规划里，同一版式连续复用的惩罚（0 = 不惩罚；越大越多样）
PAGE_LAYOUT_REPEAT_PENALTY: float = 0.22
# 每多编一页的固定代价（按"分数×格数"计分，避免全篇被拆成单格页）
PAGE_PLAN_PAGE_PENALTY: float = 0.90
# 一页内允许跨越的场景数（1 = 不跨场景；2 = 允许一次场景切换）
PAGE_MAX_SCENES_PER_PAGE: int = 2

# 页图输出目录与命名
PAGE_DIR: Path = DATA_DIR / "pages"
PAGE_FILENAME_TEMPLATE: str = "页_{index:03d}"

# 出图完成后是否立刻合成页面（关掉则只在导出/手动点"合成页面"时拼版）
PAGE_COMPOSE_ON_GENERATE: bool = True

# 字幕栏（SB-069/070/071 那种"上图下文"）：每格文字从 shot.caption 取，
# 没有时用旁白/画面描述首句兜底。字幕栏高度占该行高度的比例。
PAGE_CAPTION_ENABLED: bool = True
PAGE_CAPTION_RATIO: float = 0.20
PAGE_CAPTION_FONT_RATIO: float = 0.024
PAGE_CAPTION_NUMBER: bool = True
PAGE_CAPTION_PADDING: int = 12
PAGE_CAPTION_BG: tuple[int, int, int, int] = (250, 249, 245, 255)
PAGE_CAPTION_TEXT: tuple[int, int, int, int] = (26, 26, 32, 255)

# 分格画幅对应的构图指令（每格仍是独立单幅画面，不写"漫画格/分镜"字样）
PAGE_PANEL_COMPOSITION_WIDE: str = (
    "单幅横向宽幅构图：整张图是一个镜头、一层景，主体沿水平方向展开，"
    "左右留出环境空间，上下略微收紧，满版出血"
)
PAGE_PANEL_COMPOSITION_TALL: str = (
    "单幅竖向长构图：整张图是一个镜头、一层景，主体沿垂直方向排列，"
    "上方留出环境顶部，下方延伸到地面，满版出血"
)
PAGE_PANEL_COMPOSITION_SQUARE: str = (
    "单幅构图：整张图是一个镜头、一层景，主体居中偏上，环境完整铺满画布四边，"
    "画面直接满版出血"
)
# 这一格在页面里的视觉权重（只影响构图强度，不提"页/格"）
PANEL_EMPHASIS_PROMPTS: dict[str, str] = {
    "normal": "这一幅是常规画面：主体清楚，构图完整，环境交代到位",
    "main": "这一幅是段落重点画面：主体明确、层次分明，视觉重心集中",
    "splash": "这一幅是重点冲击画面：主体占据画面大部分，构图饱满，一眼抓住重点",
    "inset": "这一幅是局部小画面：取一个近景局部，背景简洁，主体突出",
}

# 分镜内容分析用的枚举（第四阶段与版式规划共用）
SHOT_BEATS: list[str] = [
    "建立", "对白", "反应", "动作", "情绪", "转场", "揭示", "蒙太奇",
    "日常", "幽默", "悬疑", "细节",
]
PACING_OPTIONS: list[str] = ["慢", "中", "快"]
EMPHASIS_OPTIONS: list[str] = ["normal", "main", "splash"]


# =============================================================================
# 五、对话气泡与文字叠加
# =============================================================================

# 是否默认开启气泡（需求 4.8：默认开启，输出带对话气泡的成品）
BUBBLE_ENABLED_BY_DEFAULT: bool = True

# 气泡样式：圆角矩形 / 椭圆 / 云朵 / 爆炸 / 方形 / 旁白框
BUBBLE_STYLES: list[str] = ["圆角矩形", "椭圆", "云朵", "爆炸", "方形", "旁白框"]

# 语气 -> 推荐气泡样式
TONE_TO_BUBBLE_STYLE: dict[str, str] = {
    "平静": "圆角矩形",
    "温馨": "圆角矩形",
    "浪漫": "圆角矩形",
    "悲伤": "云朵",
    "思考": "云朵",
    "搞笑": "爆炸",
    "激动": "爆炸",
    "愤怒": "爆炸",
    "紧张": "方形",
    "悬疑": "方形",
    "压抑": "方形",
    "旁白": "旁白框",
}

# 气泡默认摆放位置：bottom / top / auto。
# 新的排版引擎会优先使用导演层给出的槽位（BUBBLE_SLOTS），这里只是兜底。
BUBBLE_DEFAULT_POSITION: str = "top"

# 气泡小尾巴指向：auto（按气泡槽位与说话人朝向自动决定）/ down / up / none
BUBBLE_TAIL_DIRECTION: str = "auto"

# ---- 气泡绘制参数（快看条漫观感）----
# 白底 + 4px 近黑描边 + 非常轻的投影；文字用较重的黑体，字号按画面**宽度**取，
# 不再按高度（竖长格按高度算会忽大忽小）。
BUBBLE_FILL: tuple[int, int, int, int] = (255, 255, 255, 252)
BUBBLE_OUTLINE: tuple[int, int, int, int] = (24, 24, 30, 255)
BUBBLE_OUTLINE_WIDTH: int = 4
BUBBLE_TEXT_COLOR: tuple[int, int, int, int] = (18, 18, 22, 255)
BUBBLE_TAIL_ENABLED: bool = True
BUBBLE_TAIL_LENGTH: int = 54
BUBBLE_TAIL_WIDTH_RATIO: float = 0.075      # 尾巴根部宽度 = 气泡宽度 * 该比例（夹在 16~34px）
BUBBLE_MARGIN: int = 26
BUBBLE_PADDING_X: int = 30
BUBBLE_PADDING_Y: int = 22
BUBBLE_LINE_SPACING: int = 10
BUBBLE_LINE_SPACING_RATIO: float = 0.34     # 行距 = 字号 * 该比例
BUBBLE_GAP: int = 18
BUBBLE_CORNER_RADIUS: int = 26
BUBBLE_MAX_WIDTH_RATIO: float = 0.46        # 单个气泡最大宽度占画面比
# 气泡较多时改用较窄的气泡并排成两列，避免纵向堆满画面盖住人物
BUBBLE_MAX_WIDTH_RATIO_CROWDED: float = 0.38
BUBBLE_CROWDED_THRESHOLD: int = 3
BUBBLE_MIN_HEIGHT: int = 76
BUBBLE_MAX_COLUMNS: int = 2
# 字号 = 画面宽度 * 该比例（1088 宽 → 约 36px，手机上 100% 缩放可读）
BUBBLE_FONT_RATIO: float = 0.038
BUBBLE_FONT_MIN: int = 24
BUBBLE_FONT_MAX: int = 50
BUBBLE_SHADOW: bool = True
BUBBLE_SHADOW_OFFSET: int = 5
BUBBLE_SHADOW_ALPHA: int = 60
BUBBLE_SHADOW_BLUR: int = 7
# 气泡与画面边缘、旁白之间的最小安全距离
BUBBLE_SAFE_TOP_RATIO: float = 0.045

# ---- 出图前的气泡预留（空白气泡方案）----
# 生成图片时就把气泡位置占住，画成空白气泡；后期只填字，不再压住画面内容。
BUBBLE_PLAN_TOP_RATIO: float = 0.05          # 第一个气泡距画面顶部
BUBBLE_PLAN_MAX_WIDTH_RATIO: float = 0.44    # 预留气泡的最大宽度占比
BUBBLE_PLAN_MAX_HEIGHT_RATIO: float = 0.19   # 单个预留气泡的最大高度占比
BUBBLE_PLAN_MIN_HEIGHT_RATIO: float = 0.10   # 单个预留气泡的最小高度占比
BUBBLE_PLAN_GAP: int = 16                    # 预留气泡之间的间距（像素）

# 单条对话超过这么多字符时自动拆到下一个气泡（快看单气泡 ≤20 字）
BUBBLE_TEXT_SPLIT_THRESHOLD: int = 32
# 单行最多字符数（超过则换行）
BUBBLE_LINE_MAX_CHARS: int = 13

# 说话人标签是否显示在气泡内（如"林晚："）
BUBBLE_SHOW_SPEAKER: bool = False
# 群像/多人场面是否显示一个小名字标签（默认关闭，靠气泡槽位区分）
BUBBLE_SHOW_NAME_TAG: bool = False

# ---- 拟声词/音效艺术字 ----
SFX_ENABLED: bool = True
SFX_TEXT_COLOR: tuple[int, int, int, int] = (255, 226, 92, 255)
SFX_OUTLINE_COLOR: tuple[int, int, int, int] = (22, 22, 28, 255)
SFX_OUTLINE_WIDTH: int = 6
SFX_FONT_RATIO: float = 0.085
SFX_POSITION_RATIO: tuple[float, float] = (0.76, 0.14)
SFX_ROTATION_RANGE: float = 9.0              # 每条音效随机旋转 ±9°，避免呆板
SFX_MAX_PER_PANEL: int = 3

# ---- 旁白排版 ----
# caption = 快看式旁白条（浅米白底 + 细黑边 + 左侧色条，压在画面左上）；
# plain   = 旧版"白字 + 投影"直接排版。
# caption 的可读性远好于纯白字，尤其在复杂背景上，因此设为默认。
NARRATION_STYLE: str = "caption"
NARRATION_PLAIN: bool = False
NARRATION_TEXT_COLOR: tuple[int, int, int, int] = (22, 22, 26, 255)
NARRATION_SHADOW_COLOR: tuple[int, int, int, int] = (0, 0, 0, 160)
NARRATION_FILL: tuple[int, int, int, int] = (253, 250, 242, 240)
NARRATION_OUTLINE: tuple[int, int, int, int] = (26, 26, 32, 255)
NARRATION_ACCENT: tuple[int, int, int, int] = (196, 58, 54, 255)   # 左侧竖条
NARRATION_FONT_RATIO: float = 0.032             # 字号 = 画面宽度 * 该比例
NARRATION_FONT_MIN: int = 22
NARRATION_FONT_MAX: int = 42
NARRATION_MAX_WIDTH_RATIO: float = 0.52         # 文本块最大宽度占画面宽度比
NARRATION_TOP_RATIO: float = 0.035              # 第 1 条旁白距画面顶部的比例
NARRATION_STACK_STEP: float = 0.012             # 多条旁白之间的额外间距（占画面高度比例）
NARRATION_MAX_CHARS: int = 56                   # 单格旁白合并后最多保留多少字
NARRATION_MAX_LINES_PER_PANEL: int = 1          # 单格只排 1 条旁白（多条会自动合并）
NARRATION_LINE_SPACING_RATIO: float = 0.32      # 行距 = 字号 * 该比例
NARRATION_SHADOW_OFFSET_RATIO: float = 0.10     # plain 模式投影偏移 = 字号 * 该比例
NARRATION_PADDING_X: int = 26
NARRATION_PADDING_Y: int = 18
NARRATION_CORNER_RADIUS: int = 10
NARRATION_ACCENT_WIDTH: int = 7

# =============================================================================
# 五-2、分格边框与成品画布
# =============================================================================
#
# 快看的条漫不是"一张张图无缝贴在一起"，而是有清晰的分格边界与呼吸感的 gutter：
#   画面 → 3px 墨线边框 → 28px 纸色 gutter → 下一格
# 这样读者才能感到"这是一格"，而不是误以为图片没加载出来。
PANEL_BORDER_ENABLED: bool = True
PANEL_BORDER_COLOR: tuple[int, int, int, int] = (20, 20, 24, 255)
PANEL_BORDER_WIDTH: int = 3
PANEL_GUTTER: int = 28                       # 格与格之间的纸色间距
PANEL_BG: tuple[int, int, int, int] = (246, 245, 241, 255)   # 纸色底
PANEL_OUTER_MARGIN: int = 0                  # 格与条漫左右边缘的留白（0=满幅出血）

# 条漫顶部的话标题条（第 001 话 · 委托）与底部"本话完"尾页
EPISODE_HEADER_ENABLED: bool = True
EPISODE_HEADER_HEIGHT: int = 320
EPISODE_COVER_ENABLED: bool = True
EPISODE_COVER_SIZE: tuple[int, int] = (1088, 1600)
EPISODE_END_CARD_ENABLED: bool = True
EPISODE_END_SIZE: tuple[int, int] = (1088, 900)
# 尾页预告别写太长，一句钩子即可
EPISODE_NEXT_TEASER_MAX_CHARS: int = 40

# =============================================================================
# 六、字体
# =============================================================================

# 候选字体文件，按顺序取第一个存在的。用于气泡文字、艺术字、PDF 中文。
# 注意：Windows 自带的 HYZhongHeiTi-197.ttf 只有极少的字形（实测「测」会变豆腐块），
# 不能作为对白字体。这里统一用思源黑体 / 微软雅黑 / 黑体这类全字库字体。
FONT_CANDIDATES: list[str] = [
    r"C:\Windows\Fonts\msyhbd.ttc",             # 微软雅黑 Bold（对白默认，笔画够重）
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",      # 思源黑体（全字库，字形现代）
    r"C:\Windows\Fonts\msyh.ttc",               # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",             # 黑体
    r"C:\Windows\Fonts\Dengb.ttf",              # 等线 Bold
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

# 粗体候选（对白/旁白/标题用）
FONT_BOLD_CANDIDATES: list[str] = [
    r"C:\Windows\Fonts\msyhbd.ttc",             # 微软雅黑 Bold
    r"C:\Windows\Fonts\Dengb.ttf",              # 等线 Bold
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
]

# 拟声词字体：微软雅黑 Bold 优先（复杂汉字不会糊成一团），
# 华文琥珀只作为备选（"嗡/砰"这类字在粗描边下容易糊）。
FONT_SFX_CANDIDATES: list[str] = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\STHUPO.TTF",             # 华文琥珀
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
]

# 标题/封面字体：微软雅黑 Bold（现代悬疑/都市感），古风题材可在项目里改成行楷
FONT_TITLE_CANDIDATES: list[str] = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\STZHONGS.TTF",           # 华文中宋（更"出版物"）
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
]

# =============================================================================
# 七、输出与打包
# =============================================================================

# 输出文件夹默认名模板：漫画_日期时间（与有声书项目命名规则一致）
OUTPUT_NAME_TEMPLATE: str = "漫画_{timestamp}"
OUTPUT_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"

# 名称冲突时追加的序号格式
OUTPUT_NAME_SUFFIX_FORMAT: str = "_{index:02d}"

# 输出子目录结构
# 输出目录：按"输入 → 分析 → 角色 → 分镜 → 提示词 → 图 → 阅读 → 回溯 → 清单"编号
OUTPUT_SUBDIR_INPUT: str = "00_输入"          # 小说原文、工程信息
OUTPUT_SUBDIR_ANALYSIS: str = "01_分析"       # 分析结果、梗概、角色/场景清单
OUTPUT_SUBDIR_CHARACTERS: str = "02_角色"     # 人设卡、主视觉图、三视图
OUTPUT_SUBDIR_STORYBOARD: str = "03_分镜"     # 分镜 JSON / MD / 台词表
OUTPUT_SUBDIR_PROMPTS: str = "04_提示词"      # 逐格提示词、气泡计划、运行期回溯
OUTPUT_SUBDIR_SHOTS: str = "05_分镜图"        # 带气泡的成品
OUTPUT_SUBDIR_SHOTS_RAW: str = "05_分镜图/原图"  # 无气泡的纯画面
OUTPUT_SUBDIR_STRIP: str = "06_条漫"          # 条漫成品
OUTPUT_SUBDIR_READER: str = "07_阅读"         # PDF 与阅读文件
OUTPUT_SUBDIR_TRACE: str = "08_回溯"          # 每次调用的提示词原样
OUTPUT_SUBDIR_MANIFEST: str = "09_清单"       # manifest / README
OUTPUT_SUBDIR_DOCS: str = "03_分镜"           # 兼容旧代码：文档归到分镜目录

# 分镜图文件名模板（按序号命名）
SHOT_FILENAME_TEMPLATE: str = "分镜_{index:03d}"
# 角色图文件名模板
CHARACTER_MAIN_FILENAME: str = "主视图"
CHARACTER_POSE_FILENAME: str = "姿态图"
CHARACTER_TOP_FILENAME: str = "俯视图"
CHARACTER_TOP_SIZE: tuple[int, int] = (1024, 1024)
# 8GB / Z-Image 当前无法可靠生成真正的正上方顶视；默认只接受用户上传真实俯视参考
CHARACTER_TOP_AUTO: bool = False
CHARACTER_TOP_SKELETON: Path = BASE_DIR / "data" / "pose_library" / "top_down_skeleton.png"
# 默认俯视参考：用户放在 ComfyUI input 里的真实俯视图
CHARACTER_TOP_DEFAULT_REF: Path = Path(r"E:\Comfy-Desktop\ComfyUI-Shared\input\俯视图.png")
# Comfy Desktop 有时把 input 放在 Shared 根目录，两个位置都找
CHARACTER_TOP_DEFAULT_REFS: tuple[Path, ...] = (
    Path(r"E:\Comfy-Desktop\ComfyUI-Shared\input\俯视图.png"),
    Path(r"E:\Comfy-Desktop\ComfyUI-Shared\俯视图.png"),
    Path(r"E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\input\俯视图.png"),
)
CHARACTER_TOP_INSTRUCTION: str = (
    "角色俯视图：镜头在头顶正上方垂直向下（bird's-eye view, top-down view），"
    "只看到头顶的发旋、发缝、发髻与发饰、双肩和外套领口；"
    "头顶位于画面中心，脸被头顶遮住，看不到眼睛鼻子嘴；"
    "双臂向两侧展开，身体向下透视缩短，脚在最远处；"
    "同一角色、同一套服装、同一发型；"
    "白色纯色背景，画面里只有这一个角色；精细线稿和平涂，清晰面部与服装结构。"
)
# 旧工程兼容名（只读；新生成不再写）
CHARACTER_TRIPLE_FILENAME: str = "三视图"

# 输出图片格式与质量
OUTPUT_IMAGE_FORMAT: str = "PNG"
OUTPUT_JPEG_QUALITY: int = 95

# 缩略图边长（界面网格展示用）
THUMBNAIL_MAX_EDGE: int = 420

# 是否同时导出无气泡原图
EXPORT_RAW_SHOTS: bool = True

# 是否默认生成漫画阅读文件（PDF + 长条图）
GENERATE_READER_FILE: bool = True

# PDF 页面参数
PDF_PAGE_MODE: str = "single"        # single=一页一图；auto=按图片比例自适应页面
PDF_JPEG_QUALITY: int = 92

# 长条图参数（旧版：一格一张方图时的拼接）
LONG_STRIP_WIDTH: int = 900
LONG_STRIP_GAP: int = 24

# ---- 条漫长图（无缝竖拼，条漫模式的最终成品）----
# 统一按 WEBTOON_PANEL_WIDTH 宽度竖拼、格子之间零间距（无缝），
# 总高超过 WEBTOON_STRIP_MAX_HEIGHT 时自动切成 条漫_01.png / 条漫_02.png …
WEBTOON_STRIP_ENABLED: bool = True
WEBTOON_STRIP_MAX_HEIGHT: int = 8000
WEBTOON_STRIP_FILENAME_TEMPLATE: str = "条漫_{index:02d}"
# 条漫阅读 PDF：true=每个分块一页（便于手机阅读）；false=沿用一页一图
WEBTOON_PDF_BY_STRIP: bool = True

# 分镜描述文档格式：markdown / txt / both
STORYBOARD_DOC_FORMAT: str = "both"

# 打包压缩包文件名模板
ZIP_FILENAME_TEMPLATE: str = "{folder_name}.zip"

# 导出时是否把项目工程 JSON 一起打包（方便以后继续修改）
EXPORT_PROJECT_JSON: bool = True

# =============================================================================
# 八、界面与运行
# =============================================================================

APP_TITLE: str = "漫画生成器"
APP_SUBTITLE: str = "上传或粘贴小说 → 自动分析角色与场景 → 生成人设与三视图 → 拆解分镜 → 批量出图并叠加对话气泡"

# 五个阶段的标题（界面步骤指示器）
STAGE_TITLES: list[str] = [
    "① 输入文本",
    "② 内容分析",
    "③ 人设与三视图",
    "④ 分镜拆解",
    "⑤ 分镜出图与打包",
]

# 界面服务监听地址与端口（自建 Web 界面）
WEB_HOST: str = "127.0.0.1"
WEB_PORT: int = 7870
WEB_SHARE: bool = False          # 兼容旧配置项
WEB_OPEN_BROWSER: bool = True

# 静态前端目录
WEB_DIR: Path = BASE_DIR / "web"

# 前端轮询间隔（毫秒），写入页面供 JS 读取
POLL_INTERVAL_MS: int = 900

# 界面配色（默认「暮紫」主题；实际配色由前端主题系统在浏览器里覆盖）
THEME_PRIMARY_HUE: str = "#6C7CE7"       # 主色：柔和蓝紫
THEME_PRIMARY_HUE_DARK: str = "#4E5BD0"
THEME_SECONDARY_HUE: str = "#A9B4FF"     # 辅助色
THEME_BG: str = "#F2F4FB"                # 页面背景：极浅蓝紫
THEME_CARD_BG: str = "#FFFFFF"
THEME_TEXT: str = "#1B2032"
THEME_MUTED: str = "#7F879F"
THEME_BORDER: str = "#E4E8F4"
THEME_NEUTRAL_HUE: str = "#94A3B8"
THEME_RADIUS: str = "12px"

# 日志窗口最多保留行数
LOG_MAX_LINES: int = 800

# 生成失败时是否自动跳过并继续（需求 4.6）
SKIP_ON_ERROR: bool = True

# 生成每张图之间的间隔秒数（给显存留出回收时间，防 OOM）
INTER_SHOT_DELAY: float = 0.5

# 单次生成任务的最长运行时间（秒），防止卡死
GENERATION_HARD_TIMEOUT: int = 3600 * 4

# 是否启用请求缓存（同样的提示词与参考图直接复用上次结果）
CACHE_ENABLED: bool = True


# =============================================================================
# 九、文本读取
# =============================================================================

# 接受的文件扩展名
SUPPORTED_TEXT_EXTENSIONS: tuple[str, ...] = (
    ".txt", ".md", ".text", ".log", ".csv", ".json", ".xml", ".html", ".htm",
)

# 编码探测顺序（Python 内置），全部失败后交给 chardet 猜
ENCODING_CANDIDATES: tuple[str, ...] = (
    "utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16", "utf-16-le",
    "utf-16-be", "latin-1",
)

# 小说过短时的提示阈值（字符数）
MIN_NOVEL_LENGTH: int = 30

# 超长小说的处理上限（字符数）。超过则只分析前 N 字符，并在日志中提示。
MAX_NOVEL_LENGTH: int = 200_000

# =============================================================================
# 十、内置示例小说（"加载示例"按钮使用，包含角色、场景与对话）
# =============================================================================

SAMPLE_NOVEL: str = """\
雨夜的老宅

雨下得很大，敲在青瓦上噼啪作响。

林晚撑着一把断了一根伞骨的黑伞，站在城南那栋荒废了三十年的老宅门前。她今年二十四岁，身形清瘦，一头及腰的黑长直发被雨水打湿，贴在苍白的脸颊上。她穿着一件洗得发白的米色风衣，风衣口袋里露出半截泛黄的相片。

"就是这里了。"她低声说，声音几乎被雨声吞没。

大门虚掩着，铜环上落满灰尘。林晚伸手一推，门轴发出悠长的呻吟。院内荒草丛生，正屋的窗纸早已破损，风穿堂而过，卷起地上的落叶。

"谁？"一个沙哑的男声从正屋的阴影里传出来。

林晚脚步一顿。一个高瘦的男人从黑暗里走出来，他约莫三十岁上下，戴着一副圆框金丝眼镜，镜片后是一双极锐利的眼睛。他穿着深灰色的中山装，袖口磨得起了毛边，右手握着一盏老式煤油灯，左手垂在身侧，指节分明。

"你是……沈砚？"林晚的呼吸微微一滞。

"你认得我。"沈砚把煤油灯放在门边的石阶上，昏黄的光在两人之间铺开一小块暖色，"三年了，你还是找来了。"

"我姐的遗物，在你手里。"林晚从口袋里取出那半张相片，相片上是一个年轻女人抱着一个婴孩，笑容明亮，"林雨——她是我姐姐。"

沈砚的表情没有变化，镜片上映着灯火，"我知道。我从第一眼就知道你是她的妹妹，你和她长得很像，尤其是眼睛。"

雷声滚过屋顶，屋里的灰尘簌簌落下。

"进来吧。"沈砚侧身让开，"外面冷。有些事，站着说不完。"

林晚跨过那道高高的木门槛。正屋里只有一张八仙桌、两把椅子，墙角堆着十几个封着蜡的旧木箱。桌上摊着一本厚厚的笔记本，纸页上密密麻麻写满了字，边角画着一些奇怪的符号。

"这三年你一直住在这里？"林晚问。

"守着这些东西。"沈砚的手指划过笔记本的封面，"你姐姐死的那天晚上，把她最重要的东西交给了我。她说，等她的妹妹来找她的时候，就交给她。"

林晚的眼眶一下子红了，"她……她那时候就在等我来？"

"她一直在等。"沈砚的声音低下去，"可惜她没等到。"

雨声忽然更急了，风把半扇窗吹得哐当作响。沈砚走过去把窗关上，回身时，灯光正好照亮他半张脸，也照亮了桌角那张被压在笔记本下的旧报纸——报头写着"城南旧案"，日期是三年前的十月十七日。

林晚的目光定在那张报纸上，手指慢慢收紧。

"你想知道什么？"沈砚问。

"全部。"林晚说，"从头到尾，一个字都不要漏。"

沈砚沉默了很久，久到煤油灯的火苗都矮了下去。然后他拉开椅子坐下，示意她也坐。

"那我从那个冬天说起。"他说,"三年前的冬天，你姐姐来找我，说她发现了一件不该发现的事。"
"""


# =============================================================================
# 十一、配置辅助函数
# =============================================================================

def normalize_image_size(size) -> tuple[int, int]:
    """
    把请求尺寸对齐到服务端可接受的画幅。

    出图端（ComfyUI 的空 Latent 节点）要求：宽高为 IMAGE_DIMENSION_MULTIPLE 的倍数、
    单边不超过 IMAGE_MAX_SIDE、总像素不超过 IMAGE_MAX_PIXELS。不满足会直接报错，
    且 openai 兼容协议下只返回语义不明的 HTTP 500，所以出口统一在这里对齐。
    """
    try:
        w, h = int(size[0]), int(size[1])
    except Exception:  # noqa: BLE001
        return tuple(IMAGE_SIZE)  # type: ignore[return-value]
    m = max(1, int(IMAGE_DIMENSION_MULTIPLE))
    w = max(m, int(round(w / m)) * m)
    h = max(m, int(round(h / m)) * m)

    # 单边上限：等比缩小后再对齐
    longest = max(w, h)
    if longest > IMAGE_MAX_SIDE:
        k = IMAGE_MAX_SIDE / longest
        w = max(m, int(w * k / m) * m)
        h = max(m, int(h * k / m) * m)

    # 总像素上限：等比缩小后再对齐
    if w * h > IMAGE_MAX_PIXELS:
        k = (IMAGE_MAX_PIXELS / float(w * h)) ** 0.5
        w = max(m, int(w * k / m) * m)
        h = max(m, int(h * k / m) * m)
        while w * h > IMAGE_MAX_PIXELS and (w > m or h > m):
            if w >= h:
                w = max(m, w - m)
            else:
                h = max(m, h - m)
    return (w, h)


def webtoon_panel_size(shot_type: str = "",
                       width: int | None = None) -> tuple[int, int]:
    """
    按镜头类型取条漫画幅。

    宽度统一（默认 WEBTOON_PANEL_WIDTH），高度按 WEBTOON_PANEL_SIZE_MAP 分档；
    未匹配到的镜头类型用 WEBTOON_PANEL_SIZE_DEFAULT。返回值已对齐服务端约束。
    """
    if not WEBTOON_MODE:
        return normalize_image_size(IMAGE_SIZE)
    w = int(width or WEBTOON_PANEL_WIDTH)
    st = (shot_type or "").strip()
    size = WEBTOON_PANEL_SIZE_MAP.get(st, WEBTOON_PANEL_SIZE_DEFAULT)
    # 保持统一宽度：只取分档高度，宽度用请求宽度
    return normalize_image_size((w, size[1]))


def _env_override(name: str, default):
    """读取环境变量覆盖项。环境变量名 = COMIC_ + 变量名。"""
    raw = os.environ.get(f"COMIC_{name}")
    if raw is None or raw == "":
        return default
    # 按默认值类型做转换
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on", "y")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    return raw


def _apply_env_overrides() -> None:
    """把环境变量覆盖应用到本模块的大写配置项上。"""
    g = globals()
    # 密钥不参与覆盖，避免被空的 COMIC_COMFY_API_KEY 冲掉
    skip = {"COMFY_API_KEY", "DEEPSEEK_API_KEY", "API_KEY", "PATH"}
    for name in list(g.keys()):
        if not name.isupper() or name.startswith("_") or name in skip:
            continue
        default = g[name]
        if isinstance(default, (bool, int, float, str)):
            g[name] = _env_override(name, default)


_apply_env_overrides()

# 环境变量覆盖后再把路径类配置统一转成 Path
BASE_DIR = Path(BASE_DIR)
DATA_DIR = Path(DATA_DIR)
UPLOAD_DIR = Path(UPLOAD_DIR)
PROJECT_DIR = Path(PROJECT_DIR)
CHARACTER_DIR = Path(CHARACTER_DIR)
SHOT_DIR = Path(SHOT_DIR)
CACHE_DIR = Path(CACHE_DIR)
LOG_DIR = Path(LOG_DIR)
OUTPUT_ROOT = Path(OUTPUT_ROOT)
ASSETS_DIR = Path(ASSETS_DIR)


def ensure_dirs() -> None:
    """创建所有需要的目录（幂等）。"""
    for d in AUTO_CREATE_DIRS:
        Path(d).mkdir(parents=True, exist_ok=True)
    # 这两项在文件后半段才定义，不能放进 AUTO_CREATE_DIRS（列表在 import 时就求值）
    for d in (PAGE_DIR, DATA_DIR / "cache" / "style_refs"):
        Path(d).mkdir(parents=True, exist_ok=True)


def now_stamp() -> str:
    """当前时间戳字符串，用于输出目录命名。"""
    return datetime.now().strftime(OUTPUT_TIMESTAMP_FORMAT)


def default_output_name() -> str:
    """默认输出文件夹名：漫画_日期时间。"""
    return OUTPUT_NAME_TEMPLATE.format(timestamp=now_stamp())


def resolve_output_dir(folder_name: str | None = None) -> Path:
    """
    计算最终的输出目录。

    - 名称为空时使用默认模板
    - 名称非法字符会被替换
    - 名称冲突时自动追加序号（需求 4.10）
    """
    import re

    name = (folder_name or "").strip() or default_output_name()
    # 去掉 Windows 非法字符
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    if not name:
        name = default_output_name()

    target = OUTPUT_ROOT / name
    if not target.exists():
        return target

    index = 1
    while True:
        candidate = OUTPUT_ROOT / (name + OUTPUT_NAME_SUFFIX_FORMAT.format(index=index))
        if not candidate.exists():
            return candidate
        index += 1


def get_font_path() -> str | None:
    """返回第一个存在的字体文件路径，找不到返回 None。"""
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def active_workflow_name(preset: str | None = None,
                         *, has_reference: bool = True) -> str:
    """
    按 COMFY_WORKFLOW_PRESET 选本次要用的工作流名。

    auto：有参考图时用 style_color（生成后做色彩迁移），否则回基础工作流。
    注意这里只返回"名字"，是否真的支持参考图由 comfyworkflow 读工作流后判断。
    """
    p = (preset or COMFY_WORKFLOW_PRESET or "auto").strip().lower()
    if p in ("base", "plain", "webtoon"):
        return COMFY_WORKFLOW_NAME
    if p in ("style_color", "stylecolor", "color"):
        return COMFY_WORKFLOW_STYLE_NAME
    if p in ("style_ref", "styleref", "omni"):
        return COMFY_WORKFLOW_STYLE_REF_NAME
    if p in ("style_img2img", "img2img"):
        return COMFY_WORKFLOW_STYLE_IMG2IMG_NAME
    # auto
    if has_reference and HANDRAW_ENABLED and HANDRAW_STYLE_REF_ENABLED:
        return COMFY_WORKFLOW_STYLE_NAME
    return COMFY_WORKFLOW_NAME


def render_mode() -> str:
    """当前渲染形态：webtoon / page / hybrid（非法值回退 hybrid）。"""
    mode = (RENDER_MODE or "hybrid").strip().lower()
    return mode if mode in ("webtoon", "page", "hybrid") else "hybrid"


def config_summary() -> dict:
    """给界面显示的配置摘要。"""
    return {
        "文本模型服务": f"{TEXT_API_HOST}（{TEXT_MODEL_DISPLAY} / {TEXT_MODEL}）",
        "文本密钥": text_api_key_status(),
        "图像生成服务": f"ComfyUI {COMFY_API_HOST}",
        "图像工作流": f"{COMFY_WORKFLOW_NAME} / {COMFY_WORKFLOW_STYLE_NAME}",
        "图像鉴权": api_key_status(),
        "分镜画幅": (
            f"条漫：宽 {WEBTOON_PANEL_WIDTH} 统一，高度按镜头分档 "
            + " / ".join(f"{k}{v[1]}" for k, v in WEBTOON_PANEL_SIZE_MAP.items())
        ) if WEBTOON_MODE else f"方图 {IMAGE_SIZE[0]}×{IMAGE_SIZE[1]}",
        "条漫长图分块": f"每块最高 {WEBTOON_STRIP_MAX_HEIGHT}px 自动切分",
        "旁白呈现": "快看式旁白条" if NARRATION_STYLE == "caption" else "白字投影",
        "气泡方案": "出图时预留空白气泡，后期只填字" if IMAGE_DRAW_BUBBLES
                    else "出图后自动排版气泡",
        "分格与拼版": f"墨线 {PANEL_BORDER_WIDTH}px + 纸色 gutter {PANEL_GUTTER}px"
                       f"；封面/话标题/尾页：{'开' if EPISODE_COVER_ENABLED else '关'}",
        "推理步数": IMAGE_STEPS,
        "默认画风": (f"handraw-style 风格库（{HANDRAW_DEFAULT_STYLE or '分析后自动推荐'}）"
                    if HANDRAW_ENABLED else DEFAULT_STYLE),
        "分镜形态": {"webtoon": "原条漫（一格一张）",
                     "page": "分镜页（SB-* 版式）",
                     "hybrid": "混排（条漫长格 + 分镜页）"}.get(render_mode(), render_mode()),
        "气泡默认": "开启" if BUBBLE_ENABLED_BY_DEFAULT else "关闭",
        "最多参考图": MAX_REFERENCE_IMAGES,
        "输出根目录": str(OUTPUT_ROOT),
        "数据目录": str(DATA_DIR),
        "字体": get_font_path() or "未找到中文字体（将使用内置位图字体）",
    }


if __name__ == "__main__":
    # 直接运行本文件可自检配置
    from pprint import pprint

    print("=" * 70)
    print("漫画生成器 —— 配置自检")
    print("=" * 70)
    pprint(config_summary())
    print("-" * 70)
    missing = [str(d) for d in AUTO_CREATE_DIRS if not Path(d).exists()]
    print("尚未创建的目录：", missing or "无")
    print("默认输出文件夹名：", default_output_name())
    print("示例小说字符数：", len(SAMPLE_NOVEL))
    print("风格预设：", "、".join(STYLE_PRESETS))
    print("镜头类型：", "、".join(SHOT_TYPES))
    print("气泡样式：", "、".join(BUBBLE_STYLES))
    print("=" * 70)










