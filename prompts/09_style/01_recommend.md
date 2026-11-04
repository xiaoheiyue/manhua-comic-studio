# 绘制风格推荐

- **阶段**：09_style
- **代码位置**：`prompts.style_recommend_prompt`
- **用途**：从 365 种 handraw 画风里收敛候选，再由模型结合题材/情绪/时代选一套风格 + 主题色。

## 输入变量

变量：`project`、`shortlist`（本地关键词收敛后的候选编号）、`colors_table`。

## 提示词正文

### system

```text
你是漫画美术总监，只输出结构化 JSON。
```

### user

```text
你是这部漫画的美术总监。请为它选一套最合适的**绘制风格 + 主题色**。

【作品信息】
标题：雨夜的老宅
梗概：林晚雨夜回到老宅，遇见沈砚，发现一只停在十一点四十七分的怀表。
情节线：雨夜回宅 → 遇见沈砚 → 发现怀表
关键点：无
角色：林晚（旧宅继承人）、沈砚（旧宅访客）
场景：城南老宅
题材与气质（文本分析结果）：冷色调、低照度、强调雨夜质感

【候选画风（编号｜风格名｜参考作者/风格｜核心视觉特征）】
FG-023｜Retro 90s Cel Anime & Korean Emotional Webtoon Illustration, Grainy Nostalgic Film Aesthetic｜复古日系赛璐璐与韩系情绪动画 / Retro 90s Cel Anime & Korean Mood Webtoon｜复古日系二维动画与现代韩系情绪漫画融合的插画风格，具有1990年代手绘赛璐璐动画的朴素视觉质感
FE-059｜Airy Chinese Ink Fantasy Animation｜中国奇谭·鹅鹅鹅｜参考作品：中国奇谭的画风
FC-029｜Clean Airy Flat Vector Illustration｜清透扁平插画｜轮廓清晰的大面积平涂色块塑造形体；造型适度概括与圆润几何化，保留关键识别特征
FI-030｜David Fincher Desaturated Moody Green-Toned Precision Thriller Cinema｜David Fincher / 大卫·芬奇｜低饱和暗调；冷绿与黄绿调；精密构图；压抑悬疑；雨夜百叶窗与夜幕写字楼
FE-056｜Pale Lacquer & Raden Decorative Illustration｜素漆螺钿插画风｜当代新中式浅色漆画式二维装饰插画，融合中国古典人物画的造型语言与现代平面设计；整体首先呈现为平面装饰画，而非传统工笔写实绘画
FH-040｜Medieval Dark Fantasy Ink & Ochre Illustration｜OscarAI｜中世纪暗黑奇幻墨线插画风、精密细腻的钢笔交错排线、暗调羊皮纸暖赭色调、压迫感十足的重装牛角铠甲骑士与古老石阶遗迹氛围
FG-001｜Late-80s Cyberpunk Dark OVA Cel Anime｜OscarAI｜80年代末日本黑暗赛博朋克OVA赛璐珞动画风、锐利手绘勾线、浓郁阴郁的丙烯手绘背景、高对比度硬光影、冷艳魅惑的女性肖像与黄金时代动画胶片质感
FG-003｜Classic 80s-90s Heroic Fantasy Cel Anime｜OscarAI｜80-90年代经典剑与魔法奇幻赛璐珞动画风、纯正手绘赛璐珞质感、红发持剑女勇士与古堡阴影下的重甲战士、古典戏剧性冷暖打光
FB-034｜Minimalist Doodle & Semi-Realistic Hybrid Illustration｜简笔与半写实融合风｜写实与简笔融合的混合插画风
FE-057｜Contemporary Peking Opera Comic Illustration｜当代戏曲漫画插画风｜当代手绘戏曲漫画插画风，借用中国京剧人物画谱、京剧脸谱和老派中国戏曲漫画的造型语言，并以现代插画审美重新简化和整理
FI-019｜Helmut Newton High-Contrast Bold Monochrome Seductive Noir｜Helmut Newton / 赫尔穆特·牛顿｜黑白高反差；强烈光影；冷艳高级性感；大理石暗夜与锐利剪影
FI-022｜Gregory Crewdson Twilight Cinematic Mystery Staged Photography｜Gregory Crewdson / 格雷戈里·克鲁德森｜电影级布光；静止戏剧；神秘悬疑感；暮色小镇独栋房屋与幽暗街道

【主题色（编号｜中文名（英文名）｜色系）】
C-01｜克莱因蓝（Klein Blue）｜经典蓝系
C-02｜马若雷勒蓝（Majorelle Blue）｜经典蓝系
C-03｜普鲁士蓝（Prussian Blue）｜经典蓝系
C-04｜钴蓝（Cobalt Blue）｜经典蓝系
C-05｜群青（Ultramarine）｜经典蓝系
C-06｜孔雀蓝（Peacock Blue）｜经典蓝系
C-07｜蒂芙尼蓝（Tiffany Blue）｜清新绿系
C-08｜湖蓝（Cerulean Blue）｜清新绿系
C-09｜薄荷绿（Mint Green）｜清新绿系
C-10｜鼠尾草绿（Sage Green）｜清新绿系
C-11｜祖母绿（Emerald Green）｜清新绿系
C-12｜橄榄绿（Olive Green）｜清新绿系
C-13｜维罗纳绿（Veronese Green）｜古典红绿
C-14｜森林绿（Forest Green）｜古典红绿
C-15｜朱砂红（Cinnabar Red）｜古典红绿
C-16｜中国红（Chinese Red）｜古典红绿
C-17｜勃艮第红（Burgundy）｜古典红绿
C-18｜庞贝红（Pompeian Red）｜古典红绿
C-19｜玫瑰红（Rose Red）｜浪漫粉紫
C-20｜洋红（Magenta）｜浪漫粉紫
C-21｜珊瑚粉（Coral Pink）｜浪漫粉紫
C-22｜灰粉（Dusty Rose）｜浪漫粉紫
C-23｜薰衣草紫（Lavender）｜浪漫粉紫
C-24｜帝王紫（Imperial Purple）｜浪漫粉紫
C-25｜爱马仕橙（Hermès Orange）｜暖阳大地
C-26｜柿子橙（Persimmon Orange）｜暖阳大地
C-27｜那不勒斯黄（Naples Yellow）｜暖阳大地
C-28｜芥末黄（Mustard Yellow）｜暖阳大地
C-29｜赭石（Ochre）｜暖阳大地
C-30｜象牙白（Ivory）｜暖阳大地
C-31｜佩恩灰（Payne's Grey）｜中性色系
C-32｜炭黑（Charcoal Black）｜中性色系
C-33｜暖灰（Warm Grey）｜中性色系
C-34｜奶油白（Cream White）｜中性色系
C-35｜石板灰（Slate Gray）｜中性色系
C-36｜米驼色（Taupe）｜中性色系

【选择规则】
1. 只能从上面的候选画风里选一个，输出它的编号；不要发明新编号。
2. 结合故事的**题材、时代、情绪基调、读者与画面介质**判断，不要机械按关键词对号入座。
   同一题材可以有很多种美学解法（如水墨、木刻、粗粒油画棒、包豪斯几何）。
3. 主题色选 1~2 个：一个主色统领画面，必要时再加一个点缀色；要能和该风格的
   笔触、媒介、明暗关系共存，而不是盖掉它。
4. rationale 要具体到"这套风格的哪种视觉语言（线条/笔触/媒介/光影/色彩）如何承载
   这个故事的情绪"，不要写套话。
5. 这是漫画分镜（人物 + 场景 + 对白气泡），风格要能稳定复现人物与连续场景。

【严格输出以下 JSON】
{
  "style_id": "从候选清单里选一个编号，如 FA-012",
  "color_ids": [
    "1~2 个主题色编号，如 C-01；第二个作为点缀色"
  ],
  "rationale": "一句话说明为什么这套风格+配色契合这个故事（结合题材、时代、情绪、受众）"
}
只输出 JSON。
```

## 期望输出

JSON：{"style_id": "FA-001", "color_ids": ["C-01"], "rationale": "…"}

> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。
