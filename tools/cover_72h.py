# -*- coding: utf-8 -*-
"""《第七十二小时，他没有自杀》封面底图生成。

先出无字底图，再用 cover_72h_compose.py 叠标题；
这样不会让模型去画中文标题（Z-Image 的中文字形不稳定）。
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
    "竖版电影剧照质感插画，2:3 竖幅，35mm 胶片，真实材质。"
    "画面右侧三分之一是一位三十二岁左右的女性背影，短发及肩，穿深色长风衣，"
    "背对镜头，只看到短发与肩背轮廓；她侧身站在雨夜旧巷半开的卷帘门前，"
    "身前是一块悬空亮起的深色数据屏，屏幕上有密集的横向扫描线、时间码与细密字符，"
    "屏幕冷光从侧前方照亮她的发梢；画面右后方另有一道冷蓝色轮廓光，"
    "勾出她的肩线与风衣边缘。"
    "画面左侧三分之二是干净的深蓝色雨雾与卷帘门暗部，墙面只有雨痕与冷光反射。"
    "右下角前景是一张旧木桌，桌角放着一块金属U盘，红色指示灯亮着；"
    "旁边是一只停在六点零三分的旧手表，表盘有细微划痕。"
    "地面潮湿反光，远处卷帘门缝透出一线冷白光。"
    "电影级低调光，冷蓝主色，局部勃艮第红点缀，精密构图，压抑、悬疑、克制，"
    "浅景深，胶片颗粒。"
    "画面里只有这位女性一个人物，背对镜头，短发，深色长风衣，轮廓光，"
    "U盘红灯，停在六点零三分的旧手表。"
    "纯画面插画，画面里只有人物、屏幕、桌面与雨夜环境。"
)

NEG = (
    "低质量, 模糊, 变形, 多余手指, 水印, 签名, 可读文字, 乱码文字, 标题, 字幕, "
    "海报文字, 汉字, 中文, 书法, 标语, 招牌, 海报, 大字, 字体, 文字排版, 书封, 边框, "
    "人群, 第二个人, 多余人物, 正面脸, 微笑, 卡通, 日系萌系, 低幼, 动画, 二次元, "
    "漫画线稿, 大眼睛, 塑料皮肤, 过度磨皮, 3D渲染, 平涂, 韩漫画风, 亮色调, "
    "高饱和, 暖色调, 白天, 画中画, 分屏"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=720301)
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--strength", type=float, default=0.35)
    args = ap.parse_args()

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
    print(f"prompt={len(prompt)} 字")
    client = ImageClient()
    res = client.generate(
        prompt, reference_paths=refs, size=(1024, 1408),
        seed=args.seed, negative_prompt=NEG,
        color_transfer_strength=args.strength, use_cache=False)
    png = OUT / f"cover_72h_{args.tag}_raw.png"
    res.image.save(png)
    (OUT / f"cover_72h_{args.tag}_raw.json").write_text(
        json.dumps({"seed": args.seed, "prompt": prompt, "negative": NEG,
                    "style": style_id, "colors": colors, "tone": tone,
                    "image": str(png)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"-> {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
