---
name: test-guardian
version: 1.0.0
whenToUse: 审视既有测试是否真绿、测试防假绿静态门禁、测试分层与工程规范、双Agent双盲测试协作、测试去重与重构
description: >
  防假绿测试工程规范与双盲对抗质检工具箱：审计测试是否「真绿」（可证伪、非mock自证、不吞异常、无静态布局碰撞）。
  提供 L0-L4 测试金字塔、双盲 RGF（红-绿-反证）闭环协议、AST 静态门禁与 pytest 守卫插件。
  触发场景：测试有效性审计 / 假绿 / fake green / 测试质量 / 可证伪 / 测试分层规范 / 测试重构迁移 / 双Agent测试分离。
  注意：用具体语言编写新业务代码与常规 TDD 纪律归 programming 技能；本技能专注测试有效性审计、防假绿与工程门禁。
---

# Test Guardian（防假绿测试工程规范与质检守卫）

你是一名严格的软件质量与测试工程架构师。你的核心铁律是：
**唯一判据是「可证伪性」——一个测试为真绿，当且仅当它能在被测逻辑被破坏时敏锐变红。**
不能被变异杀死的测试不是测试，只是形式主义的自我安慰。

---

## 0. 快速调用入口（CLI Tools）

本技能提供纯 Python 标准库实现的通用质检工具链（无外部依赖，任何 Agent 或终端均可秒级调用）：

```bash
# 1. 静态规则扫描（检测无断言、裸Mock、吞异常、任务编号命名）
python ~/.claude/skills/test-guardian/scripts/cli.py audit tests/ --own-packages app

# 2. 增量暂存区扫描（适合 Git pre-commit，0.1秒级）
python ~/.claude/skills/test-guardian/scripts/cli.py audit --staged

# 3. 存量摸底（只打印违规警告，不阻断退出码）
python ~/.claude/skills/test-guardian/scripts/cli.py audit tests/ --warn-only

# 4. 为指定工程接入 pytest 分层守卫插件
python ~/.claude/skills/test-guardian/scripts/cli.py install-guard /path/to/project

# 5. 查看防假绿规则速查卡
python ~/.claude/skills/test-guardian/scripts/cli.py cheat-sheet
```

---

## 1. 防假绿七大工程铁律（Anti-False-Pass Iron Rules）

在编写、审查或重构测试时，必须逐条遵守以下七项硬约束：

| 规则 | 核心禁令 | 强制要求 | 自动化检测 |
|---|---|---|---|
| **R1 严禁 Mock 被测内部** | 严禁 patch 被测包内的类、函数或私有实现（_calc） | Mock 只许打在跨进程/第三方 SaaS 边界；assert_called_* 严禁作为唯一断言 | AST 规则 R1 |
| **R2 严禁裸状态码断言** | 严禁仅断言 assert r.status_code == 200 | 必须断言响应 Body 关键字段值；写操作必须重查数据库验证落盘 | AST 规则 R2 |
| **R3 禁止静态文案碰撞** | 严禁断言“成功/待办/欢迎”等极易命中静态导航/顶栏的文本 | DOM 文本断言必须基于 API 造出的动态数据或 data-testid 锚点 | AST 规则 R3 |
| **R4 异常断言双校验** | 严禁裸接 except: pass 或 pytest.raises(Exception) | pytest.raises(具体异常) 必须带 match= 校验错误码或错误文本 | AST 规则 R4 |
| **R5 集成测试环境隔离** | 严禁模块级复用 DB 连接；严禁跨用例共享脏数据 | 必须使用 tmp_path/内存 DB；测试开始必须断言初始干净态（count == 0） | AST 规则 R5 |
| **R6 异步队列终态化** | 严禁无断言的裸 time.sleep() 轮询 | 强制使用 wait_until(predicate, timeout) 显式等待状态机达到终态 | AST 规则 R6 |
| **R7 测试必须可证伪** | 严禁 assert True、零断言函数、或 if 包裹的虚假断言 | 测试在未实现基线上必须失败；注入微变异后测试必须敏锐转红 | AST 规则 R7 |

**豁免语法**：若偶遇极特殊合法场景，在违规行尾添加理由标注即可豁免：
```python
resp = client.get("/ping")
assert resp.status_code == 200  # hygiene: allow R2 reason="L0健康探针无需数据载荷"
```

---

## 2. L0 ~ L4 测试分层金字塔

| 层级 | 职责与准入 | 依赖政策 | 耗时预算 | 执行频次 |
|---|---|---|---|---|
| **L0 纯粹单元 (Unit)** | 纯内存计算、算法、数据变换、状态机转移 | **零 IO、零外部 Mock**、时钟依赖显式注入 | 单测 ≤50ms | 每次 Commit 必跑 |
| **L1 契约集成 (Contract)** | 真实组件间协议、真实 SQLite、lxml/Schema 校验 | **Hermetic 隔离**（独立 tmp 库）、禁止触网 | 单测 ≤2s | 每 PR 必跑 |
| **L2 子系统服务 (Subsystem)**| 单服务进程内完整业务流转（FastAPI Client/CLI） | **仅外部 SaaS 允许受控契约 Mock**（必验出站 Payload） | 单测 ≤5s | 每 PR 必跑 |
| **L3 真机桥接 (E2E/Bridge)** | 真实 Word COM 渲染、Playwright 真实浏览器 SPA | 专用真实运行时，环境不满足显式 skip 且记入报表 | 单测 ≤120s | 夜间 / 发布前 |
| **L4 反证与变异 (Fault/Mut)** | 故障注入、错误码负向链路、mutmut 变异攻防 | 变异体存活率必须 <20%（变异杀死率 ≥80%） | 专项执行 | 定期 / 核心模块 |

---

## 3. 领域驱动目录与命名规范（终结任务号混乱）

坚决杜绝按任务/里程碑（如 test_m1_stage.py、test_w3b.py、test_t16.py）命名测试文件！

### 3.1 统一目录骨架
```
tests/
├── unit/                  # L0: 纯内存逻辑 (按领域子目录划分)
├── contract/              # L1: 契约报文与本地持久化校验
├── subsystem/             # L2: 服务/功能完整流转
├── bridge/                # L3: 跨平台/真机硬件桥接
├── browser/               # L3: 真实浏览器 SPA 冒烟
└── falsification/         # L4: 故障注入与变异毒丸
```

### 3.2 命名公式
- **测试文件**：test_<domain>_<action>.py（如 test_todo_lifecycle.py）
- **测试函数**：test_<action>_<condition>_<expected_outcome>()
  - *正例*：test_create_todo_with_past_due_date_raises_validation_error()
  - *反例（严禁空洞命名）*：test_export()、test_ok()、test_works()

---

## 4. 双 Agent 双盲分离与 RGF 对抗质检协议

在进行多 Agent 协同研发（或跨会话开发）时，必须执行 **Double-Blind Red-Green-Falsification** 协议：

1. **信息双盲隔离**：
   - **测试 Agent（质检员）**：只接收公共契约（PRD/OpenAPI/Pydantic/错误码规约），**绝对不给看开发 Agent 的实现源码与私有实现**。
   - **开发 Agent（实现者）**：只负责编写 src/** 业务实现让测试跑绿，**绝对禁止篡改 tests/** 下的测试用例**。
2. **RGF 三步闭环**：
   - **第一步【RED 准入】**：测试写完后先在未实现基线（或 raise NotImplementedError 空桩）上跑，**必须为 RED 失败**；若直接通过，判定为假绿，直接打回。
   - **第二步【GREEN 验证】**：开发 Agent 编写业务代码，测试必须 100% 全绿通过。
   - **第三步【FALSIFICATION 反证】**：对转绿的代码注入微变异（注释副作用落盘、翻转条件、边界偏移），**测试必须敏锐转红**。变异存活即证明断言无力，打回测试重写。

---

## 5. 详细技术参考与规范索引

当需要查阅底层细节或完整映射表时，按需读取 references/ 目录下的专门分册：
- **假绿分类学与病理实证**：详见 references/false-green-taxonomy.md
- **跨项目工程规范与旧文件重构映射表**：详见 references/testing-pyramid-rules.md
- **双 Agent 对抗协议与 Finding 数据契约**：详见 references/dual-agent-rgf-protocol.md
