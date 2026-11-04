# Qwen Image 2.1 Turbo 本地工作流

用于创作工坊的“参考图 → chibi 表情包”链路。

## 三个文件分别是什么

| 文件 | 作用 |
|---|---|
| `qwen_image_2.1_turbo_int8_convrot.safetensors` | 扩散主模型（UNET），真正画图/改图的模型，8 步 turbo |
| `qwen3vl_8b_w4a8.safetensors` | Qwen3-VL 8B 文本+视觉编码器（CLIPLoader `type=qwen_image`），负责读提示词和参考图 |
| `qwen_image_2.1_vae_bf16.safetensors` | VAE，负责图像 ↔ latent 编码解码 |

> 注意：`qwen3vl_8b_w4a8` 不是画图模型。  
> 它负责让模型“看懂参考图 + 提示词”；真正生成/编辑的是 `qwen_image_2.1_turbo_int8_convrot`。

## 最小可运行工作流

```text
UNETLoader(qwen_image_2.1_turbo_int8_convrot)
CLIPLoader(qwen3vl_8b_w4a8, type=qwen_image)
VAELoader(qwen_image_2.1_vae_bf16)
LoadImage(参考图)
        │
        ├── image_1 ──┐
        │             ▼
CLIP ──────────► TextEncodeQwenImage21
VAE ───────────►   prompt / negative_prompt
                  resolution=1024
                  images={image_1: LoadImage}
                       │
        positive / negative / latent
                       ▼
                  KSampler
                  steps=8, cfg=1.0
                  euler, simple, denoise=1.0
                       ▼
                  VAEDecode
                       ▼
                   SaveImage
```

节点连接：

```text
1 UNETLoader ───────────────► KSampler.model
2 CLIPLoader ───────────────► TextEncodeQwenImage21.clip
3 VAELoader ────────────────► TextEncodeQwenImage21.vae
                            └► VAEDecode.vae
4 LoadImage ────────────────► TextEncodeQwenImage21.images.image_1
5 TextEncodeQwenImage21 ────► KSampler.positive / negative / latent_image
6 KSampler ─────────────────► VAEDecode.samples
7 VAEDecode ────────────────► SaveImage.images
```

## 本项目的 API 工作流

文件：

```text
comfy_workflows/qwen_image_2.1_turbo_edit_local.json
```

这是 ComfyUI 的 **API 格式**，由 `comicgen/qwenedit.py` 直接提交；不是网页 UI 的 workflow 格式。

## 为什么不用官方模板直接跑

官方模板：

```text
.../templates/image_qwen_image_2_1_turbo_image_edit.json
```

还包含：

- `QwenImage21Cache`
- `ManualSigmas` 8 步 sigma 表
- `SamplerCustomAdvanced`
- `CFGGuider`
- Qwen3.5 9B 提示词增强器

其中提示词增强器需要额外的 `qwen3.5_9b_qwen_image_2.1_pe_i2i`，当前不是必需。

本项目用最小图：

```text
简单 KSampler + 8 steps + euler/simple
```

已经实测能保留：

- 白发
- 紫黑长袍
- 银色腰带
- 额间红纹
- 精灵耳

## 8GB 显存注意事项

三个文件加起来约：

```text
UNET 7.2GB + 文本编码器 6.3GB + VAE 0.7GB ≈ 14GB
```

8GB 显存不能同时常驻。

所以 `QwenEditClient.generate()` 第一次调用前会：

```text
POST /free {"unload_models": true, "free_memory": true}
```

先卸掉 Z-Image，再加载 Qwen。

之后同一批表情共用 Qwen，不会每格重复卸载。

## 输出尺寸

`TextEncodeQwenImage21` 会根据参考图生成 latent：

- 参考图 512×512 → 输出约 512×512；
- 参考图 768×768 → 输出约 768×768；
- `resolution` 控制参考图缩放，不强制输出方形。

创作工坊传 `size=512`，所以表情格是 512×512，再拼成 1024 网格。
