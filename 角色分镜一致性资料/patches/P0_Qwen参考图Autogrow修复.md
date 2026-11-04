# P0 修复建议：Qwen 参考图 Autogrow 键格式

> 状态：**已于 2026-11-03 应用到 `comicgen/qwenedit.py`**；保留本文作为回归参考。
> 影响：`generate()`（单图/多图 Edit）与 `generate_control()`（ControlNet + 参考图）。`generate_t2i()` 无参考图，不受影响。

---

## 1. 问题

`TextEncodeQwenImage21.images` 是 ComfyUI 的 `COMFY_AUTOGROW_V3` 输入。节点 schema 里的名字是 `image_1 … image_16`，API 必须用**扁平键**：

```json
"images.image_1": ["20", 0]
```

当前代码用的是嵌套对象：

```python
images = {f"image_{i+1}": [str(20 + i), 0] for i in range(len(names))}
graph["5"]["inputs"]["images"] = images
```

ComfyUI 不会把 `"images": {"image_1": ...}` 识别成动态槽位，`images` 变成空字典，参考图被静默丢弃。表现：

- 换参考图，输出哈希不变；
- 生成时间异常短（命中缓存）；
- 画面主要受提示词/种子影响。

---

## 2. `generate()` 的修改

### 修改前

```python
load_nodes = {str(20 + i): {"class_type": "LoadImage",
                            "inputs": {"image": name}}
              for i, name in enumerate(names)}
if not load_nodes:
    load_nodes["20"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
images = {f"image_{i+1}": [str(20 + i), 0] for i in range(len(names))}
graph = {
    ...
    "5": {"class_type": "TextEncodeQwenImage21", "inputs": {
        "clip": ["2", 0], "prompt": prompt,
        "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
        "resolution": int(size),
        "images": images,
        "vae": ["3", 0]}},
    ...
}
```

### 修改后

```python
load_nodes = {str(20 + i): {"class_type": "LoadImage",
                            "inputs": {"image": name}}
              for i, name in enumerate(names)}
if not load_nodes:
    load_nodes["20"] = {"class_type": "LoadImage", "inputs": {"image": ""}}

enc_inputs = {
    "clip": ["2", 0],
    "prompt": prompt,
    "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
    "resolution": int(size),
    "vae": ["3", 0],
}
for i in range(len(names)):
    # Autogrow 动态输入：扁平键，不能用 {"images": {"image_1": ...}}
    enc_inputs[f"images.image_{i+1}"] = [str(20 + i), 0]

graph = {
    ...
    "5": {"class_type": "TextEncodeQwenImage21", "inputs": enc_inputs},
    ...
}
```

---

## 3. `generate_control()` 的修改

### 修改前

```python
ref_nodes = {str(14 + i): {"class_type": "LoadImage",
                           "inputs": {"image": name}}
             for i, name in enumerate(ref_names)}
image_map = {f"image_{i+1}": [str(14 + i), 0]
             for i in range(len(ref_names))}
graph: dict[str, Any] = {
    ...
    "5": {"class_type": "TextEncodeQwenImage21", "inputs": {
        "clip": ["2", 0], "prompt": prompt,
        "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
        "resolution": max(size), "images": image_map, "vae": ["3", 0]}},
    ...
}
```

### 修改后

```python
ref_nodes = {str(14 + i): {"class_type": "LoadImage",
                           "inputs": {"image": name}}
             for i, name in enumerate(ref_names)}

enc_inputs: dict[str, Any] = {
    "clip": ["2", 0],
    "prompt": prompt,
    "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
    "resolution": max(size),
    "vae": ["3", 0],
}
for i in range(len(ref_names)):
    enc_inputs[f"images.image_{i+1}"] = [str(14 + i), 0]

graph: dict[str, Any] = {
    ...
    "5": {"class_type": "TextEncodeQwenImage21", "inputs": enc_inputs},
    ...
}
```

---

## 4. 应用后的回归

```powershell
# 1) 参考图变化 → 输出哈希必须变化
env\Scripts\python.exe tools\ref_probe_flat.py

# 2) 全流程自检
env\Scripts\python.exe selftest.py

# 3) 语法
env\Scripts\python.exe -m compileall -q comicgen
```

人工检查：

- [ ] 同一提示词/种子，只换参考图，输出明显不同；
- [ ] 主视图参考的脸/发型/服装主色能对得上；
- [ ] 三视图侧面/背面生成不再“像另一个人”；
- [ ] 俯视图仍是顶视，没有因为参考图格式变化而变成正面；
- [ ] 分镜输出画幅没有异常（注意：输出比例跟随第 1 张参考图，见 04 §1.3）；
- [ ] 页面合成没有新增裁头/裁脚。

---

## 5. 不要一起改的东西

- 不要顺手把 `cfg=1.0` 改成 `cfg>1`：Qwen Turbo 的采样设置是配套的，改了可能过曝/变慢；
- 不要顺手把 `size` 改成 `EmptyLatentImage`：节点明确说“换尺寸会改变编辑结果”，先按 04 的 pad 方案验证；
- 不要在没有 A/B 的情况下同时改参考图格式、路由、提示词、骨架——四个变量一起动，出了问题无法归因。
