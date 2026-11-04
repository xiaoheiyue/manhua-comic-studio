# Z-Image 与 Qwen-Image-2.1 对比测试

测试日期：2026-10-26  
测试文本：`xiaoshuo.txt`（项目 `测试3_机房`，3723 字）

## 1. 切换方式

页面顶部新增：

```text
[Z-Image-Turbo] [Qwen-Image-2.1]
```

切换后：

- 新任务使用新模型；
- 已生成图不会自动重画；
- 设置写入 `data/settings.json`，重启后保留。

后端：

```text
GET  /api/model/family
POST /api/model/family
```

## 2. 工作流分类

```text
comfy_workflows/
├─ Z-Image/
│  └─ image_z_image_turbo_*.json
└─ Qwen-Image-2.1/
   ├─ qwen_image_2.1_turbo_edit_local.json
   └─ qwen_image_2.1_turbo_t2i_local.json
```

根目录仍保留一份运行副本，ComfyUI 按名字加载。

## 3. 测试结果

### 3.1 文本流程

```text
项目：测试3_机房
原文：3723 字
精修：12 锚点 / 25 格（Qwen 只影响出图，不影响精修）
分析：6 角色 / 4 场景
分镜：63 格 / 16 页
```

### 3.2 角色三视图

Qwen-Image-2.1 三视图：

- 正面/侧面/背面结构正确；
- 服装、发型一致；
- 背景不是纯白，第三视图带了建筑背景。

![Qwen 三视图](data/characters/陆鸣/默认造型/三视图.png)

### 3.3 单格分镜

Qwen 单格：

![Qwen 单格](data/shots/f64ff0e85d/分镜_001.png)

观察：

- 画风、人物、服装比 Z-Image 更接近设定；
- 但“单人格”仍可能画出第二个人（Qwen 对人数控制不强）；
- 动作不一定跟姿态骨架。

### 3.4 25 格分镜

Qwen 25 格：

![Qwen 25 格](data/projects/f64ff0e85d/25宫格_分镜结果.png)

观察：

- 画风统一、场景统一；
- 人物一致性好于 Z-Image；
- 仍然有多人同格/人数漂移；
- 速度约 4 分钟/25 格。

### 3.5 参考图表情包

Qwen Image Edit：

- 白发、紫黑长袍、银腰带、额间红纹、精灵耳都保留；
- 9 个表情同一角色、同一服装；
- 文字与表情一一对应。

![Qwen 表情包](outputs/测试_qwen_pack9.png)

这一步是 Qwen 明显强于 Z-Image 的地方。

## 4. 对比表

| 项目 | Z-Image-Turbo | Qwen-Image-2.1 |
|---|---|---|
| 8GB 可行性 | 稳定 | 可行，但需先卸载 Z-Image |
| 单格速度 | 约 20 秒 | 约 8–15 秒（512/768） |
| 25 格速度 | 约 4 分钟 | 约 4 分钟 |
| 角色发型/服装 | 容易漂 | 明显更稳 |
| 参考图 Edit | 无 | 有，效果好 |
| 姿态 ControlNet | 有 | 当前未接 |
| 多人物控制 | 弱 | 弱 |
| 画风 | 稳定，偏插画 | 更细腻，偏厚涂/动画 |
| 中文文字 | 容易错 | 仍容易错，建议程序叠加 |
| 角色一致性 | 中 | 较高 |

## 5. 当前结论

- **角色主视图/三视图/参考图表情包**：Qwen 更好；
- **最终分镜格**：Qwen 画风与角色更好，但人数/动作仍不稳；
- **姿态控制**：Z-Image 的 ControlNet 更成熟；
- **纯速度**：两者接近，Qwen 略快；
- **显存**：Qwen 需要先 `/free` 卸载 Z-Image，不能同时常驻。

## 6. 推荐用法

```text
角色参考图、表情包、主视觉：Qwen-Image-2.1
需要严格姿态骨架的分镜：Z-Image + ControlNet
普通分镜：Qwen-Image-2.1
```

完全切换到 Qwen 后：

- 姿态骨架不会真正控制动作；
- 需要后续接 Qwen 的 ControlNet/Union 才能恢复姿态控制。

## 7. 已知问题

1. Qwen 单人格仍可能补第二个人；
2. Qwen 三视图背景不纯白；
3. 姿态骨架当前未接入 Qwen；
4. Qwen 中文标题仍可能错，海报/表情包文字建议程序叠加；
5. 63 格全量出图未全部重跑，只做了角色/单格/25 格/表情包代表测试。

## 8. 文件

- 工作流：[Qwen-Image-2.1](comfy_workflows/Qwen-Image-2.1)
- 工作流说明：[Qwen-Image-2.1 本地工作流](docs/Qwen-Image-2.1本地工作流.md)
- 后端实现：`comicgen/qwencomic.py`、`comicgen/qwenedit.py`


## 9. 后续更新（Qwen ControlNet / 角色路由 / 预览）

### 9.1 Qwen ControlNet

新增：

```text
ModelPatchLoader -> qwen_image_2.1_fun_controlnet_union_int8_convrot
QwenImageDiffsynthControlnet
```

工作流：

```text
comfy_workflows/Qwen-Image-2.1/qwen_image_2.1_turbo_control_local.json
```

用途：

- 人物姿态骨架控制；
- 有角色参考图时，参考图 + ControlNet 同时使用；
- 无参考图时，纯姿态 + T2I。

实测：

- 姿态构图生效；
- 单人物时能站到骨架位置；
- 仍可能补人/脸部漂移。

### 9.2 角色 / 三视图 / 参考图路由

```text
人物主视图、三视图：
  无参考图 -> Qwen T2I
  有参考图 -> Qwen Edit

分镜：
  有角色参考 -> Qwen Edit
  无角色参考 -> Qwen T2I
  有姿态骨架 -> Qwen ControlNet

25 格：
  不生成气泡

最终分镜：
  图像不写文字/气泡字
  文字最后程序叠加
```

### 9.3 创作工坊预览

- 365 个风格缩略图，可直接点选；
- 119 个版式预览图（按用途过滤）；
- 主色/点缀色色块；
- 参考图画风开关：
  - 关闭：优先保人物；
  - 打开：风格更明显，但可能改变人物。

### 9.4 已修

- 输入文本后再点精修，不再报“请先输入小说文本”；
- 上传参考图后立即本地预览 + 上传状态；
- 作品区 4 秒自动刷新；
- 模型选择器持久化到 `data/settings.json`。


## 10. Qwen ControlNet 直接出合格图的设置

实测三种控制图：

- 原始骨架：单人、姿态正确；
- Canny 骨架：单人、线条更干净；
- 反色骨架：错误，输出只剩骨架图。

结论：

```text
Qwen ControlNet 用“原始黑白骨架”或“Canny 骨架”
不要用反色骨架
```

另一个关键：

```text
Qwen ControlNet + 角色参考图同时送
→ 复制人物/双头
```

所以姿态格现在改成：

```text
T2I + Qwen ControlNet，不送角色参考图
身份靠 prompt/角色锁
```

非姿态格才用：

```text
角色参考图 → Qwen Edit
```

这是目前 Qwen ControlNet 最稳的直接出图设置：

```text
strength = 0.7
steps = 8
cfg = 1.0
euler / simple
骨架黑白原图
```

如果一定要“角色参考 + 姿态”同时成立，需要后续做分区/多控制，不是现在的单 ControlNet 能稳定做到的。
