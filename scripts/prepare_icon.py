#!/usr/bin/env python3
"""把带白色画布的应用图标处理成紧边界透明 PNG。

脚本只删除与画布四周连通的近白色背景，因此不会误删被图标主体包围的白色标志。
Pillow 仅用于开发期素材处理，不加入 Web 服务运行时依赖。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="原始 PNG 路径")
    parser.add_argument("output", type=Path, help="主图标输出路径")
    parser.add_argument("--size", type=int, default=512, help="主图标边长，默认 512")
    parser.add_argument(
        "--apple-touch-output",
        type=Path,
        help="可选的 Apple Touch 图标输出路径",
    )
    parser.add_argument(
        "--apple-touch-size",
        type=int,
        default=180,
        help="Apple Touch 图标边长，默认 180",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=96,
        help="外部近白色背景的容差，默认 96",
    )
    return parser.parse_args()


def clear_external_background(image: Image.Image, threshold: int) -> Image.Image:
    """仅清除从画布四角可达的近白色区域，再裁剪到实际像素边界。"""

    if not 0 <= threshold <= 255:
        raise ValueError("threshold 必须在 0 到 255 之间")

    rgba = image.convert("RGBA")
    corners = (
        (0, 0),
        (rgba.width - 1, 0),
        (0, rgba.height - 1),
        (rgba.width - 1, rgba.height - 1),
    )
    for corner in corners:
        pixel = rgba.getpixel(corner)
        if pixel[3] and min(pixel[:3]) >= 255 - threshold:
            ImageDraw.floodfill(rgba, corner, (0, 0, 0, 0), thresh=threshold)

    bbox = rgba.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("背景清除后没有剩余图像，请降低 threshold")

    cropped = rgba.crop(bbox)
    # 全透明像素统一归零，防止 Lanczos 缩放时把原白底颜色带到边缘。
    cropped.putdata(
        [
            (0, 0, 0, 0) if alpha == 0 else (red, green, blue, alpha)
            for red, green, blue, alpha in cropped.get_flattened_data()
        ]
    )
    return cropped


def save_resized(image: Image.Image, output: Path, size: int) -> None:
    if size <= 0:
        raise ValueError("图标尺寸必须大于 0")
    output.parent.mkdir(parents=True, exist_ok=True)
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(
        image,
        dest=((side - image.width) // 2, (side - image.height) // 2),
    )
    square.resize((size, size), Image.Resampling.LANCZOS).save(output, optimize=True)


def main() -> None:
    args = parse_args()
    with Image.open(args.input) as source:
        icon = clear_external_background(source, args.threshold)

    save_resized(icon, args.output, args.size)
    if args.apple_touch_output:
        save_resized(icon, args.apple_touch_output, args.apple_touch_size)

    opaque_white_pixels = sum(
        1
        for red, green, blue, alpha in icon.get_flattened_data()
        if alpha > 250 and min(red, green, blue) > 240
    )
    print(
        f"已输出 {args.output}：裁剪后 {icon.width}×{icon.height}，"
        f"保留内部白色像素 {opaque_white_pixels} 个"
    )


if __name__ == "__main__":
    main()
