# 模型与权重

跑通一条完整条漫，**最少需要 3 个权重（约 11.3 GB）**；全部 9 个约 **30.4 GB**。

权重本体**不随 Git 仓库分发**：GitHub 单文件上限 100 MB，这里最小的权重也有 317 MB，
全量 30.4 GB 既推不上去，也会让 `git clone` 变成几十 GB。仓库里放的是：
说明、机器可读清单、SHA256，以及**国内直连的下载脚本**（不需要翻墙）。

## 国内直连下载（推荐，不用代理）

```powershell
python scripts/download_models.py --list    # 先看要下什么、多大
python scripts/download_models.py           # 只下必需的 3 个（约 11.3 GB）
python scripts/download_models.py --all     # 下全部有镜像的 7 个
python scripts/download_models.py --file ae.safetensors
```

- 默认走 **ModelScope 魔搭**（阿里，国内直连），已实测可用，速度约 10 MB/s；
- 断点续传，下完自动比对字节数与 SHA256；
- 下载到 `models/weights/`，按 ComfyUI 的子目录结构放好；
- 9 个里有 7 个有镜像；`Z-Image ControlNet lite` 和 `birefnet` 暂未找到国内镜像，
  可以从 [hf-mirror.com](https://hf-mirror.com) 或用代理从 HF 下载（见文末说明）。

## 目录约定

```text
models/
├── README.md          # 本文件：用途、来源、许可
├── manifest.json      # 机器可读：文件 / 大小 / sha256 / 许可 / 来源 / 是否必需
└── weights/           # 权重本体（.gitignore，本地硬链接或复制）
    ├── diffusion_models/
    ├── text_encoders/
    ├── vae/
    ├── model_patches/
    └── background_removal/
```

Windows 下 `models/weights/` 里的文件是指向 ComfyUI 安装目录的**硬链接**：
在项目目录里看得到、用得着，但不额外占磁盘（同一分区）。换机器用
`scripts/link_models.ps1` 重建即可。

## 清单

| 用途 | 文件 | 大小 | 放到 ComfyUI 的 | 许可 | 必需 |
|---|---|---|---|---|---|
| Z-Image 主模型（6B / int8 / 8 步） | `z_image_turbo_int8_convrot.safetensors` | 5.78 GB | `diffusion_models/` | Apache-2.0 | ✅ |
| Z-Image 文本编码器（Qwen3-4B / fp8） | `qwen_3_4b_fp8_mixed.safetensors` | 5.25 GB | `text_encoders/` | Apache-2.0 | ✅ |
| Z-Image VAE | `ae.safetensors` | 0.31 GB | `vae/` | Apache-2.0 | ✅ |
| Qwen-Image-2.1 主模型（turbo / int8） | `qwen_image_2.1_turbo_int8_convrot.safetensors` | 6.76 GB | `diffusion_models/` | Apache-2.0 | 可选 |
| Qwen-Image 文本+视觉编码器（Qwen3-VL 8B） | `qwen3vl_8b_w4a8.safetensors` | 5.88 GB | `text_encoders/` | Apache-2.0 | 可选 |
| Qwen-Image VAE | `qwen_image_2.1_vae_bf16.safetensors` | 0.63 GB | `vae/` | Apache-2.0 | 可选 |
| Z-Image ControlNet Union（lite，8 步） | `Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2601-8steps.safetensors` | 1.88 GB | `model_patches/` | Apache-2.0 | 可选 |
| Qwen-Image ControlNet Union | `qwen_image_2.1_fun_controlnet_union_int8_convrot.safetensors` | 3.52 GB | `model_patches/` | Apache-2.0 | 可选 |
| BiRefNet 背景去除 | `birefnet.safetensors` | 0.41 GB | `background_removal/` | MIT | 可选 |

> 「可选」不是没用：Qwen 路线用于 25 格参考图分镜，ControlNet 用于上传姿态图，
> BiRefNet 用于创作工坊去背景。只跑 Z-Image 文生图条漫时，装前三个就够。

### 最小可跑（Z-Image 路线）

```text
z_image_turbo_int8_convrot.safetensors   5.78 GB
qwen_3_4b_fp8_mixed.safetensors          5.25 GB
ae.safetensors                           0.31 GB
─────────────────────────────────────────────
合计                                    11.34 GB
```

### 工作流里还提到了、但本机没有的（备选，不影响默认流程）

- `qwen_3_4b.safetensors`（未量化的文本编码器）
- `z_image_turbo_bf16.safetensors`（bf16 全精度主模型，显存要求更高）
- `Z-Image-Turbo-Fun-Controlnet-Union.safetensors`（旧版 ControlNet）

## 来源

| 模型 | 官方来源 |
|---|---|
| Z-Image / Z-Image-Turbo | <https://huggingface.co/Tongyi-MAI/Z-Image-Turbo>（魔搭同名） |
| Qwen-Image | <https://huggingface.co/Qwen/Qwen-Image>（魔搭同名） |
| Qwen3-4B | <https://huggingface.co/Qwen/Qwen3-4B> |
| Qwen3-VL-8B | <https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct> |
| BiRefNet | <https://github.com/ZhengPeng7/BiRefNet> |
| Fun-ControlNet 系列 | 通义 Fun-ControlNet，ComfyUI 社区量化包（搜 `fun-controlnet`） |

> **量化包的许可要注意**：本仓库用的是社区 int8 量化版，其许可标注不一定继承原模型的
> Apache-2.0。已有把 Z-Image 量化版误标成 `tongyi-qianwen-license` 的案例。
> 商用请使用官方权重，或确认量化仓库明确继承 Apache-2.0。

## 校验

每个文件的字节数与 SHA256 都写在 [`manifest.json`](manifest.json) 里。

```powershell
# 单个文件
Get-FileHash models\weights\vae\ae.safetensors -Algorithm SHA256
# 与 manifest.json 里对应条目的 sha256 比对
```

## 换机器 / 新环境怎么恢复

**方式一：从已有 ComfyUI 安装链接过来（推荐，不重复下载）**

```powershell
# 默认从 E:\Comfy-Desktop\ComfyUI-Shared\models 找；改成你的路径
powershell -ExecutionPolicy Bypass -File scripts\link_models.ps1 `
  -ComfyModels "D:\ComfyUI\models"

# 不同分区没法建硬链接时，加 -Copy 复制（会真的占磁盘）
powershell -ExecutionPolicy Bypass -File scripts\link_models.ps1 -Copy
```

**方式二：自己下载**

按上表的目录放到 ComfyUI 的 `models/` 下，文件名必须**完全一致**，
否则 ComfyUI 会报 `Value not in list`。放好后打开界面
「设置 → 图像工作流 → 检查工作流」，会逐个核对。

## 许可

- 模型权重遵循各自许可（上表），**不随本仓库分发**。
- 本项目代码 Apache-2.0，见 [../LICENSE](../LICENSE)；第三方说明见 [../THIRD_PARTY.md](../THIRD_PARTY.md)。
- 用模型生成的内容，责任在使用者；请确保输入素材你有权使用。
