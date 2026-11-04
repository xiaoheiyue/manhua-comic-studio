# 分镜合并去重

- **阶段**：03_storyboard
- **代码位置**：`prompts.storyboard_merge_prompt`
- **用途**：合并分段拆解的结果，补全字段、去重、保持剧情顺序。

## 输入变量

变量：`shots_json`、`project`。

## 提示词正文

### system

```text
你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。你只输出结构化 JSON，不输出任何解释性文字。
```

### user

```text
下面是同一部小说分段拆解得到的分镜列表（已按剧情顺序拼接）。
请你做一次整体梳理，输出最终的分镜列表。

【最重要的一条：只删重复，不要减少信息】
输出列表必须覆盖原文的全部剧情推进与画面细节。**允许输出比输入更多的分镜**，
但不允许因为"合并"而丢掉任何一个情节动作、场景转换或画面细节。
默认应当保留绝大多数输入分镜。

【梳理规则】
1. 只有"完全重复"或"画面高度雷同（同一个瞬间的同一机位）"才删除，保留信息更全的那一条。
2. 只有相邻两条**确实在描述同一个连续画面**时才合并；只要描述里有不同的动作、
   不同的表情、不同的视角、或不同的场景，就必须保留为两格，不要合并。
3. **无对话的纯画面分镜（环境交代、动作细节、物件特写、时间地点提示）同样重要，
   必须完整保留**，不要因为没有台词就删掉。
4. 如果发现某个情节节点在输入里缺失（例如关键动作、物件出现、时间提示），
   可以按原文补出对应分镜，但不要新增原文没有的情节。
5. 保持剧情时间顺序不变，序号由 1 连续递增（不要出现跳跃或重复）。
6. 修正 characters 字段：只能使用这些角色名：林晚、沈砚。
   只保留画面里看得见的人；电话/画外音的人不要放进 characters。
7. 修正 shot_type / mood：必须分别落在 远景/全景/中景/近景/特写 和
   紧张/温馨/悲伤/搞笑/愤怒/平静/悬疑/激动/压抑/浪漫 之内。
8. 保持每一格的 dialogue 条数尽量不超过 3 条；超过就把该格拆成两格，
   而不是删掉台词。
9. **保留并修正导演字段**：camera / composition / lighting / panel_scale /
   transition / focus / action / expression / char_sides / time_of_day /
   silent / hook。缺了就按画面内容补，不要留空。
10. 同一场景内不得连续 3 格同景别或同机位；panel_scale 至少保留 3 种。
11. 台词单条超过 20 字就拆到相邻格；旁白超过 24 字就精简。
12. 每场戏的第一格必须是远景或全景；章末最后一格 hook=true。

【分段结果 JSON】
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

【输出结构：与输入完全相同的 {shots: [...]} 结构，shots 为最终列表】
只输出 JSON。
```

## 期望输出

JSON：shots[]

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
