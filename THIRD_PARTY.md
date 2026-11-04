# 第三方依赖与许可

本仓库以 **Apache-2.0** 发布（见 [LICENSE](LICENSE)）。下面列出运行时会接触到的第三方作品、许可与义务。

## 一、随仓库分发的第三方内容

| 名称 | 用途 | 许可 | 义务 |
|---|---|---|---|
| [handraw-style](https://github.com/yang0/handraw-style) | 365 画风 / 170 版式 / 36 主题色 | MIT + 署名要求 | 保留版权声明；在文档、仓库、产品说明或界面中显著署名 yang0 与原仓库链接 |

> `handraw-style/` 目录**不随仓库提交**（体积约 400MB），用 `scripts/fetch_handraw_style.ps1` 拉取。
> 拉取后其自带 `LICENSE` 仍在该目录内；本项目另存一份到 `third_party/handraw-style-LICENSE.txt`，
> 并在 README 与界面「关于」中署名。

## 二、模型权重（不随仓库分发）

| 模型 | 用途 | 许可 | 说明 |
|---|---|---|---|
| Z-Image-Turbo | 文生图主力 | Apache-2.0 | 官方权重见 HF `Tongyi-MAI/Z-Image-Turbo` 或魔搭 |
| Qwen-Image-2.1 | 参考图 / 分镜出图 | Apache-2.0 | 官方权重见 HF `Qwen/Qwen-Image` |

注意：社区量化版（int8 / 4bit）的许可标注可能与原始权重不一致，下载前请看清仓库标注。
例如有案例把量化版误标为 `tongyi-qianwen-license`，而原模型是 Apache-2.0。
商用前建议只使用官方权重或明确继承 Apache-2.0 的量化版。

## 三、运行时依赖（Python 包，不随仓库分发）

| 包 | 许可 |
|---|---|
| fastapi / uvicorn / python-multipart | MIT / BSD |
| requests / chardet | Apache-2.0 / LGPL |
| pillow | HPND |
| numpy / scipy | BSD-3-Clause |
| mediapipe | Apache-2.0 |
| opencv-python | Apache-2.0 |
| reportlab | BSD |

## 四、外部服务

| 服务 | 许可 / 条款 | 说明 |
|---|---|---|
| ComfyUI | GPL-3.0 | 独立进程，通过 HTTP 调用，不与本项目代码链接或打包分发 |
| DeepSeek API | 服务条款 | 文本任务可替换为任意 OpenAI 兼容端点 |

## 五、生成内容的权利

- 本项目不主张对用户生成图片的任何权利；图片权利取决于所用模型许可、输入素材权利与当地法律。
- 用户上传的小说、照片等素材，其权利归原权利人；请确保你有权使用。
- 用真人照片提取姿态时，请取得肖像权人同意。
