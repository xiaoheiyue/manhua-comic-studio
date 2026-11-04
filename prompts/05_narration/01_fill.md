# 旁白补全

- **阶段**：05_narration
- **代码位置**：`prompts.narration_fill_prompt`
- **用途**：为需要旁白的格子补写旁白（时空/内心/因果）。

## 输入变量

变量：`shots_json`、`max_chars`。

## 提示词正文

### system

```text
你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。你只输出结构化 JSON，不输出任何解释性文字。
```

### user

```text
下面是漫画分镜列表，请你为其中需要旁白的格子补写旁白。

【旁白只做这三件事】
1. 交代时间地点（例如"凌晨两点十七分，楼下卷帘门响了。"）
2. 写人物内心（例如"她早就习惯了这种声音。"）
3. 桥接因果（例如"三天前，她把同一个问题问过另一个人。"）

【绝对不要】
- 不要复述画面里已经看得见的东西（"她拿起手机"这种不要再写）。
- 不要写成对话，旁白不是台词。
- 不要新增原文没有的情节。

【输出要求】
- 每条旁白 ≤ 24 字，一条只说一件事。
- 只为下面列表里真的需要的格子写；不需要的格子返回空字符串。
- 每场戏的开头、以及出现内心活动 / 时间跳跃的格子最需要旁白。
- 一部短篇里旁白总数控制在 3-8 条，不要每格都加。

【作品信息】画风：悬疑暗调韩漫；梗概：林晚雨夜回到老宅，遇见沈砚，发现一只停在十一点四十七分的怀表。

【分镜列表】
[
  {
    "id": "d3d6584e",
    "index": 1,
    "shot_type": "中景",
    "characters": [
      "林晚"
    ],
    "scene": "城南老宅",
    "description": "林晚在画面左侧门口停下，右手还搭在门把上，眼睛盯着楼梯口；前景是半开的木门，后景是暗下来的楼梯；门外的雨光从她身后打进来。",
    "mood": "悬疑",
    "source_text": "",
    "dialogues": [
      {
        "speaker": "沈砚",
        "text": "你终于来了。",
        "tone": "",
        "bubble_style": "",
        "kind": "speech",
        "slot": "top-center",
        "tail": "",
        "offscreen": true,
        "size": ""
      }
    ],
    "panel_w": 0,
    "panel_h": 0,
    "camera": "平视",
    "composition": "三分构图",
    "lighting": "侧光",
    "focus": "她握在门把上的手",
    "expression": "眼睛盯着楼梯口",
    "action": "林晚推门进来，停在门口",
    "transition": "",
    "panel_scale": "M",
    "char_sides": {
      "林晚": "左"
    },
    "char_positions": {},
    "gaze": "",
    "props": [],
    "time_of_day": "夜晚",
    "silent": false,
    "hook": false,
    "page_id": "195e8289",
    "panel_index": 0,
    "panel_role": "main",
    "panel_orientation": "wide",
    "beat": "动作",
    "pacing": "慢",
    "panel_emphasis": "normal",
    "caption": "",
    "layout_hint": "",
    "bubble_programmatic": false,
    "image_path": "",
    "raw_image_path": "",
    "bubble_layout": [],
    "bubble_plan": [
      {
        "text": "你终于来了。",
        "speaker": "沈砚",
        "style": "圆角矩形",
        "kind": "speech",
        "slot": "top-center",
        "tail": "none",
        "x": 0.3594,
        "y": 0.06,
        "w": 0.2812,
        "h": 0.0995,
        "font_size": 41
      }
    ],
    "bubble_debug": {},
    "object_mode": "",
    "status": "待生成",
    "error": "",
    "attempts": 0,
    "skip": false,
    "seed": null,
    "reference_paths": [],
    "look_override": {},
    "quality_score": 0,
    "quality_issues": [],
    "image_note": ""
  }
]

【输出结构】
{"narration": [{"index": 序号, "text": "旁白内容（不需要就留空字符串）"}]}
只输出 JSON。
```

## 期望输出

JSON：{"narration":[{"index":1,"text":"…"}]}

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
