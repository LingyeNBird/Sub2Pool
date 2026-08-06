#!/usr/bin/env python3
"""把 Vite 产物复制到 Django 静态目录，并生成引用哈希资源的模板。"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUILD_DIR = ROOT / "frontend" / "dist"
STATIC_DIR = ROOT / "backend" / "static" / "frontend"
TEMPLATE_FILE = ROOT / "backend" / "templates" / "index.html"
ASSET_REFERENCE = re.compile(
    r'(?P<attribute>\b(?:src|href)=")/static/frontend/(?P<path>assets/[^"]+)"'
)


def build_template(build_dir: Path) -> None:
    """同步完整前端产物，并把入口中的资源 URL 转成 Django static 标签。"""
    source_index = build_dir / "index.html"
    if not source_index.is_file():
        raise SystemExit(f"找不到 Vite 入口文件：{source_index}")

    # 每次完整替换目录，避免旧哈希文件长期残留在镜像或源码树中。
    if STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
    shutil.copytree(build_dir, STATIC_DIR)

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    def static_reference(match: re.Match[str]) -> str:
        asset_path = f"frontend/{match.group('path')}"
        return f'{match.group("attribute")}{{% static \'{asset_path}\' %}}"'

    template = "{% load static %}\n" + ASSET_REFERENCE.sub(static_reference, html)
    if (
        'src="/static/frontend/assets/' in template
        or 'href="/static/frontend/assets/' in template
        or "{% static " not in template
    ):
        raise SystemExit("无法把 Vite 资源路径转换为 Django static 标签")

    TEMPLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_FILE.write_text(template, encoding="utf-8")
    print(f"已生成 {TEMPLATE_FILE.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "build_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="Vite dist 目录，默认 frontend/dist",
    )
    args = parser.parse_args()
    build_template(args.build_dir.resolve())


if __name__ == "__main__":
    main()
