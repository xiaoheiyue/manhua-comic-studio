# -*- coding: utf-8 -*-
"""《第七十二小时，他没有自杀》封面底图 v6：无窗干燥档案室。

前几版的问题都出在"雨"和"室内"抢空间：只要画面里有雨，模型就会把地面、
桌面一起画湿。v6 直接去掉窗和雨，做一间无窗的深夜档案室——
空间上没有任何可能下雨的地方，画面只靠数据屏冷光、人物轮廓和桌上物件成立。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comicgen import stylelib  # noqa: E402
from comicgen.imageclient import ImageClient  # noqa: E402

OUT = ROOT / "outputs" / "cover_72h"

SCENE = (
    "竖版电影剧照质感插画，35mm 胶片，真实材质，低调光。"
    "场景是一间没有窗户的深夜档案室兼数据复原工作室，空气干燥，室内安静。"
    "画面右侧三分之一是一位三十二岁左右的女性背影，短发及肩，穿深色长风衣，"
    "背对镜头，面向墙上一块亮起的冷蓝色数据屏；屏幕上有密集的横向扫描线、时间码与哈希字符，"
    "冷光勾亮她的发梢、耳廓与肩线，另一道更冷的蓝光从右后方勾出风衣边缘。"
    "画面左侧三分之二是一整面深色铁皮文件柜与旧砖墙组成的大块暗部，"
    "干净、完整，不放屏幕、不放人物、不放亮色物件，留给封面标题。"
    "右下角只占一条窄前景：一张干燥的旧木桌，桌面上放一块金属U盘，红色指示灯亮着；"
    "旁边是一只停在六点零三分的旧手表，表盘有细微划痕，表带压着干燥的桌面。"
    "地面是干燥的哑光水泥地，没有反光、没有水渍、没有积水；桌面干燥，空气干燥。"
    "室内只有数据屏的冷光与远处一盏极弱的顶灯，尘埃在光柱里浮动。"
    "电影级低调光，冷蓝主色，局部勃艮第红点缀，精密构图，压抑、悬疑、克制，"
    "浅景深，胶片颗粒，高动态范围。"
    "画面里只有这一位女性，背对镜头，短发，深色长风衣；纯画面，没有标题，没有文字排版。"
)

NEG = (
    "雨, 下雨, 雨丝, 雨滴, 雨水, 积水, 水坑, 水滴, 水渍, 潮湿, 湿地面, 湿桌面, "
    "反光地面, 反光桌面, 窗户, 窗外, 室外, 街道, 卷帘门, 雨伞, 水花, 蒸汽, 雾气, "
    "低质量, 模糊, 变形, 多余手指, 水印, 签名, 可读文字, 乱码文字, 标题, 字幕, "
    "海报文字, 汉字, 中文, 书法, 标语, 招牌, 海报, 大字, 字体, 文字排版, 书封, "
    "边框, 数字, 人群, 第二个人, 多余人物, 正面脸, 微笑, 卡通, 日系萌系, 低幼, "
    "动画, 二次元, 漫画线稿, 大眼睛, 塑料皮肤, 过度磨皮, 3D渲染, 平涂, "
    "韩漫画风, 亮色调, 高饱和, 暖色调, 白天, 画中画, 分屏, 多格"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="750311,750917")
    ap.add_argument("--tag", default="v6")
    ap.add_argument("--strength", type=float, default=0.35)
    ap.add_argument("--size", default="1024x1408")
    args = ap.parse_args()

    w, h = (int(x) for x in args.size.lower().split("x"))
    OUT.mkdir(parents=True, exist_ok=True)
    style_id = "FI-030"
    colors = ["C-03", "C-17"]
    tone = "T-15"
    trigger = stylelib.trigger_phrase(style_id, tone, SCENE)
    style_block = stylelib.style_prompt_block(
        style_id, colors, include_reference_note=True, tone=tone,
        trigger=trigger, text=SCENE)
    prompt = f"{style_block}。{SCENE}"
    style_ref = stylelib.style_reference_png(style_id)
    refs = [style_ref] if style_ref else []

    print(f"style={style_id} tone={tone} colors={colors} refs={len(refs)}")
    print(f"prompt={len(prompt)} 字 size={w}x{h}")
    client = ImageClient()
    for seed in (int(s) for s in args.seeds.split(",") if s.strip()):
        res = client.generate(
            prompt, reference_paths=refs, size=(w, h),
            seed=seed, negative_prompt=NEG,
            color_transfer_strength=args.strength, use_cache=False)
        png = OUT / f"cover_72h_{args.tag}_s{seed}_raw.png"
        res.image.save(png)
        (OUT / f"cover_72h_{args.tag}_s{seed}_raw.json").write_text(
            json.dumps({"seed": seed, "prompt": prompt, "negative": NEG,
                        "style": style_id, "colors": colors, "tone": tone,
                        "image": str(png)}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"-> {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
