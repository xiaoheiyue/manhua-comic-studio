# 单条分镜补全

- **阶段**：03_storyboard
- **代码位置**：`prompts.shot_fix_prompt`
- **用途**：补齐一条分镜缺失的镜头字段（本地兜底后修复）。

## 输入变量

变量：`shot`、`project`。

## 提示词正文

### system

```text
你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。你只输出结构化 JSON，不输出任何解释性文字。
```

### user

```text
下面是一条漫画分镜信息，请你补全并润色。

【作品风格】悬疑暗调韩漫
【已有角色】林晚、沈砚
【已有场景】城南老宅

【当前分镜】
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

请输出补全后的单条分镜 JSON（结构不变），要求：
- description 必须是具体、可画的镜头描述（40-90 字），只写看得见的东西，
  不要台词、心理描写或原文摘抄
- characters 只能使用已有角色名；画面里看不见的人（电话/画外音）不要填
- shot_type 属于 远景/全景/中景/近景/特写，mood 属于 紧张/温馨/悲伤/搞笑/愤怒/平静/悬疑/激动/压抑/浪漫
- 补齐 camera（平视/俯视/仰视/斜侧/过肩/主观/背影）、composition、
  lighting、panel_scale、transition、focus、action、expression、char_sides
- dialogues 保留原有的对话内容，可补充标点与语气，不得改变原意；
  单条台词 ≤20 字，单格 ≤3 条
只输出 JSON。
```

## 期望输出

单条 shot JSON

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
