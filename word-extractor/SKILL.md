---
name: word-extractor
description: >
  高精度提取 .docx Word 文档全部内容，保留结构、图片、表格与题注（基于 Word XML 深度解析，图片/表格与段落位置精确对应）。当用户需要读取、提取、分析或转换 Word 文档（论文、开题报告、含图表文档），或提到 提取word/读取docx/分析论文/word内容提取 时使用。
---

# Word Document Extractor

Extract all content from `.docx` files with high fidelity. Outputs both
**Markdown** (human-readable) and **JSON** (structured data) for downstream
processing, editing, or analysis.

## Why this skill exists

Basic tools like `python-docx` often miss or misplace critical content:
- Images are counted but not mapped to their exact paragraph positions
- Figure/table captions are disconnected from their visual elements
- Document structure (heading hierarchy) is hard to reconstruct
- Tables lose structural context

This skill solves these problems by deeply inspecting the Word XML structure.

## Usage

### 1. Run the extraction script

```bash
cd ~/projects/dc-skills
uv run python word-extractor/scripts/extract_docx.py <input.docx> -o <output-dir> -f both
```

Options:
- `-o, --output-dir`: Where to save outputs (default: `<cwd>/doc-output/word-extractor/` or `~/.claude/skills-output/word-extractor/`)
- `-f, --format`: `json`, `markdown`, or `both` (default: `both`)

### 2. Read the outputs

After running the script, read the generated files:
- `<name>.md` — Human-readable Markdown with preserved heading hierarchy
- `<name>.json` — Structured data for programmatic analysis

### 3. Use the structured data

The JSON output contains:

| Field | Description |
|-------|-------------|
| `statistics` | Paragraph count, table count, image count, heading count |
| `sections` | Detected special sections: title, abstract, keywords, references, acknowledgements |
| `heading_tree` | Hierarchical tree of all headings with levels and paragraph indices |
| `images` | Each image with paragraph position, filename, caption, and figure/table number |
| `tables` | Each table with position, dimensions, cell contents, and caption |
| `paragraphs` | Every paragraph with text, style, heading level, format info, and image/table flags |

## Key capabilities

### Image extraction
- Maps each image to its exact paragraph index via XML traversal
- Associates captions using heuristics (looks for `图X-Y` / `表X-Y` patterns near the image)
- Identifies whether the caption belongs to a figure or table

### Table extraction
- Preserves full cell structure (including merged cells)
- Associates table captions (`表X-Y ...`) from nearby paragraphs
- Records paragraph position for contextual placement

### Structure detection
- Recognizes heading levels from Word styles (Heading 1-5, 标题 1-5, 一级标题, etc.)
- Builds a heading tree showing chapter hierarchy
- Auto-detects special sections: abstract, keywords, references, acknowledgements

### Format preservation
- Records paragraph formatting: alignment, indentation, spacing
- Records run-level formatting: font, size, bold, italic, color

## Output Markdown format

Headings are converted to Markdown (`#`, `##`, etc.). Images are shown as:

```markdown
**图 1-1 系统架构图**

[图片: image1.png]
```

Tables are converted to Markdown tables with their captions.

## When to use JSON vs Markdown

- **Markdown**: For reading, summarizing, or presenting the document content to the user
- **JSON**: For precise analysis, verification, or automated processing (e.g., checking if all figures are cited, counting sections, verifying heading continuity)

## Handling edge cases

### No captions found
If the script cannot find a caption for an image or table, the `caption` field
will be `null`. In Markdown, the image will still appear as `[图片: filename]`.

### Complex nested tables
The script extracts cell text in row-major order. Deeply nested table structures
may need manual review.

### Non-standard heading styles
If the document uses custom style names not matching common patterns, heading
level detection may fail (heading_level will be 0). You can still use the
`style` field to identify headings.

## Dependencies

- `python-docx` (Python library for reading .docx files)

Install if missing:
```bash
cd ~/projects/dc-skills
uv sync
```

## Example workflow

```bash
# Extract a thesis document
cd ~/projects/dc-skills
uv run python word-extractor/scripts/extract_docx.py ~/thesis.docx -o ./output -f both

# Read the Markdown for overview
cat ./output/thesis.md

# Read the JSON for detailed analysis
uv run python -c "import json; d=json.load(open('./output/thesis.json')); print(d['statistics'])"
```
