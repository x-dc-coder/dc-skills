# 跨项目测试工程规范（Test Engineering & Anti-False-Pass Specification）v1.0

> 适用工程画像：
> - **Words-Production 型**：本地/Office/DOM/原生桥接流水线（markdown 规范检查 → docx 导出引擎 → 脚注重排 → doc_intake → COM 封面拼接，跨 WSL↔Windows 桥接）。
> - **Soul-Spark 型**：异步 Web/数据库/Bot/事件队列（FastAPI + SQLite/alembic + 飞书 Bot + WS realtime + scheduler + LLM gateway + SPA 前端）。
>
> 配套可执行工具（同目录）：
> - `check_test_hygiene.py` —— AST 静态规则检查器（七条铁律 R1–R7 的自动拦截）
> - `pytest_test_guard.py` —— pytest 插件（分层标记强制、命名门禁、层级超时预算、断言统计）

---

## 第一部分：测试分类分级金字塔（Taxonomy & Tiering）

### 1.0 总览

```
            ▲  数量少 / 慢 / 贵 / 只在专用环境跑
           ╱ ╲
          ╱L4╲   反证与变异层（横切 L0–L3，独立目录承载）
         ╱────╲
        ╱  L3  ╲  端到端与真机桥接层：真实 Word COM / Playwright 全 SPA
       ╱────────╲
      ╱    L2    ╲  子系统/服务层：单服务完整流转 + 受控契约 Mock（仅外部 SaaS）
     ╱────────────╲
    ╱      L1      ╲  契约与本地集成层：真实内存 SQLite / 真实 lxml / 真实 Schema，Hermetic
   ╱────────────────╲
  ╱        L0        ╲  纯粹单元层：纯内存计算，零 IO，零 Mock，毫秒级
 ╱────────────────────╲
            ▼  数量多 / 快 / 便宜 / 每次提交都跑
```

数量配比指导（按测试用例数）：**L0 ≈ 60–70%，L1 ≈ 20%，L2 ≈ 8%，L3 ≈ 3–5%，L4 为横切预算（核心模块变异杀死率 ≥ 60%）**。倒金字塔（E2E 比 Unit 多）视为架构债，需在 CI 报表中显式暴露。

### 1.1 L0 纯粹单元层（Unit）

| 维度 | 规定 |
|---|---|
| 被测对象 | 纯函数/纯类算法：脚注重排编号、markdown 规范规则判定、术语归一、意图解析纯逻辑、时间基换算、sync 对账算法、sparks 规则引擎 |
| IO | **禁止一切 IO**：无文件、无网络、无数据库、无子进程、无环境变量读取（配置以参数注入） |
| Mock | **零外部 Mock**。需要协作者时用内存假对象（fake/stub 纯数据结构），不用 `unittest.mock.patch` |
| 时间 | 时钟必须注入（`now: datetime` 参数或 fake clock 对象），禁止 `datetime.now()` 直读 |
| 速度预算 | 单测 ≤ 50ms，全层 ≤ 60s |
| 确定性 | 随机必须带 seed；同输入 → 逐字节同输出 |
| CI 门禁 | 每次 push 必跑，失败阻断合并 |

### 1.2 L1 契约与本地集成层（Contract / Integration）

| 维度 | 规定 |
|---|---|
| 被测对象 | 模块与真实本地基础设施的组合：ORM/alembic 迁移 × 真实 SQLite（内存或 tmp 文件）、lxml/OOXML 真实解析与生成、Pydantic/JSON Schema 真实校验、导出引擎 linux 路径产出真实 docx、doc_intake 对真实样本文件提取、scheduler 逻辑 × fake clock |
| Hermetic 三要素 | ① 数据库：`sqlite:///:memory:` 或 `tmp_path` 下新文件，**每测试新建，禁止共享**；② 文件系统：只用 `tmp_path`，样本从 `tests/fixtures/` 只读复制；③ 网络：**零网络**（socket 级熔断，见插件 `no_network` fixture） |
| Mock | 仅允许 mock 不可本地化的外部 SaaS 的**传输层**，且此时该测试应升格为 L2 |
| 断言 | 必须断言到**数据面**：DB 行内容、XML 节点树、docx 解压后的 document.xml、schema 校验错误明细 |
| 速度预算 | 单测 ≤ 5s，全层 ≤ 10min |
| CI 门禁 | 每次 push 必跑 |

### 1.3 L2 子系统/服务层（Subsystem / Service）

| 维度 | 规定 |
|---|---|
| 被测对象 | 单个服务进程内完整流转：FastAPI 路由栈（真实 DB + in-process TestClient）、飞书 Bot 消息处理管线、WS realtime 适配器、LLM gateway、导出编排服务（COM 以契约桩替代） |
| 契约 Mock 唯一准入 | **仅**针对无法本地运行的外部 SaaS（飞书开放平台、LLM provider、Windows COM——当宿主机非 Windows 时）。禁止为自己进程内的模块打契约 Mock |
| 契约 Mock 三强制 | ① **出向 Payload 契约验证**：mock 层必须对每次外发请求做 JSON Schema/Pydantic 校验（URL、method、body、headers 白名单），schema 不匹配 → 测试失败而非静默放行；② **入向响应来自录制样本**（cassette/fixture，标注录制日期与 API 版本），禁止手写“想象中的响应”；③ **契约漂移哨兵**：每 cassette 附一个 contract test，定期对真实沙箱环境重放校验（无法访问时 skip 并在周报中标记陈旧度） |
| 状态 | DB 仍走 L1 Hermetic 规则；队列/事件走内存 broker |
| 速度预算 | 单测 ≤ 30s |
| CI 门禁 | PR 必跑核心子集（`-m "l2 and core"`），全量夜跑 |

### 1.4 L3 端到端与真机桥接层（E2E / Bridge）

| 维度 | 规定 |
|---|---|
| 被测对象 | 真实 Word COM 渲染（Windows 宿主机，经 wsl-windows-bridge 通道）、Playwright 驱动完整 SPA（真实后端 + 真实 DB 文件 + 外部 SaaS 仍为契约桩）、CLI 全链路 |
| 环境标记 | `@pytest.mark.windows_com` / `@pytest.mark.playwright`，由具备该能力的 runner 认领；不满足环境的机器 **skip 且计入报表**，绝不静默通过 |
| 断言 | COM：产物 docx 的 OOXML 内容 + 页数/域渲染结果（转 PDF 后视觉/文本核验）；Playwright：显式等待终态锚点（见铁律 R3/R6），禁止裸 sleep |
| 重试与隔离 | 允许 `--reruns 1`，但**重跑通过仍记 flake**，连续 3 次 flake 移入 quarantine 目录并开单，quarantine 内测试不计入绿色门禁但计入债务看板 |
| 速度预算 | 单测 ≤ 300s |
| CI 门禁 | 夜跑 + 发布前必跑；PR 不跑（除非改动了桥接层本身） |

### 1.5 L4 反证与变异层（Falsification / Fault Injection）

| 维度 | 规定 |
|---|---|
| 目标 | 证明“测试真的能红”。三类手段：① **故障注入**：桥接通道超时/断连、飞书回调乱序/重复/丢失、SQLite 锁竞争、磁盘满、进程中途 kill；② **反证断言（negative chain）**：对每条正向链路必须存在配对的负向用例（非法输入、越权消息、损坏 docx 样本、错误 schema），且负向用例断言精确错误码；③ **变异测试**：mutmut/cosmic-ray 对核心模块（footnote 重排、sync 对账、schema 校验、意图解析）跑变异，**变异杀死率 < 60% 的模块视为测试无效**，CI 红 |
| 元测试 | 每个 L2/L3 关键链路至少一个“自毁验证”：以 monkeypatch 破坏被测行为后断言测试必然失败，防止“永绿测试” |
| Property-based | Hypothesis 用于往返不变量：markdown→docx→提取→再比对；事件序列化 round-trip；对账算法幂等性 |
| CI 门禁 | 变异测试周跑 + 核心模块变更时触发；故障注入随 L1/L2 层在 PR 跑 |

### 1.6 层级判定速查（放不进哪层就升层）

```
需要打开文件或真实 DB？          否 → L0
                                是 ↓
需要网络（哪怕 mock 的 SaaS）？  否 → L1
                                是 ↓
只在单个服务进程内？             是 → L2
                                否 ↓
需要真机（Windows COM/浏览器）？ 是 → L3
破坏它、注入故障、证明能红？      → L4（横切，按被注入对象的层级挂目录）
```

---

## 第二部分：领域驱动目录组织规范

### 2.0 总原则

1. **目录第一维 = 层级（tier），第二维 = 领域（domain）**。领域名与源码包名一一对应（Soul-Spark 的 `app/`、Words-Production 的导出流水线各阶段模块），禁止出现任务编号（m1/w1c/t7）、迭代名、issue 号作为目录或文件名主体。
2. issue 号只能以**后缀**形式出现在回归测试函数名中：`test_..._regression_issue47`。
3. `fixtures/` 按领域组织，golden 文件与输入样本同目录：`fixtures/<domain>/<case>/input.* + expected.*`。
4. 每个领域目录带 `__init__.py`（或在 conftest 声明），保证 pytest 模块名唯一。

### 2.1 Words-Production 标准 tests/ 目录树

```
tests/
├── conftest.py                        # 装载 pytest_test_guard 插件、公共 fixture
├── unit/                              # ── L0 ──
│   ├── markdown_spec/                 # check_markdown_spec 各规则判定
│   │   ├── test_citation_rule_flags_bad_reference.py
│   │   └── test_heading_rule_rejects_skipped_level.py
│   ├── footnote/                      # 脚注重排编号算法（纯计算）
│   │   ├── test_renumber_footnotes_merged_chapters_yields_sequential_ids.py
│   │   └── test_renumber_footnotes_cross_reference_stays_consistent.py
│   ├── template_config/               # 学校配置/模板参数解析
│   ├── numbering/                     # 目录/图表/公式编号计算
│   └── term_consistency/              # 术语归一化纯逻辑
├── integration/                       # ── L1（Hermetic：tmp_path + 真实文件解析）──
│   ├── docx_xml/                      # lxml/OOXML 真实解析与生成
│   ├── export_engine_linux/           # linux 引擎产出真实 docx 并解压断言 document.xml
│   ├── doc_intake/                    # 批注/模板格式提取（真实样本 docx）
│   ├── cover_merge_data/              # 封面拼接的数据层（不含 COM 调用）
│   └── spec_checker_pipeline/         # 检查器全规则串联跑真实 markdown
├── subsystem/                         # ── L2 ──
│   ├── export_service/                # 导出编排服务（COM 以契约桩替代，验证出向调用契约）
│   └── win_bridge/                    # wsl-windows-bridge 通道：pythonw/cmd fallback 契约
├── e2e/                               # ── L3 ──
│   ├── word_com/                      # @windows_com：真实 Word 渲染、转 PDF 核验
│   └── cli/                           # 完整 CLI 链路（md → 检查 → 导出 → 验证）
├── falsification/                     # ── L4 ──
│   ├── corrupted_docx/                # 损坏/畸形样本负向链路
│   ├── bridge_faults/                 # COM 超时、断连、弹窗阻塞注入
│   └── mutation/                      # mutmut 配置与元测试
└── fixtures/
    ├── docx_samples/<domain>/<case>/
    ├── markdown_samples/
    ├── corrupted/
    └── cassettes/                     # L2 录制契约（COM 调用记录、SaaS 响应）
```

### 2.2 Soul-Spark 标准 tests/ 目录树（含旧文件迁移映射）

现状（混乱样例）：`test_m1_backend.py / test_m2_stage.py / test_orm_t2.py / test_full_chain_t16.py / test_bot_inplace_t12.py ...` —— 全部按任务编号命名，同一领域散落多文件，无法从名字判断覆盖范围。重构后：

```
tests/
├── conftest.py
├── unit/                              # ── L0 ──
│   ├── time_basis/                    # ← test_time_basis.py
│   ├── intent/                        # ← test_llm_intent.py 的纯解析逻辑
│   ├── prompt_hygiene/                # ← test_llm_prompt_hygiene.py
│   ├── sparks_rules/                  # ← test_core.py 拆分
│   ├── sync_reconcile/                # ← test_sync_reconcile.py
│   └── cumulative_calc/               # ← test_cumulative_t9.py 的纯计算部分
├── integration/                       # ── L1（内存 SQLite + alembic 真实迁移）──
│   ├── storage_orm/                   # ← test_orm_t2.py
│   ├── migrations/                    # ← test_alembic_t3.py, test_migration_verify_t4.py
│   ├── db_concurrency/                # ← test_db_concurrency.py
│   ├── db_backends/                   # ← test_db_backends.py
│   ├── capture_ingest/                # ← test_attachments_t6.py, test_location_t7.py
│   ├── scheduler/                     # fake clock × 真实调度器
│   └── todo_archive/                  # ← test_todo_archive.py
├── subsystem/                         # ── L2 ──
│   ├── api/                           # ← test_m1_backend/m2_backend/m3_backend 中路由部分
│   │   ├── test_conversation_route_replies_persisted.py
│   │   └── test_todo_edit_route_validates_payload_contract.py
│   ├── feishu_bot/                    # ← test_bot_inplace_t12.py, test_bot_security_and_push.py
│   │   └── test_inplace_card_update_sends_contract_valid_request.py   # cassette 契约桩 + 出向 payload schema 验证
│   ├── realtime_ws/                   # ← test_realtime_contract.py, test_ws_adapter.py
│   ├── llm_gateway/                   # ← test_llm_settings_and_todo_edit.py 的 provider 交互
│   └── push_cumulative/               # ← test_push_cumulative_t10.py
├── e2e/                               # ── L3 ──
│   ├── web_spa/                       # @playwright：完整前端流转
│   ├── full_chain/                    # ← test_full_chain_t16.py：capture→intent→spark→push
│   └── stage_flow/                    # ← test_m1_stage/m2_stage/m3_stage 合并为按领域的阶段流转
├── falsification/                     # ── L4 ──
│   ├── bot_abuse/                     # ← test_security_and_robustness.py：伪造回调/越权消息
│   ├── queue_faults/                  # 事件丢失/重复/乱序注入
│   ├── db_faults/                     # 锁竞争、写中断
│   ├── runtime_drift/                 # ← test_issue47_runtime_and_time.py
│   └── mutation/
├── support/                           # 非测试代码：wait_until、contract_stub、fake_clock
│   ├── waiting.py
│   └── contract_stub.py
└── fixtures/
    ├── cassettes/feishu/              # 标注录制日期 + API 版本
    ├── events/
    └── db_snapshots/
```

**迁移规则**（一次性执行，单 PR 内完成）：
1. `git mv` 保留历史；一个旧文件按其中测试函数的领域**拆**到多个新文件（如 `test_m1_backend.py` 里的路由测试归 `subsystem/api/`，纯逻辑归 `unit/`）。
2. 旧名中的 t/m/w 编号一律丢弃；确需追溯的在函数 docstring 首行写 `Legacy: test_m1_backend.py::test_xxx`。
3. 迁移期间旧路径以 `collect_ignore` 屏蔽，防止双跑。
4. `test_repair_round2.py`、`test_ux_optimizations.py` 这类“过程性文件”按内容领域拆散后删除原文件——过程不是领域。

### 2.3 命名约定（强制）

| 对象 | 规则 | 正例 | 反例 |
|---|---|---|---|
| 测试文件 | `test_<domain>_<surface>.py`，domain 与目录一致，surface 是该文件覆盖的具体切面 | `test_footnote_renumber.py` | `test_m2_stage.py`, `test_t12.py` |
| 测试类 | `Test<Domain><Action>`，类内共享 arrange 时使用 | `TestFootnoteRenumberMergedChapters` | `TestM2` |
| 测试函数 | `test_<action>_<condition>_<expected_outcome>`（domain 已由路径/类承载，函数名不重复） | `test_renumber_footnotes_with_merged_chapters_yields_sequential_ids` | `test_ok`, `test_backend_3` |
| 负向用例 | expected_outcome 用 `rejects_/raises_/returns_<errcode>` | `test_edit_todo_with_missing_id_returns_422_and_error_code` | `test_edit_todo_fail` |
| fixture | `<domain>_<role>` | `feishu_cassette_bot_reply`, `memory_db_with_migration` | `db2`, `stuff` |
| golden 文件 | `fixtures/<domain>/<case>/expected.<ext>`，与 input 同目录 | | `golden_final_v2_new.docx` |
| 回归后缀 | 仅允许 `_regression_issue<N>` 后缀 | `test_ws_reconnect_keeps_seq_regression_issue47` | `test_issue47_runtime_and_time.py` |

函数名长度上限 80 字符；**条件段与结果段缺一不可**（只有 action 的名字如 `test_export` 直接拒收）。

---

## 第三部分：防假绿（Anti-False-Pass）七条工程铁律

> 每条 = 禁止项 + 强制项 + 自动检测手段（对应 `check_test_hygiene.py` 规则号）。

### R1 严禁 Mock 被测对象内部实现

- **禁止**：`patch` 目标落在被测包自身的私有成员（`app.services.export._render_section`）、被测类的内部方法（`patch.object(Exporter, "_build_xml")`）、以及“mock 掉被测函数本体再断言 mock 被调用”的自证式测试。
- **强制**：mock 只允许发生在**进程/系统边界**（外部 SaaS HTTP 层、COM 桥接层、LLM provider）。断言对象必须是真实返回值/副作用（DB 行、文件内容、渲染产物），而非“内部方法被调了几次”。`assert_called_*` 只允许作为契约验证的补充断言，不得是唯一断言。
- **检测**：AST 规则 R1 —— `patch("<own_pkg>...")` / `patch.object` 目标属于本项目包且指向私有属性 → 报错；测试内出现 `assert_called_*` 而无任何数据面断言 → 报错。

### R2 严禁仅断言 HTTP 状态码

- **禁止**：`assert resp.status_code == 200` 作为测试内唯一/最终断言；`assert resp`、`assert result is not None` 之类存在性断言充数。
- **强制**：每个走 HTTP 的测试至少断言**解码后业务载荷的具体字段值**（值相等，而非仅存在）；写操作必须追加**持久化验证**（重新查 DB/重读资源确认落库，覆盖“返回 200 但没写进去”的经典假绿）。
- **检测**：AST 规则 R2 —— 测试函数含 client 调用但断言集合 ⊆ {status_code, 存在性, is not None} → 报错。

### R3 禁止可能命中静态布局的模糊文案断言

- **禁止**：对页面断言“成功”“欢迎”“提交”“确定”等**初始 DOM 里本来就存在的通用文案**；禁止 `to_contain_text("OK")` 式短模糊串；禁止断言任何在动作发生前就已存在的文本（对着静态布局也能绿 = 假绿）。
- **强制**：DOM 文本断言必须锚定**动作后才出现的状态元素**——`data-testid` 唯一选择器 + 显式等待其 attached/visible；或断言来自被测数据的**长唯一串**（含 ID/时间戳/计算值）。任何文案断言前必须有一次显式 wait（network response / selector / 事件）。
- **检测**：AST 规则 R3 —— `to_contain_text`/`to_have_text`/`inner_text` 断言的字面量命中 banned-fuzzy 词表或长度 < 4，且函数内无 `data-testid` 定位与显式 wait → 报错。

### R4 异常断言必须校验 exc.value 与错误码

- **禁止**：`with pytest.raises(Exception):`（过宽基类，任何崩溃都能绿）；`pytest.raises` 后不检查异常内容；`try/except: pass` 吞掉失败。
- **强制**：`with pytest.raises(<具体异常>) as exc:` 之后必须断言 `exc.value` 的**错误码字段与关键消息内容**（业务错误码精确相等；消息至少断言关键 token）。负向链路必须验证到“调用方可感知的错误契约”为止，而不是“抛了个什么东西”。
- **检测**：AST 规则 R4 —— raises 块无 `as` 绑定、绑定后无引用 `exc.value` 的断言、异常类为 `Exception`/`BaseException`、测试体内 `except: pass` / `except Exception: pass` → 报错。

### R5 集成测试必须显式清理隔离环境并断言初始干净状态

- **禁止**：跨测试共享可变 DB/目录；模块级建立连接或 Session；依赖“上一个测试留下的数据”；测试结束后残留文件/行不清理。
- **强制**：一律使用 `tmp_path` / `sqlite:///:memory:` / 专用 `memory_db_with_migration` fixture；**setup 阶段断言初始干净**（如 `assert count_rows("sparks") == 0` 作为前置条件写进测试体首行）；teardown 断言无泄漏（临时目录外无新文件、连接已关闭）。L1+ 测试必须请求白名单内的隔离 fixture，否则收集报错。
- **检测**：AST 规则 R5 —— 模块作用域出现 `connect(`/`create_engine(`/`Session(` → 报错；路径含 `integration/` 的测试函数未引用任何白名单隔离 fixture（tmp_path / memory_db* / clean_*）→ 报错。插件在 L0/L1 施加 socket 熔断（`no_network` autouse fixture）。

### R6 异步/队列必须等待显式终态

- **禁止**：裸 `time.sleep(n)` / `asyncio.sleep(n)` / `page.wait_for_timeout(n)` 作为同步手段（“sleep 后断言”在快机器上绿、慢机器上红，是最典型的间歇假绿/假红来源）；对 WS/队列只断言“连接成功”不等终态帧。
- **强制**：统一使用白名单轮询助手 `wait_until(predicate, timeout, interval)`——超时即 fail 并打印**最后观测状态**（而非笼统 timeout）；WS/队列测试必须断言**终态帧/终态行的具体内容**（含 seq/状态字段）；事件顺序用序列号断言而非到达时间；前端操作等待具体 network response 或状态选择器。
- **检测**：AST 规则 R6 —— 测试体内直接调用 sleep 系 API → 报错（豁免 `tests/support/waiting.py` 助手自身）；文件名/内容含 ws、queue、event、async 关键词的测试既无 `wait_until` 也无显式终态断言 → 报错。

### R7 每个测试必须可证伪，绿色必须可解释

- **禁止**：恒真断言（`assert True`、`assert 1`、`assert x == x`）；条件断言（`if flag: assert ...`——flag 为假时静默绿）；空测试体 / 只有 `pass`；无 reason 的 `skip`；`xfail` 不带 `strict=True`（实现修好后测试悄悄变“意外通过”无人知晓）。
- **强制**：每个测试至少一个**内容断言**（比较具体值）；每条 L2/L3 关键链路配一个元测试证明“破坏实现 → 测试变红”；核心模块进变异测试预算（杀死率 ≥ 60%）；skip 必须带 `reason=`（建议附 issue 链接）；CI 输出每测试断言计数，**断言数为 0 的“绿”测试进红灯报表**。
- **检测**：AST 规则 R7 —— 常量断言、`if` 包裹的断言、空函数体、skip 无 reason、xfail 无 strict → 报错；pytest 插件统计断言数并生成 `--assert-report`。

### 铁律速查卡

| # | 一句话 | 拦截点 |
|---|---|---|
| R1 | Mock 只许打在系统边界，不许打进被测对象体内 | AST |
| R2 | 状态码不是结果，落库的字段值才是 | AST |
| R3 | 静态页面上就有的文案，断言了等于没断言 | AST |
| R4 | 抛异常不等于抛对了异常：校验 exc.value + 错误码 | AST |
| R5 | 环境先证明是干净的，测试绿了才有意义 | AST + fixture 白名单 |
| R6 | sleep 换来的绿是借来的，迟早要还 | AST + wait_until 强制 |
| R7 | 打不红的测试不是测试；skip/xfail 必须可审计 | AST + 插件断言统计 |

---

## 第四部分：可执行的质检辅助方案

### 4.1 工具链总览

```
开发写测试 ──► pre-commit（秒级）──► PR CI（分钟级）────► 夜跑/周跑（小时级）
                │                    │                    │
                ├ check_test_hygiene │ ├ L0+L1 全量        ├ L2 全量 + L3（专用 runner）
                ├ 命名/目录规则       │ ├ L2 core 子集      ├ L4 故障注入全量
                └ ruff/flake8        │ ├ hygiene 复检      ├ 变异测试（核心模块 kill ≥ 60%）
                                     │ ├ 断言统计门禁       ├ cassette 契约漂移哨兵
                                     │ └ 层级超时预算执行   └ flake/quarantine 债务看板
```

### 4.2 AST 规则检查器（`check_test_hygiene.py`，随本规范交付）

- 纯 stdlib（`ast` + `pathlib` + `argparse`），零依赖，任何环境可跑。
- 用法：
  ```bash
  python tools/check_test_hygiene.py tests/ --own-packages app,wordprod
  python tools/check_test_hygiene.py tests/ --rules R2,R4      # 只跑部分规则
  python tools/check_test_hygiene.py tests/ --rules naming     # 命名门禁
  python tools/check_test_hygiene.py tests/ --warn-only        # 存量摸底期
  ```
- 规则 R1–R7 + naming 与铁律一一对应；输出 `文件:行号 [R#] 说明`，非零退出码阻断。
- **豁免机制**：确需违规时行尾加 `# hygiene: allow R2 reason="COM 桩仅验证调用序列"`，检查器校验 reason 非空；豁免清单进 CI 周报（豁免总数上涨 = 债务上升）。

### 4.3 pytest 插件（`pytest_test_guard.py`，随本规范交付）

功能：
1. **层级标记强制**：按路径自动打 `l0–l4` marker；收集时既无层级 marker、路径又不在五层目录内 → 收集错误。
2. **命名门禁**：收集时校验文件名/函数名正则；命中 `test_(m|w|t)\d+` 旧任务编号模式直接收集失败，杜绝 m1/t7 回潮。
3. **超时预算**：L0=1s / L1=10s / L2=60s / L3=300s 单测硬超时（依赖 pytest-timeout，未安装时降级为警告）。
4. **网络熔断**：L0/L1 目录下 autouse `no_network` fixture 屏蔽 socket —— 尝试联网的“单元测试”当场失败并给出明确信息。
5. **断言统计**：通过 `pytest_runtest_makereport` + AST 预扫描统计每测试断言数，`--assert-report` 输出零断言测试清单；CI 中零断言测试数 > 0 → 红。
6. **skip 审计**：无 reason 的 skip → 报错；`xfail(strict=False)` → 警告。

接入方式（各项目根 conftest.py）：

```python
pytest_plugins = ["pytest_test_guard"]   # 或将插件放入 tests/support/ 并加 sys.path
```

### 4.4 pre-commit 配置

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: test-hygiene
        name: anti-false-pass AST rules (R1-R7)
        entry: python tools/check_test_hygiene.py
        args: ["--own-packages", "app"]
        language: system
        files: ^tests/.*[.]py$
      - id: test-naming
        name: test naming convention
        entry: python tools/check_test_hygiene.py
        args: ["--rules", "naming"]
        language: system
        files: ^tests/.*[.]py$
```

### 4.5 CI 分级门禁（GitHub Actions 要点）

```yaml
# pr.yml —— 快车道，必须全绿才可合并
- run: pytest tests/unit tests/integration -m "not slow" --strict-markers --assert-report
- run: pytest tests/subsystem -m "core"
- run: python tools/check_test_hygiene.py tests/ --own-packages app

# nightly.yml —— L2 全量 + L4 故障注入 + cassette 契约漂移哨兵
# weekly.yml —— L3（windows_com / playwright 专用 runner）+ mutmut（核心模块，kill-rate ≥ 60% 门禁）
```

### 4.6 落地路线（两阶段）

**阶段一（1 周内）**：
1. 拷贝 `check_test_hygiene.py` 与 `pytest_test_guard.py` 到各项目 `tools/`；
2. 先以 `--warn-only` 跑存量摸底，生成违规报表；
3. 按 2.2 映射表 `git mv` 迁移目录（单 PR，保留 git 历史）；
4. 根 conftest 挂插件，pre-commit 上线 naming + hygiene。

**阶段二（2–4 周）**：
1. 存量违规清零（无法立即修的走豁免 + reason，进债务看板）；
2. L2 契约桩补齐出向 payload schema 校验（见附录 B）；
3. 核心模块接入 mutmut，设 kill-rate 门禁；
4. L3 quarantine 目录与 flake 看板上线；
5. hygiene 从 warn-only 切为 block。

---

## 附录 A：pytest 标记注册（pyproject.toml）

```toml
[tool.pytest.ini_options]
markers = [
  "l0: pure unit, no IO, no mocks",
  "l1: hermetic local integration",
  "l2: subsystem with boundary contract mocks",
  "l3: e2e / real machine bridge",
  "l4: falsification / fault injection",
  "windows_com: requires Windows + Word COM runner",
  "playwright: requires browser runner",
  "core: PR-mandatory subset",
  "slow: excluded from PR fast lane",
]
addopts = "--strict-markers"
```

## 附录 B：L2 契约 Mock 参考实现（出向 payload 验证）

```python
# tests/support/contract_stub.py
from pathlib import Path
from jsonschema import validate

class FeishuContractStub:
    """录制回放 + 出向契约校验。
    任何 schema 不匹配的外发请求 → AssertionError（绝不静默放行），
    这是 L2 与“随手 mock 返回值”的本质区别。
    """

    def __init__(self, cassette_dir: Path):
        self.cassettes = load_cassettes(cassette_dir)  # 每条含录制日期 + API 版本
        self.sent: list = []

    def request(self, req):
        rec = self.cassettes[req.key]
        validate(instance=req.body, schema=rec.request_schema)   # ← 出向 payload 契约强制验证
        assert req.method in rec.allowed_methods
        assert req.url in rec.allowed_urls
        self.sent.append(req)
        return rec.response
```

## 附录 C：wait_until 参考实现

```python
# tests/support/waiting.py
import time

class WaitTimeout(AssertionError):
    pass

def wait_until(predicate, timeout: float = 10.0, interval: float = 0.1, state_repr=None):
    """轮询等待显式终态；超时时 fail 并打印最后观测状态（而非笼统 timeout）。"""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    detail = state_repr() if state_repr else f"last observed: {last!r}"
    raise WaitTimeout(f"terminal state not reached within {timeout}s — {detail}")
```
