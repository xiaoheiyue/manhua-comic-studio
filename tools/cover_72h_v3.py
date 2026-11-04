# -*- coding: utf-8 -*-
"""《第七十二小时，他没有自杀》封面底图（第三版，为标题留白）。

只出无字底图。标题、72、角标全部由 cover_72h_compose.py 程序叠加，
避免 Z-Image 写中文出现错字或把标题画成画面里的招牌。
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
    "构图：一位三十二岁左右的女性背影站在画面右侧三分之一，短发及肩，"
    "穿深色长风衣，背对镜头，只看到后脑、短发与肩背；她微微侧身，"
    "面向身前一屏悬空亮起的冷蓝色数据屏。屏幕上有密集的横向扫描线、时间码与细密字符，"
    "冷光从侧前方勾亮她的发梢、耳廓与肩线，另一道更冷的蓝光从右后方勾出风衣边缘。"
    "画面左侧三分之二保持大块深蓝近黑的雨雾与卷帘门暗部，墙面只有雨痕与极弱的冷光反射，"
    "这片暗部干净、完整，不放屏幕、不放人物、不放亮色物件，留给封面标题。"
    "右下角只占一条窄前景：旧木桌的一角，桌面上放一块金属U盘，红色指示灯亮着；"
    "旁边是一只停在六点零三分的旧手表，表盘有细微划痕，表带压着桌面。"
    "地面潮湿，远处卷帘门缝透出一线冷白。"
    "电影级低调光，冷蓝主色，局部勃艮第红点缀，精密构图，压抑、悬疑、克制，"
    "浅景深，胶片颗粒，高动态范围。"
    "画面里只有这一位女性，背对镜头，短发，深色长风衣；"
    "纯画面，没有标题，没有文字排版。"
)

NEG = (
    "低质量, 模糊, 变形, 多余手指, 水印, 签名, 可读文字, 乱码文字, 标题, 字幕, "
    "海报文字, 汉字, 中文, 书法, 标语, 招牌, 海报, 大字, 字体, 文字排版, 书封, "
    "边框, 数字, 人群, 第二个人, 多余人物, 正面脸, 微笑, 卡通, 日系萌系, 低幼, "
    "动画, 二次元, 漫画线稿, 大眼睛, 塑料皮肤, 过度磨皮, 3D渲染, 平涂, 韩漫画风, "
    "亮色调, 高饱和, 暖色调, 白天, 画中画, 分屏, 多格"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="720517,721103")
    ap.add_argument("--tag", default="v3")
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
