# prompts/ · 全流程提示词库

> 目标：**每一阶段的提示词都能单独查看、对比、回溯**。

## 目录

| 阶段 | 内容 |
| --- | --- |
| [01_analysis](01_analysis/README.md) | 小说内容分析（角色 / 场景 / 情节 / 画风） |
| [02_characters](02_characters/README.md) | 角色人设与跨角色差异化 |
| [03_storyboard](03_storyboard/README.md) | 分镜拆解与整理 |
| [04_director](04_director/README.md) | 导演层重拍与单格修镜头 |
| [05_narration](05_narration/README.md) | 旁白补全 |
| [06_image](06_image/README.md) | 分镜出图提示词（含气泡坐标） |
| [07_bubble](07_bubble/README.md) | 气泡绘制、检测、回填与返修 |
| [08_repair](08_repair/README.md) | 出图自检与返修 |
| [09_style](09_style/README.md) | 绘制风格推荐（handraw-style 365 种画风 + 36 主题色 + 16 阶调） |
| [10_layout](10_layout/README.md) | 分页与版式规划（SB-001–SB-071 + 原生 WB-*） |

## 在界面中查看 / 编辑

每个相关步骤（内容分析、角色、分镜拆解、出图与打包）顶部都有
「本步骤提示词 · 查看 / 编辑」入口：

- 默认显示**按当前工程实时渲染**的 system / user / 图像提示词；
- 可以直接修改并保存，保存结果写在工程 JSON 的 `prompt_overrides` 里；
- 保存后，下一次对应生成会使用编辑后的版本；
- 文段 / 角色 / 分镜类提示词按对象分别保存，互不影响；
- `07_bubble/02_detection_fill.md` 与 `08_repair/01_repair_rules.md` 是规则说明，
  界面里可保存为本工程备注，但不会直接替换模型提示词。

## 两种存储

1. **模板/样例（本目录）**：`export_prompts.py` 用固定样例把每个 builder 渲染一遍，
   保证文档 = 代码实际发出的文本；
2. **运行期真实提示词**：`comicgen/prompt_trace.py` 在每次调用时把
   system/user、气泡计划、检测结果写到 `data/projects/<工程id>/prompts/<阶段>/`，
   工程目录下还有 `index.md` 汇总。

## 重新生成

```powershell
env\Scripts\python.exe export_prompts.py
```

## 回溯一张图

1. 在 `data/projects/<id>/prompts/06_image/` 找到该格的提示词；
2. 在 `07_bubble/` 看当时的气泡计划（plan）与检测结果（debug）；
3. 在 `02_characters/` 看人设/身份卡版本；
4. 对比 `prompts/` 里当前模板，就能判断问题出在规则、数据还是模型。
