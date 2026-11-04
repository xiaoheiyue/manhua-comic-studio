# 对话拆格

- **阶段**：03_storyboard
- **代码位置**：`prompts.storyboard_dialogue_split_prompt`
- **用途**：一格台词过多时拆成多格（说话人/听者/动作交替）。

## 输入变量

变量：`shots_json`、`max_per_shot`。

## 提示词正文

### system

```text
你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。你只输出结构化 JSON，不输出任何解释性文字。
```

### user

```text
下面是漫画分镜列表。其中部分分镜把多轮对话塞进了同一格，
会导致这一格要叠很多对话气泡、把画面完全盖住，必须拆开。

【你的任务】
把每一个"对话条数超过 2 条"或"包含多个连续动作"的分镜拆成若干格，使得：
1. 每一格的对话条数不超过 2 条。
2. 每一格仍然是一个清晰的独立画面（说话人近景/特写、听者反应、动作瞬间等）。
3. 原画面描述的细节不要丢：把原描述拆开分配给拆分后的各格，可适当补充镜头语言，
   但不要新增原文没有的情节。
4. 对话条数不超过 2 条的分镜，原样保留，不要改动。
5. 所有分镜保持原有剧情顺序。
6. characters 只能使用这些角色名：林晚、沈砚。
7. shot_type 属于 远景/全景/中景/近景/特写，
   mood 属于 紧张/温馨/悲伤/搞笑/愤怒/平静/悬疑/激动/压抑/浪漫。

【当前分镜列表】
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

【输出结构：与输入完全相同的 {shots: [...]} 结构】
只输出 JSON。
```

## 期望输出

JSON：shots[]

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
