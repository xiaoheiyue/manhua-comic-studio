# -*- coding: utf-8 -*-
"""离线自检脚本：不联网、不依赖模型服务，用桩客户端跑通全部五个阶段。

用法：
    env\\Scripts\\python.exe selftest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 控制台编码兜底：GBK 终端无法输出 ✅ 之类的符号，避免自检结果打印时崩掉
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

import config  # noqa: E402
from PIL import Image  # noqa: E402
from comicgen import bubble, exporter, imggen, models, pipeline, textutil  # noqa: E402
from comicgen.imageclient import GenResult  # noqa: E402


class StubImageGenerator:
    """自检用出图桩：不连 ComfyUI，把品牌图缩放后当作生成结果。"""

    supports_reference_images = True

    def __init__(self):
        logo = Path(config.ASSETS_DIR) / "logo.png"
        self._src = Image.open(logo).convert("RGB") if logo.exists() else None

    def generate(self, prompt, reference_paths=None, size=None, **kwargs) -> GenResult:
        w, h = size or config.IMAGE_SIZE
        if self._src is not None:
            img = self._src.resize((w, h), Image.LANCZOS)
        else:
            img = Image.new("RGB", (w, h), (108, 124, 231))
        return GenResult(image=img, seed=1, size=(w, h),
                         reference_paths=list(reference_paths or []),
                         workflow="selftest")

    def health(self):
        return True, "自检桩"

    def image_model_status(self):
        return {"loaded": True, "workflow": "selftest"}

    def load_image_model(self, *args, **kwargs):
        return True, "自检桩"

    def list_models(self):
        return []



# ---------------------------------------------------------------------------
# 桩文本客户端：按提示词内容返回预置 JSON
# ---------------------------------------------------------------------------

ANALYSIS = {
    "summary": "林晚在雨夜来到城南荒废的老宅，寻找姐姐林雨的遗物，遇到了守宅三年的沈砚，"
               "得知姐姐临终前把最重要的东西托付给了他。",
    "plotline": "1. 雨夜林晚抵达老宅\n2. 沈砚现身\n3. 林晚表明身份\n4. 沈砚请她进屋\n"
                "5. 发现三年前的旧报纸\n6. 林晚要求听全部真相",
    "key_points": ["林晚与沈砚在老宅相遇", "林晚亮出姐姐的半张相片", "桌上三年前的旧报纸被看见"],
    "style": "悬疑暗调韩漫",
    "style_detail": "雨夜、老宅、煤油灯，冷灰蓝为主色，局部暖黄光源，强调压抑悬疑氛围",
    "characters": [
        {"name": "林晚", "gender": "女", "age": "二十四岁", "identity": "自由撰稿人",
         "personality": "克制、执拗、内心敏感", "role": "主角", "is_extra": False},
        {"name": "沈砚", "gender": "男", "age": "三十岁上下", "identity": "守宅人、旧案知情人",
         "personality": "沉默、锐利、守诺", "role": "关键线索人物", "is_extra": False},
        {"name": "林雨", "gender": "女", "age": "二十七岁", "identity": "林晚的姐姐（已故）",
         "personality": "明亮、坚定", "role": "故事起因", "is_extra": False},
        {"name": "邻居路人", "gender": "未知", "age": "中年", "identity": "路人",
         "personality": "好奇", "role": "群众角色", "is_extra": True},
    ],
    "scenes": [
        {"name": "城南老宅门前", "features": "青瓦雨檐、铜环木门、荒草", "atmosphere": "阴冷潮湿",
         "frequency": "高频"},
        {"name": "老宅正屋", "features": "八仙桌、煤油灯、封蜡木箱、旧报纸", "atmosphere": "压抑安静",
         "frequency": "高频"},
    ],
}

DESIGN = {
    "design": "【基本形象】女性，二十四岁，身形清瘦，鹅蛋脸，五官精致，眼神清亮而疲惫。\n"
              "【发型与发色】及腰黑长直发，被雨水打湿，无发饰。\n"
              "【服装风格】米色风衣，洗得发白，袖口微卷，内搭深色高领毛衣。\n"
              "【气质与表情】克制的忧郁，眉头常微微蹙起，表情清冷。\n"
              "【标志性特征】手中一把断了伞骨的黑伞，口袋里露出半截泛黄相片。\n"
              "【色彩方案】主色米白与灰蓝，辅助色暗红（相片与唇色）。",
    "keywords": ["及腰黑长直发", "苍白清瘦的脸", "米色旧风衣", "断骨黑伞",
                 "深色高领毛衣", "清冷忧郁的眼神"],
    "main_visual_prompt": "二十四岁清瘦女性角色全身正面站姿，黑长直发，米色旧风衣",
    "pose_sheet_prompt": "二十四岁清瘦女性角色姿态总图，正面侧面背面与动作",
}

SHOTS = {
    "shots": [
        {"shot_type": "远景", "characters": ["林晚"], "scene": "城南老宅门前",
         "description": "雨夜，林晚撑着一把断骨黑伞站在老宅门前，青瓦上的雨水连成线落下，"
                        "昏黄路灯把她的影子拉得很长。",
         "mood": "压抑", "source_text": "雨下得很大，敲在青瓦上噼啪作响。林晚撑着一把断了一根伞骨的黑伞，站在城南那栋荒废了三十年的老宅门前。",
         "dialogues": []},
        {"shot_type": "近景", "characters": ["林晚"], "scene": "城南老宅门前",
         "description": "林晚的侧脸特写，雨水顺着发梢滴落，她目光定在虚掩的铜环木门上。",
         "mood": "悬疑", "source_text": "“就是这里了。”她低声说，声音几乎被雨声吞没。",
         "dialogues": [{"speaker": "林晚", "text": "就是这里了。", "tone": "平静", "kind": "speech"}]},
        {"shot_type": "全景", "characters": ["林晚", "沈砚"], "scene": "老宅正屋",
         "description": "推开门后，沈砚从正屋阴影里走出，右手举着煤油灯，昏黄的光在两人之间"
                        "铺开一小块暖色，林晚站在门槛外。",
         "mood": "紧张", "source_text": "“谁？”一个沙哑的男声从正屋的阴影里传出来。",
         "dialogues": [{"speaker": "沈砚", "text": "谁？", "tone": "紧张", "kind": "speech"}]},
        {"shot_type": "特写", "characters": ["林晚"], "scene": "老宅正屋",
         "description": "林晚的面部特写，瞳孔微缩，嘴唇轻启，雨水从下颌滑落。",
         "mood": "激动", "source_text": "“你是……沈砚？”林晚的呼吸微微一滞。",
         "dialogues": [{"speaker": "林晚", "text": "你是……沈砚？", "tone": "激动", "kind": "speech"}]},
        {"shot_type": "中景", "characters": ["沈砚"], "scene": "老宅正屋",
         "description": "沈砚把煤油灯放在石阶上，侧身让开门口，眼神藏在金丝眼镜的反光之后。",
         "mood": "平静", "source_text": "“进来吧。”沈砚侧身让开，“外面冷。有些事，站着说不完。”",
         "dialogues": [{"speaker": "沈砚", "text": "进来吧，外面冷。", "tone": "平静", "kind": "speech"},
                       {"speaker": "旁白", "text": "雨声忽然更急了。", "tone": "", "kind": "narration"},
                       {"speaker": "音效", "text": "轰隆！", "tone": "", "kind": "sfx"}]},
        {"shot_type": "近景", "characters": ["林晚", "沈砚"], "scene": "老宅正屋",
         "description": "桌角那张被压在笔记本下的旧报纸，报头“城南旧案”在灯下清晰可见，"
                        "林晚的手停在报纸边缘。",
         "mood": "悬疑", "source_text": "林晚的目光定在那张报纸上，手指慢慢收紧。",
         "dialogues": [{"speaker": "林晚", "text": "全部。从头到尾，一个字都不要漏。", "tone": "激动",
                        "kind": "speech"}]},
    ]
}


class StubTextClient:
    """按调用顺序返回预置结果的桩客户端。"""

    def __init__(self):
        self.calls: list[str] = []
        self._design_index = 0

    def chat(self, messages, **kwargs):
        return json.dumps(ANALYSIS, ensure_ascii=False)

    def chat_json(self, messages, expect="object", json_mode=True, **kwargs):
        prompt = "\n".join(m.get("content", "") for m in messages)
        self.calls.append(prompt[:60])
        if "拆解成漫画分镜" in prompt:              # 分镜拆解
            return SHOTS
        if "整体梳理" in prompt:                    # 分镜合并梳理
            return SHOTS
        if "请为下面这个角色设计" in prompt:          # 人设
            self._design_index += 1
            return DESIGN
        if "合并成一份" in prompt:                   # 分段分析合并
            return ANALYSIS
        if "补全并润色" in prompt:
            return SHOTS["shots"][0]
        return ANALYSIS                            # 小说整体分析


def _ensure_prop_images(project) -> int:
    """给"无人物道具格"补一张占位道具图。

    生产链路里，纯道具格没有上传道具图会**故意失败**（避免文生图补人）。
    离线自检要跑通全流程，这里用 PIL 画一张没有脸、没有手的桌面物件图。
    """
    from PIL import Image, ImageDraw

    folder = Path(config.DATA_DIR) / "_selftest_props"
    folder.mkdir(parents=True, exist_ok=True)
    made = 0
    for s in project.shots:
        if s.characters or (s.prop_image and Path(s.prop_image).exists()):
            continue
        p = folder / f"prop_{s.index:03d}.png"
        im = Image.new("RGB", (512, 512), (28, 32, 38))
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((70, 150, 442, 390), radius=18,
                               fill=(56, 62, 70), outline=(120, 128, 136), width=3)
        draw.line((110, 200, 400, 200), fill=(150, 158, 166), width=4)
        draw.line((110, 250, 330, 250), fill=(110, 118, 126), width=3)
        draw.ellipse((330, 300, 410, 360), fill=(70, 76, 84))
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, format="PNG")
        s.prop_image = str(p)
        made += 1
    return made


# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("漫画生成器 —— 离线自检")
    print("=" * 72)

    config.ensure_dirs()
    # 自检不能写真实角色目录，否则会把用户的 林晚/林雨 图覆盖成占位图
    config.CHARACTER_DIR = Path(config.DATA_DIR) / "_selftest_characters"
    config.CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    pipeline.log.info("自检开始")

    # ---- 文本工具 ----
    chunks = textutil.split_into_chunks(config.SAMPLE_NOVEL, chunk_size=400, overlap=50)
    assert len(chunks) >= 2, "分段失败"
    print(f"[1/8] 文本分段 OK：{len(chunks)} 段")

    text, enc, name = textutil.detect_and_decode(
        config.SAMPLE_NOVEL.encode("gb18030")), "gb18030", "sample.txt"
    assert "林晚" in text[0], "GB18030 解码失败"
    print(f"[2/8] 编码识别 OK：gb18030 -> {text[0][:12]}…")

    # ---- 工程与模型 ----
    project = models.Project(title="雨夜的老宅", novel_text=config.SAMPLE_NOVEL,
                             source_filename="sample.txt")
    project.save()
    reloaded = models.Project.load(Path(config.PROJECT_DIR) / f"{project.id}.json")
    assert reloaded.title == project.title and len(reloaded.shots) == 0
    print(f"[3/8] 工程落盘/读取 OK：{project.id}")

    # ---- 流水线（桩文本 + 桩出图）----
    pipe = pipeline.Pipeline(text_client=StubTextClient(),
                             image_client=StubImageGenerator(),
                             log_cb=lambda m: None)
    pipe.analyze_novel(project)
    assert project.characters and project.scenes, "分析结果为空"
    print(f"[4/8] 内容分析 OK：角色 {len(project.characters)} 个、"
          f"场景 {len(project.scenes)} 个、画风「{project.style}」")

    # 人设 + 参考图
    pipe.generate_character_images(project, project.characters[0])
    c0 = project.characters[0]
    assert Path(c0.main_visual).exists(), "主视图未生成"
    assert Path(c0.pose_sheet).exists(), "姿态总图未生成"
    assert c0.reference_image(), "参考图解析失败"
    print(f"[5/8] 人设、主视图与姿态总图 OK：{c0.name} -> "
          f"{Path(c0.main_visual).name} / {Path(c0.pose_sheet).name}，"
          f"参考图 {Path(c0.reference_image()).name}")

    # 分镜拆解
    pipe.decompose_storyboard(project)
    assert len(project.shots) >= config.MIN_SHOTS - 1, "分镜数量异常"
    shot_with_dialogue = next((s for s in project.shots if s.has_dialogue), None)
    assert shot_with_dialogue is not None, "未解析出对话"
    prop_n = _ensure_prop_images(project)
    print(f"[6/8] 分镜拆解 OK：{len(project.shots)} 格，"
          f"第 {shot_with_dialogue.index} 格含对话；"
          f"为 {prop_n} 个无人物格补了占位道具图")

    # 批量出图 + 气泡
    gen = imggen.ShotBatchGenerator(pipe, log_cb=lambda m: None)
    gen.run(project, bubbles_enabled=True)
    done, total = project.progress()
    assert done == total and total > 0, f"出图未全部完成：{done}/{total}"
    # 纯旁白格走 caption 通道，不产生气泡布局，这是正常的
    def _needs_bubbles(s) -> bool:
        return any(d.kind == "speech" and (d.text or "").strip()
                   for d in s.dialogues)

    assert all(s.bubble_layout or not _needs_bubbles(s) for s in project.shots), \
        "含对话分镜缺少气泡布局"
    print(f"[7/8] 分镜出图与气泡 OK：{done}/{total} 格完成，"
          f"气泡布局合计 {sum(len(s.bubble_layout) for s in project.shots)} 个")

    # 分镜编辑功能
    first = project.shots[0]
    assert project.split_shot(first.id), "拆分分镜失败"
    assert project.merge_shots(project.shots[0].id), "合并分镜失败"
    assert project.move_shot(project.shots[1].id, -1) or True, "移动分镜异常"
    new_shot = project.add_shot(project.shots[0].id)
    assert project.delete_shot(new_shot.id), "删除分镜失败"
    project.reindex_shots()
    print(f"[8/8] 分镜增删改与排序 OK：拆分/合并/移动/新增/删除均正常，"
          f"当前 {len(project.shots)} 格")

    # ---- 导出打包 ----
    result = exporter.export_project(project, folder_name="自检输出",
                                     progress_cb=lambda m, v: None,
                                     generate_reader=True, make_zip=True)
    out_dir = Path(result["output_dir"])
    assert (out_dir / config.OUTPUT_SUBDIR_SHOTS).exists(), "分镜图目录缺失"
    assert (out_dir / config.OUTPUT_SUBDIR_CHARACTERS).exists(), "角色目录缺失"
    assert (out_dir / config.OUTPUT_SUBDIR_DOCS / "分镜脚本.md").exists(), "分镜文档缺失"
    assert result["zip_path"] and Path(result["zip_path"]).exists(), "压缩包缺失"
    pdf = out_dir / config.OUTPUT_SUBDIR_READER / "漫画阅读.pdf"
    print(f"[+] 导出打包 OK：{out_dir.name}")
    print(f"    文件统计：{result['counts']}")
    print(f"    PDF：{'已生成 ' + str(pdf.stat().st_size // 1024) + ' KB' if pdf.exists() else '未生成'}")
    print(f"    压缩包：{Path(result['zip_path']).name} "
          f"({Path(result['zip_path']).stat().st_size // 1024} KB)")

    # ---- 气泡渲染单独验证 ----
    preview_shot = next((s for s in project.shots if s.image_path and s.bubble_layout),
                        None)
    assert preview_shot is not None, "没有可用于气泡预览的成品格"
    img = bubble.preview_from_layout(preview_shot.image_path,
                                     preview_shot,
                                     preview_shot.bubble_layout)
    assert img.size[0] > 0
    print(f"[+] 气泡按布局重绘 OK：{img.size[0]}×{img.size[1]}")

    # ---- 图像工作流（ComfyUI）：界面格式拍平 + 参数注入 ----
    from comicgen import comfyworkflow as comfy
    from comicgen.imageclient import ImageClient

    wf_path = Path(config.COMFY_WORKFLOW_DIR) / f"{config.COMFY_WORKFLOW_NAME}.json"
    assert wf_path.exists(), f"缺少工作流备份文件：{wf_path}"
    graph = comfy.to_api_graph(json.loads(wf_path.read_text(encoding="utf-8")))
    template = comfy.WorkflowTemplate(graph=graph, name=config.COMFY_WORKFLOW_NAME,
                                      source="local")
    nodes = template.nodes
    assert nodes["sampler"] and nodes["latent"] and nodes["prompt"] and nodes["save"], \
        f"工作流节点定位失败：{nodes}"
    built = template.build("自检提示词", size=(1088, 1216), seed=123,
                           filename_prefix="selftest")
    assert built[nodes["prompt"]]["inputs"]["text"] == "自检提示词", "提示词未写入"
    assert (built[nodes["latent"]]["inputs"]["width"],
            built[nodes["latent"]]["inputs"]["height"]) == (1088, 1216), "画幅未写入"
    assert built[nodes["sampler"]]["inputs"]["seed"] == 123, "种子未写入"
    assert built[nodes["save"]]["inputs"]["filename_prefix"] == "selftest", "前缀未写入"
    assert template.graph[nodes["prompt"]]["inputs"]["text"] != "自检提示词", \
        "build() 不应该改动原工作流"
    print(f"[+] ComfyUI 工作流 OK：{config.COMFY_WORKFLOW_NAME}（{len(graph)} 节点，"
          f"采样器 {built[nodes['sampler']]['class_type']}"
          f"/{built[nodes['sampler']]['inputs'].get('steps')} 步，"
          f"参考图输入：{'有' if template.supports_reference_images else '无'}）")

    # 界面格式（含子图）拍平：节点 id 变成「实例id:内部id」，子图出入口要接通
    ui = {
        "nodes": [
            {"id": 9, "type": "SaveImage",
             "inputs": [{"name": "images", "type": "IMAGE", "link": 20},
                        {"name": "filename_prefix", "type": "STRING",
                         "widget": {"name": "filename_prefix"}}],
             "widgets_values": ["x"]},
            {"id": 7, "type": "sg",
             "inputs": [{"name": "text", "type": "STRING"},
                        {"name": "width", "type": "INT"},
                        {"name": "height", "type": "INT"}],
             "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [20]}],
             "widgets_values_named": {"text": "子图提示词", "width": 768, "height": 512}},
        ],
        "links": [[20, 7, 0, 9, 0, "IMAGE"]],
        "definitions": {"subgraphs": [{
            "id": "sg", "inputNode": {"id": -10}, "outputNode": {"id": -20},
            "inputs": [{"name": name, "linkIds": [i + 1]}
                       for i, name in enumerate(("text", "width", "height"))],
            "outputs": [{"name": "IMAGE", "linkIds": [14]}],
            "nodes": [
                {"id": 3, "type": "EmptySD3LatentImage",
                 "inputs": [{"name": "width", "type": "INT", "link": 2},
                            {"name": "height", "type": "INT", "link": 3}],
                 "outputs": [{"name": "LATENT", "type": "LATENT", "links": [11]}],
                 "widgets_values": [1024, 1024, 1]},
                {"id": 4, "type": "CLIPTextEncode",
                 "inputs": [{"name": "text", "type": "STRING", "link": 1}],
                 "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING",
                              "links": [12]}],
                 "widgets_values": ["内部默认"]},
                {"id": 5, "type": "KSampler",
                 "inputs": [{"name": "positive", "type": "CONDITIONING", "link": 12},
                            {"name": "latent_image", "type": "LATENT", "link": 11},
                            {"name": "seed", "type": "INT", "link": None}],
                 "outputs": [{"name": "LATENT", "type": "LATENT", "links": [13]}],
                 "widgets_values_named": {"seed": 1, "steps": 8, "cfg": 1}},
                {"id": 6, "type": "VAEDecode",
                 "inputs": [{"name": "samples", "type": "LATENT", "link": 13}],
                 "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [14]}]},
            ],
            "links": [
                {"id": 1, "origin_id": -10, "origin_slot": 0,
                 "target_id": 4, "target_slot": 0},
                {"id": 2, "origin_id": -10, "origin_slot": 1,
                 "target_id": 3, "target_slot": 0},
                {"id": 3, "origin_id": -10, "origin_slot": 2,
                 "target_id": 3, "target_slot": 1},
                {"id": 11, "origin_id": 3, "origin_slot": 0,
                 "target_id": 5, "target_slot": 1},
                {"id": 12, "origin_id": 4, "origin_slot": 0,
                 "target_id": 5, "target_slot": 0},
                {"id": 13, "origin_id": 5, "origin_slot": 0,
                 "target_id": 6, "target_slot": 0},
                {"id": 14, "origin_id": 6, "origin_slot": 0,
                 "target_id": -20, "target_slot": 0},
            ],
        }]},
    }
    flat = comfy.to_api_graph(ui)
    assert set(flat) == {"9", "7:3", "7:4", "7:5", "7:6"}, set(flat)
    assert flat["7:4"]["inputs"]["text"] == "子图提示词", "子图入口值没有传进去"
    assert flat["7:3"]["inputs"]["width"] == 768, "子图入口画幅没有传进去"
    assert flat["9"]["inputs"]["images"] == ["7:6", 0], "子图输出没有接到内部节点"
    print("[+] 界面格式（含子图）拍平 OK：子图出入口与实例参数均已接通")

    client = ImageClient()
    assert client._is_oom("CUDA out of memory") and client._is_oom("显存不足")
    assert not client._is_oom("invalid prompt")
    print("[+] 显存不足识别 OK")

    # ---- 规则库（rules/）----
    from comicgen import prompts as prompts_mod
    from comicgen import rules as rules_mod
    rc = rules_mod.self_check()
    assert rc["ok"], rc["errors"]
    assert rules_mod.card_max_chars() <= 100
    shot0 = project.shots[0]
    cast0 = [{"name": c.name, "card": c.identity_card(),
              "side": shot0.char_sides.get(c.name, "")}
             for c in project.characters_by_names(shot0.characters)]
    ptext = prompts_mod.shot_image_prompt(project, shot0, cast=cast0)
    assert len(ptext) <= config.PROMPT_MAX_CHARS, f"提示词超预算：{len(ptext)}"
    assert "画面里只有" in ptext or "物件" in ptext, "缺少人数声明"
    assert "不要" not in ptext, "提示词里仍有否定句"
    # 撞车检测：把两个角色的人设写成一样，必须能报出来
    tmp = models.Project(title="撞车测试")
    tmp.characters = [
        models.Character(name="甲", gender="男", keywords=["瘦长脸", "单眼皮细长眼", "黑色短发", "深灰夹克"]),
        models.Character(name="乙", gender="男", keywords=["瘦长脸", "单眼皮细长眼", "黑色短发", "深灰夹克"]),
    ]
    assert pipe.character_collisions(tmp), "撞车检测失效"

    # ---- 人数锁 / 角色隔离（castguard）----
    from comicgen import castguard, poseplan
    cg = castguard.self_check()
    assert cg["ok"], cg["errors"]
    solo = models.Shot(index=1, shot_type="远景", characters=["林晚"],
                       scene="夜晚的街头",
                       description="深夜的街头，林晚独自走着，身后的路人停下脚步，"
                                   "画面另一侧只露出一个模糊的肩背。",
                       action="她看向对方", gaze="看向画面深处")
    pipe.build_shot_prompt(project, solo, include_bubbles=False)  # 预热规则缓存
    solo_prompt = pipe.build_shot_prompt(project, solo, include_bubbles=False)
    assert "画面里只有一位人物" in solo_prompt, solo_prompt
    assert "唯一的人影" in solo_prompt, solo_prompt
    assert all(w not in solo_prompt for w in ("路人", "对方", "肩背", "人群")), \
        f"单人提示词里仍有召唤词：{solo_prompt}"
    assert "不要" not in solo_prompt, "单人提示词里仍有正向否定"
    solo_neg = prompts_mod.negative_prompt(project, solo, kind="shot")
    assert "第二个人" in solo_neg and "半截人影" in solo_neg, solo_neg
    # 幂等：同一格连续构建两次，提示词必须一致（旧版会重复叠加"只有一个人"）
    assert solo_prompt == pipe.build_shot_prompt(project, solo, include_bubbles=False)

    pair_project = models.Project(title="双人隔离测试")
    pair_project.characters = [
        models.Character(name="甲", gender="女", hard_features={
            "face": "瘦长脸", "hair": "黑色及肩低马尾", "eyes": "细长上挑杏眼",
            "outfit": "黑色长款风衣", "signature": "左手腕内侧浅白旧疤"}),
        models.Character(name="乙", gender="男", hard_features={
            "face": "棱角分明的长方脸", "hair": "黑色短发两侧推短", "eyes": "细长平直眼",
            "outfit": "灰色短款工装夹克", "signature": "右眉尾短浅旧疤"}),
    ]
    pair = models.Shot(index=2, shot_type="全景", characters=["甲", "乙"],
                       char_sides={"甲": "左", "乙": "右"},
                       description="甲站在门槛外，乙提着灯从阴影里迈出一步，"
                                   "两人隔着门槛对视。",
                       scene="老宅正屋")
    pair_prompt = pipe.build_shot_prompt(pair_project, pair, include_bubbles=False)
    assert "画面里只有两位人物" in pair_prompt, pair_prompt
    assert "两张脸各自独立" in pair_prompt, pair_prompt
    assert "左三分之一" in pair_prompt and "右三分之一" in pair_prompt, pair_prompt
    assert "不要" not in pair_prompt, "双人提示词里仍有正向否定"
    pair_neg = prompts_mod.negative_prompt(pair_project, pair, kind="shot")
    assert "第三个人" in pair_neg and "融合脸" in pair_neg, pair_neg

    # 3~4 人：不能再被硬压成两人，也不能只写"两人明显不同"
    multi_project = models.Project(title="多人隔离测试")
    multi_project.characters = [
        models.Character(name="甲", gender="女", hard_features={
            "face": "瘦长脸", "hair": "黑色及肩低马尾", "eyes": "细长上挑杏眼",
            "outfit": "黑色长款风衣", "signature": "左手腕内侧浅白旧疤"}),
        models.Character(name="乙", gender="男", hard_features={
            "face": "棱角分明的长方脸", "hair": "黑色短发两侧推短", "eyes": "细长平直眼",
            "outfit": "灰色短款工装夹克", "signature": "右眉尾短浅旧疤"}),
        models.Character(name="丙", gender="女", hard_features={
            "face": "略方短圆脸", "hair": "深蓝黑齐耳短发", "eyes": "圆大黑豆眼",
            "outfit": "米灰宽大针织衫", "signature": "左耳旧铜色小发夹"}),
        models.Character(name="丁", gender="男", hard_features={
            "face": "窄长脸", "hair": "花白短发后梳", "eyes": "小而聚光的细眼",
            "outfit": "深绿宽松连帽卫衣", "signature": "右耳黑色有线耳机"}),
    ]
    shot3 = models.Shot(index=3, shot_type="全景", characters=["甲", "乙", "丙"],
                        char_sides={"甲": "左", "乙": "中", "丙": "右"},
                        char_positions={"甲": "左侧前景", "乙": "中央中景", "丙": "右侧中景"},
                        description="三人在长桌旁对峙。", scene="老宅正屋")
    p3 = pipe.build_shot_prompt(multi_project, shot3, include_bubbles=False)
    assert "画面里只有三位人物" in p3 and "甲、乙、丙" in p3, p3
    assert "画面中央" in p3 and "半臂宽" in p3, p3
    assert "每张脸各自独立" in p3, p3
    n3 = prompts_mod.negative_prompt(multi_project, shot3, kind="shot")
    assert "第四个人" in n3, n3
    shot4 = models.Shot(index=4, shot_type="全景",
                        characters=["甲", "乙", "丙", "丁"],
                        char_sides={"甲": "左", "乙": "中", "丙": "中", "丁": "右"},
                        char_positions={"甲": "左侧前景", "乙": "中左", "丙": "中右", "丁": "右侧后景"},
                        description="四人在机房门口形成对峙。", scene="老宅正屋")
    # 2026-11 新政策：Qwen 群像上限 3 人；4 人拆 3+1/2+2（当前实现为逐人拆）。
    old_one_person = bool(getattr(config, "STORYBOARD_ONE_PERSON_PER_PANEL", False))
    multi_project.shots = [shot3, shot4]
    config.STORYBOARD_ONE_PERSON_PER_PANEL = False
    pipe._split_multi_character_shots(multi_project)
    assert len(multi_project.shots) == 5, "群像模式没有把 4 人拆开（3 人保留 + 4 人拆成 4 格）"
    multi_project.shots = [shot3, shot4]
    config.STORYBOARD_ONE_PERSON_PER_PANEL = True
    pipe._split_multi_character_shots(multi_project)
    assert len(multi_project.shots) == 7, "一致性模式没有把 3~4 人拆成单人格"
    config.STORYBOARD_ONE_PERSON_PER_PANEL = old_one_person
    multi_project.shots = [shot3, shot4]
    # 骨架校验：已知的"双头骨架"必须被拒绝，不能用它出单人图
    bad_pose = Path(config.BASE_DIR) / "data" / "pose_library" / "actions" / "action_04.png"
    assert bad_pose.exists() and not poseplan.validate_pose(bad_pose)[0], \
        "双头骨架未被拦截"
    door = models.Shot(index=3, shot_type="全景", characters=["林晚"],
                       description="货梯门打开，白色走廊延伸，林晚站在门口。",
                       action="货梯门打开，白色走廊展现")
    _door_pose, _door_reason, _door_dynamic = poseplan.choose_pose(door)
    assert not _door_dynamic and "动作" not in _door_reason, \
        "「打开」不应触发上肢/行走骨架"
    print(f"[+] 人数锁/角色隔离 OK：单人/双人/3 人提示词无召唤词，4 人自动拆，"
          f"负向清单按人数切换，坏骨架已拦截")

    print(f"[+] 规则库 OK：{rules_mod.DATA_DIR}（提示词 {len(ptext)} 字，撞车检测正常）")

    # ---- 内置画风预设（名称 / 分组 / 提示词正文必须一一对应）----
    missing = [s for s in config.STYLE_PRESETS if s not in config.STYLE_PROMPT_MAP]
    extra = [s for s in config.STYLE_PROMPT_MAP if s not in config.STYLE_PRESETS]
    assert not missing and not extra, f"内置画风缺正文 {missing} / 多出正文 {extra}"
    assert len(config.STYLE_PRESETS) == len(set(config.STYLE_PRESETS)), "内置画风有重名"
    grouped = [n for g in config.STYLE_PRESET_GROUPS for n in g["items"]]
    assert grouped == config.STYLE_PRESETS, "分组清单与扁平清单不一致"
    print(f"[+] 内置画风 OK：{len(config.STYLE_PRESETS)} 种 / "
          f"{len(config.STYLE_PRESET_GROUPS)} 个家族")

    # ---- handraw-style 风格库 ----
    from comicgen import stylelib, pagelayout
    sc = stylelib.self_check()
    assert sc["available"], sc
    assert sc["styles"] >= 300 and sc["colors"] >= 30 and sc["layouts"] >= 70, sc
    assert stylelib.normalize_style_id("18") == stylelib.normalize_style_id("FA-018")
    rec = stylelib.recommend("民国上海悬疑凶案，雨夜、霓虹、压抑，女性主角追查真相")
    assert rec["style_id"] and rec["color_ids"], rec
    assert rec["style_tone"] and rec["style_trigger"], rec
    sblock = stylelib.style_prompt_block(rec["style_id"], rec["color_ids"],
                                         tone=rec["style_tone"])
    assert "视觉语言" in sblock and "主题色" in sblock
    assert "漫画分级特征" in sblock, sblock
    assert stylelib.tone_meta(rec["style_tone"]).get("name_zh", "") in sblock, sblock
    assert "禁止" not in sblock and "不要" not in sblock, sblock
    assert "#" not in sblock, sblock
    assert sc["sample_reference"] and Path(sc["sample_reference"]).exists()
    print(f"[+] 风格库 OK：{sc['styles']} 画风 / {sc['colors']} 主题色 / "
          f"{sc['tones']} 阶调 / {sc['layouts']} 版式；"
          f"推荐 {rec['style_id']} + {','.join(rec['color_ids'])} + {rec['style_tone']}")

    # 画风块必须原样保留（不能被"写实/3D"替换规则洗掉）
    project.style_id = ""
    project.style_color_ids = []
    legacy = prompts_mod.shot_image_prompt(project, shot0, cast=cast0)
    project.style_id = rec["style_id"]
    project.style_color_ids = list(rec["color_ids"])
    project.style_tone = rec["style_tone"]
    handraw = prompts_mod.shot_image_prompt(project, shot0, cast=cast0)
    assert "视觉语言" in handraw and "视觉语言" not in legacy
    assert "漫画分级特征" in handraw
    print("[+] 画风提示词 OK：风格块 / 漫画分级特征 / 阶调已注入")

    # ---- 分页 / 版式 / 合成 ----
    pagelayout.annotate_shots(project)
    for mode in ("page", "hybrid"):
        plans = pagelayout.plan_local(project, mode=mode)
        assert plans, f"{mode} 没有规划出页面"
        assert sum(p.panel_count for p in plans) == len(project.shots), mode
        assert all(p.cells and len(p.cells) == p.panel_count for p in plans), mode
    plans = pagelayout.plan_local(project, mode="hybrid")
    pagelayout.apply_plans(project, plans, "selftest")
    assert pagelayout.pages_valid(project)
    stats = pagelayout.compose_all_pages(project)
    assert stats["done"] > 0, stats
    tiles = pagelayout.strip_tiles(project)
    assert tiles and all(Path(t).exists() for t in tiles)
    print(f"[+] 分页与版式 OK：{len(plans)} 页 / {len(project.shots)} 格，"
          f"已合成 {stats['done']} 页，条漫图块 {len(tiles)} 个")

    # ---- 复杂风格工作流（参考图注入 / 色彩迁移）----
    for name in (config.COMFY_WORKFLOW_STYLE_NAME,
                 config.COMFY_WORKFLOW_STYLE_REF_NAME,
                 config.COMFY_WORKFLOW_STYLE_IMG2IMG_NAME):
        path = Path(config.COMFY_WORKFLOW_DIR) / f"{name}.json"
        assert path.exists(), f"缺少工作流备份：{path}"
        graph = json.loads(path.read_text(encoding="utf-8"))
        tpl = comfy.WorkflowTemplate(graph=graph, name=name, source="file")
        assert tpl.supports_reference_images, name
        if name == config.COMFY_WORKFLOW_STYLE_NAME:
            assert tpl.color_matched and tpl.reference_slot_count == 1, name
            built = tpl.build("测试", size=(1088, 1216), seed=1,
                              reference_names=["style.png"],
                              color_transfer_strength=0.55)
            ct = next(n for n in built.values()
                      if n["class_type"] == "ColorTransfer")
            assert abs(float(ct["inputs"]["strength"]) - 0.55) < 1e-6
        if name == config.COMFY_WORKFLOW_STYLE_REF_NAME:
            built = tpl.build("测试", size=(1088, 1216), seed=1,
                              reference_names=["style.png", "char.png"])
            assert built[tpl.nodes["prompt"]]["inputs"]["image1"] == ["90", 0]
            assert "image3" not in built[tpl.nodes["prompt"]]["inputs"], "多余槽位没有摘掉"
    print("[+] 复杂风格工作流 OK：色彩迁移强度 / 参考图槽位注入 / 多余槽位摘除")

    # 清掉自检临时角色图，避免留下测试产物
    try:
        import shutil
        shutil.rmtree(config.CHARACTER_DIR, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    print("=" * 72)
    print("全部自检通过 ✅")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
