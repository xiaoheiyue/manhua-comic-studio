# 导演重拍

- **阶段**：04_director
- **代码位置**：`prompts.director_pass_prompt`
- **用途**：把已有分镜重写成可拍镜头：补机位/构图/光线/画幅，剔掉画外音误入画面。

## 输入变量

变量：`shots_json`、`novel_excerpt`。

## 提示词正文

### system

```text
你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。你只输出结构化 JSON，不输出任何解释性文字。
```

### user

```text
下面是一份已经拆好的漫画分镜，但它是"原文搬运式"的：画面描述像小说、
缺少机位与构图、把打电话的对方也画进了同一间屋子、台词与旁白分工不清。
请你以快看漫画分镜主笔的身份，把每一格**重拍**成可以直接开画的镜头。

【作品信息】
画风：Retro 90s Cel Anime & Korean Emotional Webtoon Illustration, Grainy Nostalgic Film Aesthetic（参考作者/风格：复古日系赛璐璐与韩系情绪动画 / Retro 90s Cel Anime & Korean Mood Webtoon；核心特征：复古日系二维动画与现代韩系情绪漫画融合的插画风格，具有1990年代手绘赛璐璐动画的朴素视觉质感；主题色：湖蓝、那不勒斯黄）
梗概：林晚雨夜回到老宅，遇见沈砚，发现一只停在十一点四十七分的怀表。
角色：林晚、沈砚
场景：城南老宅

【重拍规则（按优先级）】
1. **保留剧情与台词**：不要删掉情节节点，不要改写台词原意；只重做镜头与描述。
   **总格数控制在原格数的 ±20% 以内**，不要为了"细"把一格拆成四五格。如果是修复
   旧工程，原格数通常已经是目标值，优先原样保留格数。
2. **一格一个 beat**：原格里如果写了多个连续动作，拆成多格；如果两格是同一瞬间
   的同一机位，合并成一格。拆分/合并后保持剧情顺序。
3. **画面里只留看得见的人**：电话、画外音、回忆里的声音，说话人用
   offscreen=true，不要放进 characters。一格最多 4 人；3~4 人只用于群像/对峙/
   建立镜头，5 人以上拆格。
4. **description 只写看得见的东西**：①谁 ②画面位置 ③一个动作瞬间 ④视线表情
   ⑤前后景 ⑥光从哪来。禁止引号、台词、心理描写、原文摘抄；
   **禁止写景别/机位**（俯视/全景/特写/过肩…那是独立字段）。28~110 字。
5. **补齐字段**：每格都必须有 shot_type / camera / composition / lighting /
   panel_scale / transition / focus / action / expression / char_sides /
   char_positions / gaze / props / time_of_day / silent / hook。
6. **节奏**：每场第一格建立镜头；同场景不连续 3 格同景别或同机位；
   近景+特写 20%~30%；每 6~8 格至少 1 格无台词；连续台词格 ≤3；
   panel_scale 至少 3 种。
7. **台词量**：每格 ≤3 条、每条 ≤20 字；长了就拆到相邻格。
   旁白 ≤24 字，只交代时空/内心/因果，不复述画面。
   说话人必须在 characters 里，否则 offscreen=true。
8. **槽位**：说话人在左 → top-left / mid-left / bottom-left（按台词顺序往下）；
   在右 → top-right / mid-right / bottom-right；画外音 → top-center；
   旁白在顶部。槽位避开人物脸部。
9. 最后一格 hook=true，作为本章悬念。
10. 不要新增原文没有的情节。
11. **物件/屏幕格 characters 必须为空**，不要为了"有主角"塞一个人进去。
12. **为多格页服务**：每格标 beat（建立/对白/反应/动作/情绪/转场/揭示/蒙太奇/
    日常/幽默/细节）与 pacing（慢/中/快）；只在钩子、反转、情绪爆发格标
    panel_emphasis=main/splash，其余 normal；说明型画面可给 ≤40 字 caption，
    对白格与高潮格留空。同一段对白拆成"说话人 → 听者反应"的交替格，
    方便程序把 2~6 格编进同一页。

【原文摘录（仅供核对剧情，不要抄进 description）】
雨夜的老宅

雨下得很大，敲在青瓦上噼啪作响。

林晚撑着一把断了一根伞骨的黑伞，站在城南那栋荒废了三十年的老宅门前。她今年二十四岁，身形清瘦，一头及腰的黑长直发被雨水打湿，贴在苍白的脸颊上。她穿着一件洗得发白的米色风衣，风衣口袋里露出半截泛黄的相片。

"就是这里了。"她低声说，声音几乎被雨声吞没。

大门虚掩着，铜环上落满灰尘。林晚伸手一推，门轴发出悠长的呻吟。院内荒草丛生，正屋的窗纸早已破损，风穿堂而过，卷起地上的落叶。

"谁？"一个沙哑的男声从正屋的阴影里传出来。

林晚脚步一顿。一个高瘦的男人从黑暗里走出来，他约莫三十岁上下，戴着一副圆框金丝眼镜，镜片后是一双极锐利的眼睛。他穿着深灰色的中山装，袖口磨得起了毛边，右手握着一盏老式煤油灯，左手垂在身侧，指节分明。

"你是……沈砚？"林晚的呼吸微微一滞。

"你认得我。"沈砚把煤油灯放在门边的石阶上，昏黄的光在两人之间铺开一小块暖色，"三年了，你还是找来了。"

"我姐的遗物，在你手里。"林晚从口袋里取出那半张相片，相片上是一个年轻女人抱着一个婴孩，笑容明亮，"林雨——她是我姐姐。"

沈砚的表情没有变化，镜片上映着灯火，"我知道。我从第一眼就知道你是她的妹妹，你和她长得很像，尤其是眼睛。"

雷声滚过屋顶，屋里的灰尘簌簌落下。

"进来吧。"沈砚侧身让开，"外面冷。有些事，站着说不完。"

林晚跨过那道高高的木门槛。正屋

【当前分镜 JSON】
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

【输出结构：与输入相同的 {"shots": [...]}，但每格补齐上述字段】
只输出 JSON。
```

## 期望输出

JSON：shots[]（字段同分镜拆解）

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
