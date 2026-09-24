from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import plan_output

try:
    from scripts.parser import ddl_to_model
    from scripts.renderer import render_diagram_png
except ImportError:
    from parser import ddl_to_model
    from renderer import render_diagram_png


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ER diagram PNG from SQL DDL")
    parser.add_argument("--sql-file", required=True, help="Path to SQL DDL file")
    parser.add_argument("--output", "--out", default=None, help="Output PNG path (default: auto-detect from input file)")
    parser.add_argument("--dialect", default="mysql", help="SQL dialect for sqlglot")
    parser.add_argument("--scale", type=int, default=4, help="Render scale factor (default: 4, higher = more pixels)")
    parser.add_argument("--downsample", action="store_true", help="Downsample output to 1x (default: output high-res)")
    args = parser.parse_args()

    sql_text = Path(args.sql_file).read_text(encoding="utf-8")
    model = ddl_to_model(sql_text, dialect=args.dialect)

    png = render_diagram_png(model, scale=args.scale, downsample_output=args.downsample)

    plan = plan_output("drawing", "diagram-er", "er-diagram.png", explicit=args.output)
    output_path = plan.primary

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(png)

    plan.commit()
    print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()
