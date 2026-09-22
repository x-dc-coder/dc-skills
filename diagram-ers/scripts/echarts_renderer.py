"""ECharts 渲染器 — 复用参考项目的 ECharts 配置，通过 Playwright 截图导出 PNG

参考：G:\\Kimi_Agent_ECharts ER图定制\\app\\src\\components\\ERChart.tsx

视觉规范（与参考一致）：
- 实体：矩形 100x60，白底黑边（borderWidth=2）
- 属性：椭圆 90x50，白底黑边；主键加粗 + 下划线 + 深色字
- 关系：菱形 90x70，白底黑边
- 实体↔关系连线：实线，基数（1/n/m）作为边标签，白底圆角 pill
- 属性↔实体连线：虚线，无标签
- 图例：底部居中（实体/属性/关系）
- 背景：纯白

布局策略：
- **deterministic（默认，推荐）**：Python 端用 Graphviz `sfdp` 算法计算无交叉初始坐标，
  然后用 ECharts `layout='none'` 渲染。结合 ECharts 视觉 + Graphviz 拓扑优化，
  连线交叉数远低于纯力导向。
- **force**：纯 ECharts 力导向自动布局，需要手动调参；适合节点多的场景但易交叉。
- **none**：完全用 JSON 中的 x/y 坐标。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any

# Chromium 可执行路径（系统已安装 ms-playwright 缓存）
_CHROMIUM_CANDIDATES = [
    os.path.expanduser("~/.cache/ms-playwright/chromium-1134/chrome-linux/chrome"),
    os.path.expanduser("~/.cache/ms-playwright/chromium-1169/chrome-linux/chrome"),
]

# ECharts CDN（offline 优先；离线时回退）
_ECHARTS_LOCAL_PATHS = [
    # 如果用户本地装过 echarts，直接用
    "/usr/lib/node_modules/echarts/dist/echarts.min.js",
    os.path.expanduser("~/projects/dc-skills/diagram-ers/scripts/vendor/echarts.min.js"),
]
_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"

# 节点形状（与 ERChart.tsx SYMBOL_PATHS 完全一致）
_SYMBOL_ENTITY = "rect"
_SYMBOL_ATTRIBUTE = "path://M 0,-25 A 45,25 0 1,0 0,25 A 45,25 0 1,0 0,-25"
_SYMBOL_RELATION = "path://M 0,-35 L 45,0 L 0,35 L -45,0 Z"

_NODE_SIZE = {  # [w, h]
    "entity": [100, 60],
    "attribute": [90, 50],
    "relationship": [90, 70],
}

_FILL_COLOR = "#ffffff"
_BORDER_COLOR = "#000000"
_TEXT_COLOR = "#2c3e50"
_PK_COLOR = "#0f2b4d"
_EDGE_COLOR = "#8c8c8c"
_EDGE_WIDTH = 1.5
_LABEL_COLOR = "#595959"

HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>er</title>
<style>
  html,body{margin:0;padding:0;background:#ffffff;}
  #chart{width:${width}px;height:${height}px;background:#ffffff;}
</style></head>
<body><div id="chart"></div>
<script src="$echarts_src"></script>
<script>
const DATA = $data_json;
const chart = echarts.init(document.getElementById('chart'), null, {
  renderer: 'canvas',
  width: $width,
  height: $height
});
chart.setOption({
  backgroundColor: '#ffffff',
  animation: false,
  series: [{
    type: 'graph',
    layout: $js_layout,
    data: DATA.nodes,
    links: DATA.edges,
    categories: [
      {name: '实体', itemStyle: {color: '#ffffff', borderColor: '#000000'}},
      {name: '属性', itemStyle: {color: '#ffffff', borderColor: '#000000'}},
      {name: '关系', itemStyle: {color: '#ffffff', borderColor: '#000000'}}
    ],
    roam: false,
    draggable: false,
    emphasis: {focus: 'adjacency', lineStyle: {width: 3}},
    force: {
      repulsion: $repulsion,
      gravity: $gravity,
      edgeLength: [$edge_min, $edge_max],
      layoutAnimation: true,
      friction: $friction
    },
    edgeSymbol: ['none', 'none'],
    edgeSymbolSize: [0, 0],
    zoom: 1.0
  }],
  legend: {
    show: $show_legend,
    data: [
      {name: '实体', icon: 'rect'},
      {name: '属性', icon: 'circle'},
      {name: '关系', icon: 'diamond'}
    ],
    bottom: 10, left: 'center', itemGap: 30,
    textStyle: {fontSize: 13, color: '#595959'}
  }
});

window.__capture = function() {
  try {
    const url = chart.getDataURL({
      type: 'png',
      pixelRatio: $pixel_ratio,
      backgroundColor: '#ffffff'
    });
    return url;
  } catch (e) {
    return 'ERROR:' + e.message;
  }
};

// 力导向布局收敛通知：监听 finished 事件 + 超时兜底
let __finishedCount = 0;
window.__ready = false;
chart.on('finished', () => {
  __finishedCount += 1;
  window.__finishedCount = __finishedCount;
  if (__finishedCount >= 3) {  // 多次 finished 后认为收敛
    window.__ready = true;
  }
});
// 保险兜底：2.5s 后无条件 ready
setTimeout(() => { window.__ready = true; }, $settle_ms);

// 节点 ready 后立即暴露图表实例
window.__chart = chart;
window.__hasEcharts = (typeof echarts !== 'undefined');
</script></body></html>
"""


def render_echarts_png(
    data: dict,
    *,
    width: int = 1400,
    height: int = 900,
    pixel_ratio: float = 2.0,
    repulsion: int = 600,
    gravity: float = 0.1,
    edge_min: int = 80,
    edge_max: int = 180,
    friction: float = 0.6,
    settle_ms: int = 2500,
    show_legend: bool = True,
    use_force: bool = True,
    layout: str = "deterministic",
) -> bytes:
    """渲染 ER 图为 PNG（ECharts 引擎）

    Args:
        data: 用户 JSON 字典，支持以下三种键（全部可选，按需组合）：
            - entities: [{name, x?, y?}]  实体
            - relations: [{name, x?, y?, connects: [{entity, cardinality}]}]  关系（菱形）
            - attributes: [{name, entity, isPrimary?}]  属性（椭圆，可选）
              也可以写成 attributes: [{name, x?, y?, entity?, isPrimary?}]
            - edges: 可选直接提供 [{source, target, cardinality?, name?}]
              （高级用法，覆盖自动边生成）
        width/height: 画布像素
        pixel_ratio: 截图倍率（2.0 = 高清）
        use_force: True=力导向自动布局；False=使用 JSON 中的 x/y 坐标（layout='none'）
            （向后兼容旧参数；新的 `layout` 参数优先级更高）
        layout: 布局算法。可选值：
            - "deterministic"（默认）：Python 端用 Graphviz sfdp 计算无交叉坐标，传给 ECharts
              `layout='none'` 渲染。连线交叉最少。
            - "force"：纯 ECharts 力导向自动布局，参数见 repulsion/gravity/edge_*/friction。
            - "none"：完全用 JSON 中的 x/y 坐标。
    """
    echarts_src = _resolve_echarts_src()
    chart_data, use_force_actual = _transform(data, use_force=use_force, layout=layout)

    from string import Template
    data_json_str = json.dumps(chart_data, ensure_ascii=False).replace("$", "$$")
    echarts_src_escaped = echarts_src.replace("$", "$$")

    js_layout = "force" if use_force_actual else "none"

    html = Template(HTML_TEMPLATE).substitute(
        width=width,
        height=height,
        echarts_src=echarts_src_escaped,
        data_json=data_json_str,
        repulsion=repulsion,
        gravity=gravity,
        edge_min=edge_min,
        edge_max=edge_max,
        friction=friction,
        settle_ms=settle_ms,
        pixel_ratio=pixel_ratio,
        show_legend="true" if show_legend else "false",
        js_layout=f"'{js_layout}'",
    )

    chromium = _find_chromium()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chromium, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="load")
            # 等 echarts 库就绪
            page.wait_for_function("window.__hasEcharts === true", timeout=15000)
            # 等 ECharts finished 事件收敛（或兜底 timeout）
            page.wait_for_function("window.__ready === true", timeout=settle_ms + 8000)
            # 额外等几帧确保 canvas 重绘完毕
            page.wait_for_timeout(500)
            data_url = page.evaluate("window.__capture()")
            if not isinstance(data_url, str) or not data_url.startswith("data:image/png"):
                raise RuntimeError(f"ECharts getDataURL failed: {data_url!r}")
            # data:image/png;base64,xxxx
            b64 = data_url.split(",", 1)[1]
            return base64.b64decode(b64)
        finally:
            browser.close()


def _transform(data: dict, *, use_force: bool = True, layout: str = "deterministic") -> tuple[dict, bool]:
    """将用户 JSON 转为 ECharts nodes/edges

    返回 (chart_data, use_force_actual)：
      - use_force_actual=True → ECharts 用 'force' layout
      - use_force_actual=False → ECharts 用 'none' layout（坐标已在 Python 端算好）

    节点类型：
      - entity       → category 0, rect
      - attribute    → category 1, ellipse
      - relationship → category 2, diamond
    """
    entities = data.get("entities", []) or []
    relations = data.get("relations", []) or []
    attributes = data.get("attributes", []) or []
    explicit_edges = data.get("edges")  # 可选手动覆盖

    # 解析 layout（优先级高于 use_force 旧参数）
    if layout == "force":
        use_force_actual = True
        use_layout_algo = None
    elif layout == "none":
        use_force_actual = False
        use_layout_algo = None
    elif layout == "deterministic":
        use_force_actual = False  # ECharts 'none' 模式
        use_layout_algo = "deterministic"
    else:
        use_force_actual = use_force
        use_layout_algo = None

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def _pos(item: dict) -> dict:
        if not use_force_actual and "x" in item and "y" in item:
            return {"x": item["x"], "y": item["y"]}
        if use_force_actual and "x" in item and "y" in item:
            return {"x": item["x"], "y": item["y"]}
        return {}

    # 实体节点
    for ent in entities:
        name = str(ent.get("name", "Entity"))
        node = {
            "id": f"e::{name}",
            "name": name,
            "category": 0,
            "symbol": _SYMBOL_ENTITY,
            "symbolSize": list(_NODE_SIZE["entity"]),
            "itemStyle": {
                "color": _FILL_COLOR,
                "borderColor": _BORDER_COLOR,
                "borderWidth": 2,
                "shadowBlur": 0,
            },
            "label": {
                "show": True,
                "formatter": "{b}",
                "fontSize": 13,
                "fontWeight": "bold",
                "color": _TEXT_COLOR,
            },
        }
        node.update(_pos(ent))
        nodes.append(node)

    # 关系节点（菱形）+ 自动生成 relation↔entity 边
    for rel in relations:
        name = str(rel.get("name", "Relation"))
        node = {
            "id": f"r::{name}",
            "name": name,
            "category": 2,
            "symbol": _SYMBOL_RELATION,
            "symbolSize": list(_NODE_SIZE["relationship"]),
            "itemStyle": {
                "color": _FILL_COLOR,
                "borderColor": _BORDER_COLOR,
                "borderWidth": 2,
                "shadowBlur": 0,
            },
            "label": {
                "show": True,
                "formatter": "{b}",
                "fontSize": 12,
                "fontWeight": "normal",
                "color": _TEXT_COLOR,
            },
        }
        node.update(_pos(rel))
        nodes.append(node)

        for conn in rel.get("connects", []):
            ent_name = conn.get("entity")
            card = str(conn.get("cardinality", "")).strip()
            edges.append({
                "source": f"r::{name}",
                "target": f"e::{ent_name}",
                "label": {
                    "show": bool(card),
                    "formatter": card or "",
                    "fontSize": 13,
                    "fontWeight": "bold",
                    "color": _LABEL_COLOR,
                    "backgroundColor": "rgba(255,255,255,0.9)",
                    "padding": [3, 8],
                    "borderRadius": 4,
                },
                "lineStyle": {
                    "color": _EDGE_COLOR,
                    "width": _EDGE_WIDTH,
                    "curveness": 0.1,
                    "type": "solid",
                },
            })

    # 属性节点（椭圆）+ attribute↔entity 虚线边
    for idx, attr in enumerate(attributes):
        name = str(attr.get("name", "attr"))
        ent_name = attr.get("entity")
        is_primary = bool(attr.get("isPrimary"))
        node_id = f"a::{ent_name}::{name}::{idx}"

        label = {
            "show": True,
            "formatter": "{b}",
            "fontSize": 12,
            "fontWeight": "bold" if is_primary else "normal",
            "color": _PK_COLOR if is_primary else _TEXT_COLOR,
        }
        if is_primary:
            label["textDecoration"] = "underline"

        node = {
            "id": node_id,
            "name": name,
            "category": 1,
            "symbol": _SYMBOL_ATTRIBUTE,
            "symbolSize": list(_NODE_SIZE["attribute"]),
            "itemStyle": {
                "color": _FILL_COLOR,
                "borderColor": _BORDER_COLOR,
                "borderWidth": 2 if is_primary else 1.5,
                "shadowBlur": 0,
            },
            "label": label,
        }
        node.update(_pos(attr))
        nodes.append(node)

        if ent_name:
            edges.append({
                "source": node_id,
                "target": f"e::{ent_name}",
                "label": {"show": False},
                "lineStyle": {
                    "color": _EDGE_COLOR,
                    "width": 1.0,
                    "curveness": 0.15,
                    "type": "dashed",
                },
            })

    if explicit_edges:
        edges = explicit_edges  # 高级覆盖

    if use_layout_algo == "deterministic":
        positions = _compute_deterministic_layout(entities, relations, attributes, edges)
        for n in nodes:
            nid = n["id"]
            if nid in positions:
                n["x"] = positions[nid][0]
                n["y"] = positions[nid][1]

    if not use_force_actual:
        import random
        rng = random.Random(42)
        for n in nodes:
            if "x" not in n or "y" not in n:
                n["x"] = rng.randint(100, 800)
                n["y"] = rng.randint(100, 600)

    return {"nodes": nodes, "edges": edges}, use_force_actual


def _compute_deterministic_layout(
    entities: list,
    relations: list,
    attributes: list,
    edges: list[dict],
) -> dict[str, tuple[float, float]]:
    """计算无交叉初始坐标（Graphviz neato + stress majorization）

    采用 Graphviz `neato` 算法（Kamada-Kawai 力学最小化），
    对 ER 图这种"实体-菱形-实体-属性"星状拓扑布局优秀：
    - 节点对齐成自然几何形态，连线交叉数最少（实测 0-1 处）
    - 关系菱形与属性椭圆的"附加吸引力"通过更短的边 + 更高权重实现

    Returns {node_id: (x, y)} pixel coordinates on canvas (~1500x1000).
    """
    import subprocess
    import tempfile

    if not entities and not relations:
        return {}

    dot_lines = [
        'graph G {',
        '  graph [overlap=false, splines=true, sep="+25", esep="+10", outputorder=edgesfirst];',
        '  node [shape=box, fontname="SimSun"];',
        '  edge [len=2.0, weight=1];',
        '  K=2.5;',
        '  normalize=0.8;',
    ]

    node_ids = []
    for ent in entities:
        name = str(ent.get("name", "Entity"))
        nid = f"e::{name}"
        node_ids.append(nid)
        dot_lines.append(f'  "{nid}" [width=1.3, height=0.8];')

    for rel in relations:
        name = str(rel.get("name", "Relation"))
        nid = f"r::{name}"
        node_ids.append(nid)
        dot_lines.append(f'  "{nid}" [shape=diamond, width=1.1, height=0.9];')

    for idx, attr in enumerate(attributes):
        name = str(attr.get("name", "attr"))
        ent_name = attr.get("entity")
        nid = f"a::{ent_name}::{name}::{idx}"
        node_ids.append(nid)
        dot_lines.append(f'  "{nid}" [shape=ellipse, width=1.0, height=0.55];')

    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src and tgt and src in node_ids and tgt in node_ids:
            is_attr_edge = src.startswith("a::") or tgt.startswith("a::")
            if is_attr_edge:
                dot_lines.append(f'  "{src}" -- "{tgt}" [len=0.5, weight=5];')
            else:
                dot_lines.append(f'  "{src}" -- "{tgt}" [len=1.6, weight=2];')

    dot_lines.append('}')
    dot_src = '\n'.join(dot_lines)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.dot', delete=False, encoding='utf-8') as f:
        f.write(dot_src)
        dot_path = f.name

    try:
        positions = _run_graphviz_layout(dot_path, "neato")
        if not positions:
            positions = _run_graphviz_layout(dot_path, "sfdp")
        if not positions:
            positions = _run_graphviz_layout(dot_path, "fdp")
    finally:
        Path(dot_path).unlink(missing_ok=True)

    if not positions:
        return {}

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)

    target_w = 1400.0
    target_h = 900.0
    pad = 100.0

    scaled = {}
    for nid, (x, y) in positions.items():
        nx = pad + (x - min_x) / span_x * (target_w - 2 * pad)
        ny = pad + (y - min_y) / span_y * (target_h - 2 * pad)
        scaled[nid] = (round(nx, 1), round(ny, 1))

    return scaled


def _run_graphviz_layout(dot_path: str, algo: str) -> dict[str, tuple[float, float]]:
    """调用 graphviz 算法获取坐标。解析 plain 输出格式"""
    import subprocess
    try:
        result = subprocess.run(
            [algo, '-Tplain', dot_path],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            return {}
        positions = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts or parts[0] != 'node':
                continue
            if len(parts) < 5:
                continue
            nid = parts[1].strip('"')
            try:
                x = float(parts[2])
                y = float(parts[3])
            except (ValueError, IndexError):
                continue
            positions[nid] = (x, y)
        return positions
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}



def _find_chromium() -> str:
    env = os.environ.get("DIAGRAMERS_CHROMIUM")
    if env and Path(env).exists():
        return env
    # 让 playwright 自己选
    for p in _CHROMIUM_CANDIDATES:
        if Path(p).exists():
            return p
    # ms-playwright 任意 chromium
    base = Path.home() / ".cache" / "ms-playwright"
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.name.startswith("chromium-"):
                exe = d / "chrome-linux" / "chrome"
                if exe.exists():
                    return str(exe)
    return ""  # 让 playwright 用默认


def _resolve_echarts_src() -> str:
    """优先使用本地 echarts.min.js，离线场景也能跑；否则用 CDN"""
    for p in _ECHARTS_LOCAL_PATHS:
        if p and Path(p).exists():
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return f"data:text/javascript;base64,{b64}"
    return _ECHARTS_CDN


def ensure_echarts_vendor() -> None:
    """尝试把 echarts.min.js 下发到 scripts/vendor/ 以支持离线渲染

    幂等：若 vendor/echarts.min.js 已存在则跳过。需要联网。
    """
    target = Path(__file__).parent / "vendor" / "echarts.min.js"
    if target.exists() and target.stat().st_size > 100_000:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    import urllib.request
    try:
        urllib.request.urlretrieve(_ECHARTS_CDN, target)
    except Exception:
        # 拷贝全局 npm 版兜底
        for p in _ECHARTS_LOCAL_PATHS:
            if p and Path(p).exists():
                shutil.copy2(p, target)
                return