# -*- coding: utf-8 -*-
"""漫画生成器 —— 核心包。

模块划分：

    models.py       数据模型（角色 / 场景 / 分镜 / 工程）
    logger.py       日志（界面日志窗口 + 文件日志）
    textutil.py     文本读取、编码识别、分段
    textclient.py   DeepSeek-V4.1-Flash 等 OpenAI 兼容文本模型客户端
    comfyworkflow.py ComfyUI 工作流读取 / 界面格式->API 格式拍平 / 参数注入
    imageclient.py  ComfyUI 图像生成客户端（排队、轮询、取图、显存降级、缓存）
    prompts.py      各阶段提示词模板
    pipeline.py     五阶段业务流水线（分析 / 人设 / 三视图 / 分镜拆解）
    bubble.py       对话气泡与文字叠加渲染
    imggen.py       分镜出图（组合提示词 + 参考图 + 气泡）
    exporter.py     结果输出与打包（目录 / PDF / 长条图 / ZIP）
"""

__version__ = "1.0.0"
__all__ = [
    "models", "logger", "textutil", "textclient", "imageclient", "comfyworkflow",
    "prompts", "pipeline", "bubble", "imggen", "exporter",
    "rules", "prompt_trace",
]
