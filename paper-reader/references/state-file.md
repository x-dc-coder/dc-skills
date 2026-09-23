# 流水线状态文件参考（`_pipeline_state.json`）

> 从 SKILL.md 拆出（2026-09-24，B7a 行数门禁）。完整 JSON 示例与字段语义；调试状态文件、排查断点续跑问题时读。

## 流水线状态文件

`_pipeline_state.json` 跟踪每篇论文的处理进度：

```json
{
  "pipeline_version": "2.0",
  "last_updated": "2026-07-18T15:30:00",
  "papers": {
    "2605.05208": {
      "source": {
        "url": "https://arxiv.org/abs/2605.05208",
        "pdf_url": "https://arxiv.org/pdf/2605.05208",
        "doi": "10.1109/xxx",
        "arxiv_id": "2605.05208",
        "source_db": "arxiv",
        "title_from_source": "MDVRP: A Multi-Depot...",
        "venue": "Transportation Science",
        "year": 2026,
        "authors": ["Lei, H.", "Smith, J."]
      },
      "pdf_hash": "sha256:abc123...",
      "precheck": {"status": "passed", "page_count": 40},
      "phase1_converted": {
        "status": "done",
        "marker_ok": true,
        "mineru_ok": true,
        "at": "2026-07-15T10:23:00"
      },
      "phase2_merged": {
        "status": "done",
        "diff_paragraphs": 66,
        "images_copied": 22,
        "at": "2026-07-15T10:23:05"
      },
      "phase3_summarized": {
        "status": "done",
        "summary_path": "paper-summaries/2605.05208.md",
        "at": "2026-07-16T09:00:00"
      }
    }
  }
}
```
