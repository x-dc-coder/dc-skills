---
name: unified-search
description: >
  统一网页+学术搜索聚合器（10 源：keenable/tavily/firecrawl/bocha/zhipu/arxiv/dblp/semantic_scholar/openalex/ai4scholar），三模式：general（多源+分歧仲裁）、academic（六源并行+论文链接记录）、fetch（单页正文提取），配额感知。内置自愈：NO_PROXY 方括号 IPv6 清洗、arxiv 429 降级 HTML、dblp Anubis PoW 过墙、401/402 熔断、失败源透出。任何需要实时信息、学术论文、网页内容搜索（搜一下/查一下/find papers/search the web）都优先使用本技能，取代内置 web 搜索工具；纯代码/本地问题勿用。
---

# Unified Search

A single skill that replaces ALL default web search tools. 10 sources, 3 modes,
quota-aware, with retry and dedup. **Always use this skill first** for any
search need; only fall back to built-in tools if this skill fails entirely.

## Sources

| Source | Type | Cost | Strength |
|---|---|---|---|
| keenable | CLI | free | General web search, fast fetch |
| tavily | HTTP, 1000/mo | metered | AI answer summary, high relevance |
| firecrawl | HTTP, 1000/mo | metered | Markdown body extraction, scraping |
| bocha | HTTP | pay-per-call | 中文搜索最强，预充值按量 |
| zhipu | HTTP | GLM 套餐按次计积分 | 中文资讯聚合摘要（无链接，见下） |
| arxiv | HTTP | free | Preprint papers (physics/CS/math) |
| dblp | HTTP | free | CS publication catalog |
| semantic_scholar | HTTP | free | Citation graph, abstracts, PDF links（需 S2_API_KEY 才稳） |
| openalex | HTTP | free | 2.5 亿+ 论文目录，无需 key、无人机墙，学术兜底主力 |
| ai4scholar | HTTP | credits（按次扣积分） | S2 语料 2 亿+；另有 MCP 通道覆盖 arXiv/PubMed/bioRxiv/medRxiv/Google Scholar |

注：arxiv / dblp / semantic_scholar / openalex 全部免费；付费源为 tavily、firecrawl（各 1000/月）、
bocha（按次）与 zhipu（GLM Coding Plan 套餐按次计积分）。源健康与自愈机制见下文「源健康与自愈」。

## 环境依赖（本技能并入原 keenable-cli 技能）

- **keenable 源为硬依赖**：`unified_search.py` 直接调用 `keenable` CLI 二进制（config.json
  `sources.keenable.command`），未安装时该源不可用（其余源不受影响）。
- **安装 / 认证 / 更新 / 为 AI 客户端配置 keenable MCP**：见 `references/keenable-setup.md`
  （原 keenable-cli 技能全文迁移，含安装脚本、设备码登录、configure-mcp、CLI 参考）。
- 用户提出"配置 keenable MCP / 安装 keenable / 登录 keenable"等工具维护诉求时，同样走本技能
  （读 keenable-setup.md 执行）。

## 分层（--tier，general 模式）

- `--tier value`（默认）：性价比源 = keenable（免费）+ bocha（按量中文）+ zhipu（套餐积分）
- `--tier flagship`：旗舰源 = tavily advanced（AI 摘要最强）+ bocha + zhipu
- 日常检索用 value；需要权威/深度/外文时用 flagship

## When to Use (MANDATORY)

**Use this skill INSTEAD OF** `websearch_web_search_exa`, `webfetch`, or
`keenable_search_web_pages` whenever:

- User asks for current/real-time info, news, prices, weather
- User asks to find papers, research, academic literature
- User says "搜一下", "查一下", "search", "look up", "find"
- User needs web page content extracted (use `--fetch`)
- You need to verify info across multiple sources

**Do NOT use this skill** for:
- Pure code/logic questions answerable from the codebase
- Mathematical facts or historical events in training data
- Single-file local lookups (use Read/Grep)

## Quick Start

```bash
# Auto-classify intent (general vs academic) and search
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py "your query"

# Force general mode (keenable + tavily, firecrawl arbitration if divergent)
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py "query" --mode general

# Force academic mode (arxiv + dblp + semantic_scholar, paper links recorded)
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py "transformer attention" --mode academic

# Export paper links as _download_manifest.json for paper-reader
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py "VRP GPU" --mode academic --export-manifest papers/

# Fetch single URL content (markdown) — firecrawl primary, keenable fallback
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py --fetch https://example.com/article

# Search history cache (reuse past accurate results)
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py --history "past query"

# Check monthly quota usage for tavily/firecrawl
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py --quota
```

## Modes

### `general` (default for non-academic queries)

1. **Parallel**: value tier = keenable + bocha + zhipu；flagship tier = tavily + bocha + zhipu
2. **Agreement check**: compute URL-host Jaccard overlap（无链接源如 zhipu 不参与按 URL 合并）
   - If overlap ≥ 0.4 → merge, dedup, rank, done
   - If overlap < 0.4 → **arbitration**: invoke firecrawl as 3rd source
3. **Merge**: dedup by normalized URL, cross-validated results get score bonus

Rationale: free/metered sources first; expensive firecrawl only when
sources disagree, conserving the 1000/month quota.

### `academic` (auto-triggered for paper/research queries)

1. **Parallel**: arxiv + dblp + semantic_scholar + openalex + ai4scholar + zhipu（前四个免费；ai4scholar 按积分、zhipu 按套餐积分计费）
   - arxiv：export API 被限流（429）时自动降级抓 `https://arxiv.org/search/`（结果带 `via: html_fallback`）
   - dblp：Anubis 人机墙自动过墙（PoW，结果带 `via: anubis_pow`，cookie 缓存 1h）
   - openalex：免费兜底，不受上述限流影响
   - zhipu：中文资讯/技术报道补充（无链接结果，仅供排序展示）
2. **Merge**: dedup by DOI/arxiv-id/normalized-URL, cross-validated bonus
3. **Record**: all paper links saved to `data/history.db` for later retrieval
4. Each result includes: title, url, pdf_url, doi, arxiv_id, year, venue, authors

Academic hint keywords that trigger auto-classification: paper, 论文, research,
study, survey, algorithm, model, neural, deep learning, transformer, LM,
benchmark, dataset, arxiv, doi, citation, method, approach, novel, proposed.

### `fetch` (single-URL content extraction)

| Strategy | Primary | Fallback | Use when |
|---|---|---|---|
| `markdown_body` (default) | firecrawl | keenable | Need full article body as markdown |
| `fast_summary` | keenable | — | Quick snippet, free |
| `ai_extract` | tavily | firecrawl | AI-structured extraction |

### `history`

Searches past results in `data/history.db`. General mode stores top-5;
academic mode stores top-5 + all paper links. Use this before re-searching
a similar query to save quota.

## Quota Conservation (CRITICAL)

Tavily and Firecrawl each have **1000 calls/month**. The script:

- Uses **basic** search depth by default (cheaper)
- Only invokes firecrawl for arbitration when sources diverge
- Only uses tavily `advanced` depth on explicit academic arbitration
- Falls back to free keenable when quota exhausted
- Tracks usage in `data/quota.json` per month

**Before searching, check quota**:
```bash
cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py --quota
```

If a metered source is exhausted, the script automatically degrades to free
sources (keenable for general, arxiv+dblp+semantic_scholar for academic).

## Output Format

All output is JSON to stdout. Example (general mode):

```json
{
  "mode": "general",
  "query": "rust async patterns",
  "sources_used": ["keenable", "tavily"],
  "agreement_score": 0.62,
  "arbitration_triggered": false,
  "total_results": 12,
  "results": [
    {
      "title": "...",
      "url": "https://...",
      "snippet": "...",
      "source": "keenable",
      "score": 0.78,
      "also_from": ["keenable", "tavily"]
    }
  ]
}
```

For academic mode, additional `paper_links` array with DOI/PDF URLs.

## Paper-Reader Bridge (`--export-manifest`)

Exports academic search results as `_download_manifest.json`, ready for consumption by
the `paper-reader` skill's `--init --from-manifest` command.

```bash
# Search + export manifest (the manifest maps arxiv IDs → expected filenames)
uv run python unified-search/scripts/unified_search.py "MDVRP" --mode academic \
  --export-manifest papers/

# Output: papers/_download_manifest.json
# Then paper-reader can consume it:
#   paper_reader.py papers/ --init --from-manifest papers/_download_manifest.json
```

**Filename inference** (in priority order):
1. `arxiv_id` → `{id}.pdf` (e.g. `2205.02453.pdf`)
2. PDF URL basename (e.g. `abc123-Paper.pdf`)
3. URL path stem → `{stem}.pdf`
4. Fallback: `paper_001.pdf`, `paper_002.pdf`, ...

**Warning**: duplicate filenames are flagged in stderr so you can rename before downloading.

## Integration with Sub-agents

When delegating to `librarian` or `explore` agents, pass
`load_skills=["unified-search"]` so they use this skill instead of
built-in web search.

## Files

```
unified-search/
├── SKILL.md                  # this file
├── config.json               # sources, keys, modes, quota
├── scripts/
│   └── unified_search.py     # main script
├── data/
│   ├── history.db            # SQLite: past searches + paper links
│   └── quota.json            # monthly usage tracking
└── cache/                    # reserved for future result caching
```

## Zhipu 源（GLM 套餐 web_search，2026-09-21 接入验收）

| 项 | 值 |
|---|---|
| 端点 | `POST https://open.bigmodel.cn/api/paas/v4/web_search` |
| 认证 | `Authorization: Bearer <ZHIPU_API_KEY>`（GLM Coding Plan 套餐 Key） |
| 请求体 | `{"search_engine":"search_std","search_query":"...","count":8}`（ engines: search_std/search_pro/search_sim 等） |
| 响应 | `search_result[]`：`title` / `link` / `content` / `media` / `publish_date` / `refer` / `icon` |
| 计费 | 套餐按次计积分（联网搜索每次 Output 系数 1.2）；config.json 设软上限 monthly_limit=2000 仅本地计数 |
| 密钥 | `~/.config/vision-ai/.env` 的 `ZHIPU_API_KEY`（脚本自动加载）；`~/.config/dsh/secrets.env` 同步一份 |

**关键限制（实测）**：`search_std` 与 `search_pro` 的 `link` 字段都返回空字符串——本源只给
标题 + 正文摘要 + 日期，没有 URL。定位为「中文摘要引擎」：结果带 `no_link: true`，按
`标题+源` 独立成键，不参与跨源 URL 合并（不会被误判为与其它源重复）。需要链接时用
keenable/bocha/tavily 的结果，或拿 zhipu 的标题再 `--fetch`。

**验收（2026-09-21）**：`--mode general` 三源并行（keenable+bocha+zhipu）返回正常，
zhipu 贡献 GLM-5.3 中文报道 6 条且 `sources_failed` 为空；51 个既有单测全过。

**与视觉 MCP 的关系**：智谱官方视觉 MCP（`@z_ai/mcp-server`）的 8 个工具已并进本地
`~/projects/Vision-MCP`（见 `vision-workflow` 技能），与本技能的搜索源互不重叠——
搜索走 REST `web_search`，视觉走 `chat/completions`，两者共用同一把 `ZHIPU_API_KEY`。

## Ai4Scholar 开放 API（新增学术源，2026-09-12 接入验收）

| 项 | 值 |
|---|---|
| REST 基址 | @@BT@@https://ai4scholar.net/graph/v1@@BT@@（Semantic Scholar 形态） |
| 认证 | @@BT@@Authorization: Bearer <AI4SCHOLAR_API_KEY>@@BT@@ —— **不支持 @@BT@@x-api-key@@BT@@**（那是 S2 官方用法） |
| 端点 | @@BT@@paper/search@@BT@@、@@BT@@paper/{id}@@BT@@、@@BT@@paper/{id}/citations@@BT@@、@@BT@@paper/{id}/references@@BT@@、@@BT@@author/search@@BT@@、@@BT@@author/{id}@@BT@@、@@BT@@author/{id}/papers@@BT@@、@@BT@@paper/batch@@BT@@、@@BT@@paper/search/bulk@@BT@@、@@BT@@GET /api/credits@@BT@@（免费） |
| 计费 | 每次成功调用扣积分（1 分起，batch/bulk 固定 2 分）；失败退还；响应头 @@BT@@x-credits-charged@@BT@@ / @@BT@@x-credits-remaining@@BT@@ 可对账 |
| 限流 | 免费 10 次/分；专业版 100 次/分；团队版不限 |
| 密钥 | @@BT@@~/.config/vision-ai/.env@@BT@@ 的 @@BT@@AI4SCHOLAR_API_KEY@@BT@@（不进 config.json） |
| 验收 | 实测 search / detail / citations / references / author search 全部 200；一次 academic 检索贡献 15 条结果、扣 1 积分 |

### 它能覆盖多少来源

| 通道 | 覆盖范围 |
|---|---|
| **REST 开放 API**（本技能接入的就是这条） | **仅 Semantic Scholar**：2 亿+ 篇（计算机/生物医学/物理/数学，每周更新）——实测返回 @@BT@@paperId@@BT@@/@@BT@@externalIds@@BT@@/@@BT@@citationCount@@BT@@，是标准 S2 结构 |
| **MCP 通道**（托管 SSE 或本地 stdio，29 个工具） | arXiv、PubMed（3500 万+）、Semantic Scholar（2 亿+）、Google Scholar（3.89 亿+）、bioRxiv、medRxiv，外加按 DOI 下载 |
| **平台网页端**（无 API） | Google Patents（1.2 亿+，100+ 国家/地区）、中文文献（中文核心/CSSCI/CSCD/北大核心、学位论文、会议论文） |

结论：这把 key 的 REST 通道 = S2 2 亿+ 语料；要 arXiv/PubMed/Google Scholar 等更多来源得走 MCP；
Google Patents 与中文文献目前只在他家网页端，没有 API。

### PDF 下载（本地 stdio MCP，已实测）

托管版 MCP 的 @@BT@@download_*@@BT@@ 会直接返回「该工具仅在本地模式（stdio）下可用」，所以下载必须本机装一次：

@@BT@@@@BT@@@@BT@@bash
uv venv --python 3.12 ~/.local/share/ai4scholar-mcp/venv
uv pip install --python ~/.local/share/ai4scholar-mcp/venv/bin/python ai4scholar-mcp "mcp<2"
@@BT@@@@BT@@@@BT@@

> 必须 @@BT@@mcp<2@@BT@@：ai4scholar-mcp 0.4.0 仍用旧版 @@BT@@mcp.server.fastmcp@@BT@@ API，
> 装了 mcp 2.x 会直接 @@BT@@ModuleNotFoundError: No module named 'mcp.server.fastmcp'@@BT@@。

用法（脚本会自动找上面的 venv）：

@@BT@@@@BT@@@@BT@@bash
cd ~/projects/dc-skills
uv run python unified-search/scripts/ai4scholar_download.py --doi 10.48550/arXiv.1706.03762
uv run python unified-search/scripts/ai4scholar_download.py --arxiv 2312.10997 --out ./papers
uv run python unified-search/scripts/ai4scholar_download.py --semantic <paperId>
uv run python unified-search/scripts/ai4scholar_download.py --list-tools
@@BT@@@@BT@@@@BT@@

实测（2026-09-12）：@@BT@@--arxiv 1706.03762@@BT@@ → 2,215,244 B；@@BT@@--arxiv 2312.10997@@BT@@ → 1,662,567 B；
@@BT@@--semantic@@BT@@ → 885,323 B；三份文件 @@BT@@%PDF-@@BT@@ 魔数全部正确。下载链路为
Unpaywall → 出版商页面 → 可选 Sci-Hub；在校园网出口可借机构权限拿付费论文。

## 源健康与自愈（2026-09-12 体检 + 修复）

| 现象 | 根因 | 现在的处理 |
|---|---|---|
| 所有 HTTP 源报 @@BT@@InvalidURL: Invalid port: ':1]'@@BT@@ | DSH 的 @@BT@@@deepseek-ai/dsh-http-proxy@@BT@@ 会把 loopback 绕过项 @@BT@@[::1]@@BT@@（连同 @@BT@@NODE_USE_ENV_PROXY=1@@BT@@）注入**所有**子进程；httpx 把 @@BT@@[::1]@@BT@@ 当 host:port 解析 | 脚本导入时自动清洗 @@BT@@no_proxy/NO_PROXY@@BT@@：只删方括号条目，保留裸 @@BT@@::1@@BT@@，语义不变 |
| arxiv 报 @@BT@@429 Rate exceeded@@BT@@ | export API 对当前出口 IP 限流（同一时刻 arxiv.org HTML 页仍 200） | 自动降级抓 HTML 搜索页，超时 60s + 重试 1 次 |
| dblp 返回 Anubis 挑战页（HTTP 200 + HTML） | dblp.org 位于 Anubis v1.27.0 之后（difficulty=4 的 SHA-256 PoW） | @@BT@@scripts/anubis_dblp.py@@BT@@ 纯 Python 解 PoW 换取 @@BT@@dblp_org-auth-*@@BT@@ cookie（JWT 1h），缓存 @@BT@@data/dblp_cookies.json@@BT@@；代理/直连双通道自动回退 |
| firecrawl @@BT@@402 Payment Required@@BT@@ | 密钥有效但额度耗尽（本周期 2026-08-14 → **2026-09-14 重置**） | 命中 401/402/403 即在本次进程内熔断，不再重复白打 API；@@BT@@--fetch markdown_body@@BT@@ 自动落到 keenable |
| semantic_scholar @@BT@@429 Too Many Requests@@BT@@ | 未配置 @@BT@@S2_API_KEY@@BT@@，走匿名共享限流（实测 4 次里 1 次成功） | **需人工申请免费 key**，见下 |

### semantic_scholar API key 申请（免费）

1. 打开官方页 https://www.semanticscholar.org/product/api#api-key-form ，点 **Request an API key** 填表（需登录 Semantic Scholar 账号）
2. 密钥通过**邮件**发送。官方限流口径：未认证=全站共享且会被额外节流；带 key 起步 1 RPS。**不要外传密钥**
3. 写入统一密钥文件 @@BT@@~/.config/vision-ai/.env@@BT@@（本脚本自动加载）：
   @@BT@@S2_API_KEY=<你的key>@@BT@@
4. 验证：@@BT@@cd ~/projects/dc-skills && uv run python unified-search/scripts/unified_search.py "retrieval augmented generation" --mode academic@@BT@@ ，
   输出 JSON 的 @@BT@@sources_failed@@BT@@ 中不再出现 @@BT@@semantic_scholar@@BT@@ 即为生效

### 输出新增字段

- @@BT@@sources_failed@@BT@@：完全失败的源 + 错误原因（区分「没搜到」与「源挂了」）
- @@BT@@degraded@@BT@@：@@BT@@true@@BT@@ 表示本轮有源失败

### 排查命令

@@BT@@@@BT@@@@BT@@bash
cd ~/projects/dc-skills

# 逐源体检（一条命令看各源真实状态与降级通道）
uv run python ~/.claude/skills-output/unified-search/probe_sources2.py nobracket

# 配额 / 真实额度（tavily、firecrawl 走官方 usage 接口）
uv run python unified-search/scripts/unified_search.py --quota

# dblp 过墙单独自测
uv run python unified-search/scripts/anubis_dblp.py "transformer" -n 5
@@BT@@@@BT@@@@BT@@

## Troubleshooting

| Symptom | Fix |
|---|---|
| `keenable: command not found` | Run `source ~/.cargo/env` first |
| `tavily: quota_exhausted` | Wait for month rollover or use `--mode general` (keenable only path) |
| `firecrawl: 401` | Check `FIRECRAWL_API_KEY` env var or value in config.json |
| arxiv returns empty | arxiv rate-limits (1 req/3s); script retries with backoff |
| zhipu: no API key / 401 | 确认 `ZHIPU_API_KEY` 已写入 `~/.config/vision-ai/.env`；套餐 Key 与平台普通 Key 不通用，需与 `Z_AI_MODE=ZHIPU` 匹配 |
| All sources fail | Script returns error entries; check network and API keys |
