"""命令行入口

支持两种渲染引擎：
  - echarts (默认): Playwright + Chromium + ECharts 5.5.0 力导向布局
    参考实现：G:\\Kimi_Agent_ECharts ER图定制
    优点：自动布局、连线不重叠、视觉接近 ECharts 参考效果
    缺点：需要 Chromium（系统已预装 ms-playwright 缓存）
  - pillow (旧版兜底): 手动坐标布局 + Pillow 绘制
    优点：无外部依赖（除 Pillow）
    缺点：需手动写坐标，效果粗糙
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import plan_output

from scripts.renderer import render_er_diagram


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ER diagram PNG (multi-entity, supports attributes)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 默认：deterministic 布局（Graphviz neato，0 交叉）+ ECharts 渲染（推荐）
  python -m scripts.cli --json-file er.json
  # 纯 ECharts 力导向布局（可能存在连线交叉，节点很多时适合）
  python -m scripts.cli --json-file er.json --layout force
  # 使用 JSON 中的 x/y 坐标固定布局
  python -m scripts.cli --json-file er.json --layout none
  # 旧版 Pillow 引擎（手动坐标，无 Chromium 时使用）
  python -m scripts.cli --json-file er.json --engine pillow
  # 自定义输出路径
  python -m scripts.cli --json-file er.json --output diagram.png
        """,
    )
    parser.add_argument("--json-file", required=True, help="Path to JSON data file")
    parser.add_argument("--output", "--out", default=None, help="Output PNG path (default: auto-detect from input file)")
    parser.add_argument(
        "--engine",
        choices=["echarts", "pillow"],
        default="echarts",
        help="Render engine: echarts (default, ECharts force layout) or pillow (legacy manual layout)",
    )
    # ---- ECharts 引擎：布局算法 ----
    parser.add_argument(
        "--layout",
        choices=["deterministic", "force", "none"],
        default="deterministic",
        help="[echarts] Layout algorithm. 'deterministic' (default): Graphviz neato computes "
             "cross-free initial coordinates, ECharts renders with layout='none'. "
             "'force': pure ECharts force-directed layout (may have crossings). "
             "'none': use x/y from JSON as-is.",
    )
    # ---- ECharts 引擎选项 ----
    parser.add_argument("--width", type=int, default=1600, help="[echarts] Canvas width in pixels")
    parser.add_argument("--height", type=int, default=1100, help="[echarts] Canvas height in pixels")
    parser.add_argument("--pixel-ratio", type=float, default=2.0, help="[echarts] Screenshot pixel ratio (2.0 = high-DPI)")
    parser.add_argument("--repulsion", type=int, default=600, help="[echarts] Force layout repulsion")
    parser.add_argument("--gravity", type=float, default=0.1, help="[echarts] Force layout gravity")
    parser.add_argument("--edge-min", type=int, default=80, help="[echarts] Force layout min edge length")
    parser.add_argument("--edge-max", type=int, default=180, help="[echarts] Force layout max edge length")
    parser.add_argument("--friction", type=float, default=0.6, help="[echarts] Force layout friction")
    parser.add_argument("--settle-ms", type=int, default=2500, help="[echarts] Max ms to wait for force layout to converge")
    parser.add_argument("--no-legend", action="store_true", help="[echarts] Hide bottom legend")
    parser.add_argument("--no-force", action="store_true", help="[echarts] Use x/y from JSON (layout='none') instead of force layout")
    # ---- Pillow 引擎选项（兼容旧版）----
    parser.add_argument("--safe-margin", type=int, default=24, help="[pillow] Safe white margin in pixels after auto-crop")
    parser.add_argument("--no-auto-crop", action="store_true", help="[pillow] Disable automatic content-based crop")
    parser.add_argument("--scale", type=int, default=None, help="[pillow] Render scale factor (higher = more pixels)")
    parser.add_argument("--downsample", action="store_true", help="[pillow] Downsample output to 1x (default: high-res)")
    args = parser.parse_args()

    json_data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))

    if args.engine == "echarts":
        from scripts.echarts_renderer import render_echarts_png, ensure_echarts_vendor
        ensure_echarts_vendor()
        png = render_echarts_png(
            json_data,
            width=args.width,
            height=args.height,
            pixel_ratio=args.pixel_ratio,
            repulsion=args.repulsion,
            gravity=args.gravity,
            edge_min=args.edge_min,
            edge_max=args.edge_max,
            friction=args.friction,
            settle_ms=args.settle_ms,
            show_legend=not args.no_legend,
            use_force=not args.no_force,
            layout=args.layout,
        )
    else:
        kwargs = dict(
            auto_crop=not args.no_auto_crop,
            safe_margin=max(0, args.safe_margin),
            downsample_output=args.downsample,
        )
        if args.scale is not None:
            kwargs["scale"] = args.scale
        png = render_er_diagram(json_data, **kwargs)

    plan = plan_output("drawing", "diagram-ers", "ers-diagram.png", explicit=args.output)
    output_path = plan.primary

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(png)
    plan.commit()
    print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()