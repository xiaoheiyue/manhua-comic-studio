# ComfyUI 工作流备份

这里放的是本项目**自带的工作流副本**，只在连不上 ComfyUI（或 ComfyUI 里没有
同名工作流）时兜底用。

## 命名规则

文件名 = 工作流名称 + `.json`，要和 `config.py` 里的名称对上：

| 文件 | 配置项 | 用途 |
| --- | --- | --- |
| `image_z_image_turbo_int8.json` | `COMFY_WORKFLOW_NAME` | 基础文生图（纯文生图，无参考图） |
| `image_z_image_turbo_int8_style_color.json` | `COMFY_WORKFLOW_STYLE_NAME` | **画风色彩迁移**（默认风格链路，1 张画风参考，只迁色） |
| `image_z_image_turbo_int8_style_ref.json` | `COMFY_WORKFLOW_STYLE_REF_NAME` | **实验性**：Z-Image Omni 参考图条件（1 画风 + 2 角色） |
| `image_z_image_turbo_int8_style_img2img.json` | `COMFY_WORKFLOW_STYLE_IMG2IMG_NAME` | 参考图 img2img 兜底（会带构图） |
| `image_z_image_turbo_int8_character_pose.json` | `COMFY_WORKFLOW_CHARACTER_POSE_NAME` | 角色姿态总图 / 本格自定义姿态（Pose ControlNet） |

`COMFY_WORKFLOW_PRESET` 决定用哪份：

```python
COMFY_WORKFLOW_PRESET = "auto"
# auto：本次有画风参考图（选了 handraw 风格）时用 style_color，否则用 base
# base / style_color / style_ref / style_img2img：强制指定
```

## 两种格式都认

| 格式 | 长什么样 | 说明 |
| --- | --- | --- |
| API 格式（本目录自带的是这种） | `{"57:3": {"class_type": "KSampler", "inputs": {...}}}` | ComfyUI 菜单「工作流 → 导出（API）」得到，可直接提交 `/prompt` |
| 界面格式 | `{"nodes": [...], "links": [...], "definitions": {...}}` | Ctrl+S 存下来的那份，程序会自动拍平（含子图）成 API 格式 |

## 读取顺序

1. `config.COMFY_WORKFLOW_FILE`：显式指定的文件
2. ComfyUI 里的同名工作流（`/api/userdata/workflows/<名称>.json`）——**优先用这个**，
   所以你在 ComfyUI 里改工作流会直接生效
3. 本目录的同名文件
4. 代码内置的 Z-Image-Turbo 模板（`comicgen/comfyworkflow.py::inline_template`）

## 五份工作流的节点结构

### 1）基础文生图 `image_z_image_turbo_int8`

```
CLIPLoader(qwen_3_4b_fp8_mixed, lumina2) ─┐
UNETLoader(z_image_turbo_int8_convrot) ── ModelSamplingAuraFlow(shift=3) ─┐
VAELoader(ae) ────────────────────────────────────────────────────────────┤
CLIPTextEncode(正向) ─────────────── positive ─┐
CLIPTextEncode(负面提示词) ───────── negative ─┤
EmptySD3LatentImage(1088×1216) ─── latent ──┤ KSampler(8 步 / cfg=1.4 /   │
                                            └ res_multistep + simple) ─────┘
                                                     │
                                                VAEDecode ── SaveImage(manhua/)
```

### 1.5）角色姿态总图 `image_z_image_turbo_int8_character_pose`

```text
LoadImage(pose_sheet.png / 用户骨架) → ImageScale(1536²)
ModelPatchLoader(ControlNet 2.1 lite) → QwenImageDiffsynthControlnet
UNETLoader → ModelSamplingAuraFlow(shift=3) → ControlNet
CLIPLoader → TextEncodeZImageOmni（无参考图时等价文本编码）
EmptySD3LatentImage(1536²) → KSampler(8步/cfg1.4) → VAEDecode → SaveImage
```

- 姿态骨架是固定 ControlNet 输入，不计入参考图槽位；
- 不要把角色参考图接进 Omni/ReferenceLatent：与 ControlNet patch 的 batch 会冲突；
- 角色姿态总图、本格自定义姿态都用这个工作流；
- 主视图从姿态总图正面裁出，不再单独文生图。

### 2）画风色彩迁移 `image_z_image_turbo_int8_style_color`（默认）

```
CLIPLoader(qwen_3_4b_fp8_mixed, lumina2) ─┐
UNETLoader(z_image_turbo_int8_convrot) ── ModelSamplingAuraFlow(shift=3) ─┐
VAELoader(ae) ────────────────────────────────────────────────────────────┤
CLIPTextEncode(正向) ─ positive ┐                                      │
CLIPTextEncode(负面提示词) ─ negative ┤                                  │
EmptySD3LatentImage(画幅) ─ latent ┤ KSampler(8 步 / cfg=1.4) ──────────┘
                                                     │
LoadImage(参考图1_画风) ─────────────────────────────┤
                                                     ▼
                                          VAEDecode ─ ColorTransfer ─ SaveImage
                                          (method=reinhard_lab, strength=0.55)
```

- `ColorTransfer` 是 ComfyUI 内置节点（`image/filters`），把参考图的**色彩分布**
  迁到生成图上；只动色，不动内容/构图；
- 强度由 `config.COMFY_COLOR_TRANSFER_STRENGTH` 注入（默认 0.55，0=不迁，1=完全贴参考）；
- 参考图上传前会等比缩到 768 左右；**style_color 不填灰边**（灰边会污染
  ColorTransfer 的色彩统计），只有 Omni 才填 768×768 保证多图同尺寸；
- 色彩迁移会把空白气泡一起染色，所以用这份工作流时**气泡全部由程序画**
  （`model_bubbles=False`），不依赖模型按坐标画气泡。

### 3）实验性参考图条件 `image_z_image_turbo_int8_style_ref`

```
CLIPLoader(qwen_3_4b_fp8_mixed, lumina2) ─┐
VAELoader(ae) ────────────────────────────┤
UNETLoader(z_image_turbo_int8_convrot) ── ModelSamplingAuraFlow(shift=3) ─┐
                                                                          │
LoadImage(参考图1_画风) ─ image1 ┐                                        │
LoadImage(参考图2_角色) ─ image2 ┤ TextEncodeZImageOmni(clip, prompt, vae, │
LoadImage(参考图3_角色) ─ image3 ┘   auto_resize_images=true) ─ positive ─┤
                                                    │                     │
                          CLIPTextEncode(负面提示词) ─ negative ───────────┤
EmptySD3LatentImage(画幅) ─ latent ───────────────────────────────────────┤
                                       KSampler(8 步 / cfg=1.4) ───────────┘
                                                     │
                                                VAEDecode ── SaveImage(manhua_style/)
```

⚠️ **用基础 Z-Image Turbo 权重时，Omni 条件会连参考图的内容、构图、文字一起抄**
（实测：把三视图的三个小人排进画面、把参考图标题"Reza Farazmand"画在顶部）。
它适合装了 **Z-Image Omni / Z-Image Edit 权重**的环境；用基础权重请用
`style_color`。`auto` 预设**不会**选它。

### 4）参考图 img2img 兜底 `image_z_image_turbo_int8_style_img2img`

```
LoadImage(参考图1_画风) ─ ImageScale(1216×1216, center) ─ VAEEncode ─ latent ┐
CLIPTextEncode(prompt) ─ positive ────────────────────────────────────────────┤
CLIPTextEncode(负面提示词) ─ negative ────────────────────────────────────────┤
UNETLoader ─ ModelSamplingAuraFlow ───────────────────────────────────────────┤
                                          KSampler(8 步 / cfg=1.4 / denoise=0.62)
                                                     │
                                                VAEDecode ── SaveImage(manhua_img2img/)
```

- denoise 0.62 仍会保留参考图的构图/内容，只适合当色调兜底；
- 尺寸写在 `ImageScale` 上，程序会自动找到并写入本次画幅。

## 负面条件（negative）

五份工作流都新增了一个 `CLIPTextEncode`（`_meta.title = "负面提示词"`），
接到 `KSampler.negative`，原来的 `ConditioningZeroOut` 不再使用。

- 默认 `COMFY_CFG=1.4`：Z-Image-Turbo 官方推荐 cfg=1，此时 negative 被数学忽略；
  1.4 是"负向词开始生效、又不至于明显失真"的折中；
- 负向词由 `prompts.negative_prompt()` 按镜头/角色/三视图/背面/风格组合；
- 三视图负向词**不能**带"多格漫画/分格线"，否则会把并排三角度压掉；
- 背面负向词单独加"双人/正面人物/镜像/背面换大衣"；
- 关闭：`COMFY_NEGATIVE_ENABLED=False`；回官方画质：`COMFY_CFG=1.0`（负向词失效）。

> 如果 ComfyUI 里同名工作流还是旧版（没有 negative 节点），程序仍会读 ComfyUI 里
> 那份，负向词不会生效；用「设置 → 检查工作流」确认 `负面提示词` 支持。

## 程序会写入什么

只写**正向提示词、负向提示词、画幅、种子、文件名前缀、参考图文件名、ColorTransfer 强度、可选旧 LoRA（已停用，默认不传）**；
默认 `COMFY_APPLY_SAMPLER_SETTINGS=True`，会把 cfg 写成 `config.COMFY_CFG`（默认 1.4），
步数仍是工作流里的 8。`TextEncodeZImageOmni` 的正向提示词输入叫 `prompt`（不是 `text`），
负向提示词走独立的 `CLIPTextEncode` 节点（标题「负面提示词」），不再用 `ConditioningZeroOut`。
程序两种都认。

