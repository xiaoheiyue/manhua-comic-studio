# 单格修镜头

- **阶段**：04_director
- **代码位置**：`prompts.panel_repair_prompt`
- **用途**：自检不过关时只重写这一格。

## 输入变量

变量：`shot_json`、`issues`。

## 提示词正文

### system

```text
你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。你只输出结构化 JSON，不输出任何解释性文字。
```

### user

```text
下面这一格漫画分镜存在问题，请只重写这一格。

【作品】雨夜的老宅／画风：Retro 90s Cel Anime & Korean Emotional Webtoon Illustration, Grainy Nostalgic Film Aesthetic（参考作者/风格：复古日系赛璐璐与韩系情绪动画 / Retro 90s Cel Anime & Korean Mood Webtoon；核心特征：复古日系二维动画与现代韩系情绪漫画融合的插画风格，具有1990年代手绘赛璐璐动画的朴素视觉质感；主题色：湖蓝、那不勒斯黄）
【角色】林晚、沈砚
【场景】城南老宅
【问题】描述过短；说话人不在画面

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

【要求】
- 保留原有剧情与台词，只修镜头：description 按①谁②画面位置③一个动作瞬间
  ④视线表情⑤前后景⑥光从哪来 来写，28~110 字，**不要写景别/机位**；
- characters 最多 2 人；只留画面里看得见的人，画外音用 offscreen=true；
- 补齐 camera / composition / lighting / panel_scale / transition / focus /
  action / expression / char_sides / char_positions / gaze / props / time_of_day；
- 台词单条 ≤20 字、单格 ≤3 条；旁白 ≤24 字；
- 输出单条分镜 JSON（结构不变），不要数组、不要解释。
只输出 JSON。
```

## 期望输出

单条 shot JSON

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
