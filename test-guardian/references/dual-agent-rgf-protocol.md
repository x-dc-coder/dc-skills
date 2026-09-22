# 双 Agent 双盲分离与对抗质检协议（Double-Blind RGF Protocol）

> **标准代号**：DSH-SPEC-RGF-2026  
> **版本**：v1.0 · 2026-09-18 · 工业级实施规范  
> **适用架构**：DSH AgentTeams 多智能体研发协作、自动化 CI/CD 质量闭环、高可靠系统工程  
> **核心法则**：写实现的人不得批自己的卷；黑盒契约斩断过度 Mock；未被反证证伪的测试一律视为假绿。

---

## 1. 背景与核心痛点根源分析

在单智能体（Single Agent）或松散多智能体（Unconstrained Multi-Agent）软件工程中，让编写业务实现的 Agent 同时编写或自裁其测试，存在三大结构性病理：

```
               【单 Agent / 伪分离下的自证病理闭环】
  ┌────────────────────────────────────────────────────────┐
  │ 1. 认知偏见 (Confirmation Bias)                        │
  │    实现者带着对特定实现路径的偏好设计测试，对隐式边界、 │
  │    非预期异常与并发竞态天生盲目。                      │
  ├────────────────────────────────────────────────────────┤
  │ 2. 内部紧耦合与过度 Mock (Internal Leakage)            │
  │    深知内部私有 helper 实现，单测直接 patch 内部私有方法，│
  │    重构即崩；或者 mock 掉真实数据链路，形成“无痛过关”。  │
  ├────────────────────────────────────────────────────────┤
  │ 3. 假绿共谋与静默退化 (Collusion & Silent Degradation) │
  │    测试不通过时，Agent 拥有同时修改实现与测试的双重权力，│
  │    极易演变为放宽断言（assert is not None）、吞掉异常   │
  │    （except: pass），使测试蜕变为无防御力的“橡胶图章”。│
  └────────────────────────────────────────────────────────┘
```

多 Agent 协作时若只在 Prompt 里做口头分工（“你是开发”、“你是测试”），但缺乏**物理级信息隔离**与**严谨的执行契约**，往往迅速坠入以下协同陷阱：
1. **死锁推诿（Finger-pointing Deadlock）**：Test Agent 自由脑补未定义的边界条件并断言失败，Dev Agent 坚持认为属于未定义行为拒绝修改，双方在自然语言中无休止扯皮。
2. **盲目放行（Rubber Stamping）**：Test Agent 能够读取 Dev Agent 的代码实现，被 Dev Agent 的代码逻辑“带偏”，依葫芦画瓢写出同构测试，把 bug 顺理成章地放行。
3. **无限驳回与震荡破坏（Infinite Rejection Churn）**：Test Agent 给出抽象模糊的“不合格”结论，Dev Agent 盲目打补丁，修好 A 破坏 B，引发震荡。

必须在底层机制上确立**双盲输入隔离（Double-Blind）**、**红-绿-反证（RGF）三步闭环**与**确定性仲裁状态机**。

---

## 2. Dev Agent 与 Test Agent 双盲分离机制（Double-Blind Protocol）

### 2.1 双盲拓扑结构与信息边界

双盲分离的原则：**Dev Agent 不参与测试用例编写，且在实现时不可见具体的私有测试用例细节；Test Agent 永远看不见 Dev Agent 的源码实现、私有变量与推演思考过程。两者只通过“不可抵赖的公共契约规范”和“沙箱测试执行结果”进行信息交互。**

```
                       ┌────────────────────────────────┐
                       │   需求与接口契约 (Contract)    │
                       │   - OpenAPI / Pydantic / RPC   │
                       │   - 状态机不变量 & 错误码规约   │
                       └───────┬────────────────┬───────┘
                               │                │
            ┌──────────────────▼──┐          ┌──▼──────────────────┐
            │      Dev Agent      │          │     Test Agent      │
            │  (实现者 Implementer)│          │  (对抗者 Adversary) │
            └──────────┬──────────┘          └──┬──────────────────┘
                       │                        │
             写出业务实现 (src/**)             写出契约测试 (tests/**)
             【看不见测试用例细节】            【看不见实现私有源码】
                       │                        │
                       └───────────► ┬ ◄────────┘
                                     │
                             ┌───────▼───────┐
                             │ 沙箱仲裁执行器 │
                             │  (Sandbox)    │
                             └───────────────┘
```

### 2.2 信息输入隔离矩阵（Whitelist vs Blacklist）

对 Test Agent 的 Context 注入实施严格的物理级准入与拦截过滤：

| 隔离维度 | 白名单（Test Agent 必须且仅接收的内容） | 黑名单（Test Agent 严禁接收的内容） | 物理拦截原因 |
|---|---|---|---|
| **需求与规格** | 需求规格书（PRD / User Story）、验收标准（Acceptance Criteria）、业务边界约束（Pre/Post-conditions, Invariants） | Dev Agent 的架构选型推演、业务实现草稿、开发内部思考链（CoT） | 阻断“顺藤摸瓜”式推演，确保测试用例完全独立源自真实用户与系统契约 |
| **接口与类型** | 公共接口签名（Public Function Signature）、OpenAPI/Schema、CLI 参数/选项/环境变量规约、公开自定义异常类型（Custom Exceptions） | 私有函数（`_internal_*`）、局部变量、内部类、私有 helper 方法、模块内部临时状态 | 剥夺针对内部私有细节的白盒窥探权，强制将被测系统视为黑盒/灰盒 SUT |
| **测试脚手架** | 真实的基础设施 Harness（公用 test DB 容器、进程外 mock server 端口、标准冻结时钟 fixture、隔离 `tmp_path`） | Dev Agent 在其代码内私设的单测 mock、Dev 篡改的全局配置、Dev 编写的“示例测试代码” | 防止 Dev Agent 通过提供 mock 样例反向污染、驯化 Test Agent 的断言准则 |
| **通信通道** | 结构化仲裁报文（包含退出码、可复现命令、最小复现输入、预期与实际 diff 的 Finding） | 自然语言形式的辩论、无测试代码佐证的情绪化推诿 | 消除模糊自然语言扯皮，让所有分歧与交互收敛于确定性代码证据 |

### 2.3 为什么黑盒/契约输入是消灭“过度 Mock 与内部耦合”的关键

1. **物理级剥夺针对私有逻辑的 Mock 命名权**：
   - 传统单 Agent 单测中，Agent 极易写出与内部结构强耦合的“易碎测试”：
     ```python
     # 典型脆弱的内部耦合测试（单 Agent 伪测试）
     @patch("app.order.service._validate_coupon_internal") # 私有函数
     @patch("app.order.service._calc_tax_step2")           # 内部临时 helper
     def test_checkout(mock_tax, mock_coupon):
         mock_tax.return_value = 10
         # 当服务重构合并税率计算步骤时，测试全挂；当内部私有逻辑写出 bug 时，测试全绿！
     ```
   - 在双盲契约下，Test Agent 仅能查阅公共契约文档：`OrderService.checkout(order_req: OrderRequest) -> OrderResponse`。Test Agent 在物理视界内**完全不知道**内部有无 `_validate_coupon_internal` 或 `_calc_tax_step2`。
   - **唯一可行的测试编写方式**：Test Agent 必须通过构造真实的 `OrderRequest` 领域实体，传入公开的 `checkout()` 入口，断言真实的输出 `OrderResponse` 以及数据库/事件总线可观测的副作用。
2. **Mock 自动收敛至系统外部边界，保障重构自由**：
   - 因为无法穿透内部，所有的 Mock / Stub 只能被迫建立在**契约明确约定的跨进程/第三方物理边界**（如：飞书 OpenAPI 网关、第三方支付网关、LLM 推理端点、硬件 COM 端口）。
   - 业务逻辑内部完全以真实代码路径执行，实现**测试与重构的彻底解耦**——Dev Agent 无论如何重构内部结构（抽取 helper、改为策略模式、状态机解耦），只要外部契约不变，Test Agent 的测试套件永远坚挺有效，成为真正的工程重构安全网。

---

## 3. 核心质检流程：“红-绿-反证”（RGF）三步闭环

传统 TDD 的 Red-Green-Refactor 依赖人类工程师的自觉。在多智能体对抗环境中，必须升级为具备**硬性拦截判据与变异攻防的 Red-Green-Falsification (RGF) 三步闭环**：

```
                    ┌─────────────────────────┐
                    │ 需求与接口契约发布 (Req) │
                    └────────────┬────────────┘
                                 │
     ┌───────────────────────────▼───────────────────────────┐
     │  第一步：RED 准入 (Pre-condition Ingress)              │
     │  - Test Agent 依据契约编写测试 (tests/test_feature.py) │
     │  - 在未修改代码基线 / 空实现桩 (Stub) 上执行测试       │
     │  - 判定：测试结果【必须为 RED (FAILED)】！              │
     │    * 若测试竟然是 GREEN -> 判定为【假绿测试】，直接打回 │
     │    * 若测试为 RED 且原因匹配契约缺口 -> 准入通过      │
     └───────────────────────────┬───────────────────────────┘
                                 │ 准入测试套件 (Frozen Tests)
     ┌───────────────────────────▼───────────────────────────┐
     │  第二步：GREEN 验证 (Implementation Conformance)       │
     │  - Dev Agent 仅修改实现代码 (src/**)                   │
     │  - 在沙箱中接入准入的测试套件执行                      │
     │  - 判定：测试结果【必须 100% 为 GREEN】                │
     │    * 若出现 FAILED -> 提取结构化 Finding，Dev 修复     │
     │    * 若 Dev 试图篡改 tests/ -> 门禁强制阻断 (E_MUTATE) │
     └───────────────────────────┬───────────────────────────┘
                                 │ 绿态全通 (Green Certified)
     ┌───────────────────────────▼───────────────────────────┐
     │  第三步：FALSIFICATION 反证/变异攻防 (Adversarial)     │
     │  - 对 Dev Agent 提交的 SUT 代码进行微小变异注入:       │
     │    * 注释副作用保存 (如 db.commit(), send_event())    │
     │    * 状态机翻转 (如 == 改为 !=, return True 改 False)  │
     │    * 边界偏移 (如 >= 改为 >, timeout 设为 0)          │
     │  - 重新运行测试套件                                   │
     │  - 判定：测试【必须敏锐转红 (RED)】！                  │
     │    * 若变异体存活 (仍为 GREEN) -> 判定断言无力，打回测试│
     │    * 若变异体被全部杀死 (全部 RED) -> 判定具备实战防御力│
     └───────────────────────────┬───────────────────────────┘
                                 │ 终审通过 (Certified Solid)
                    ┌────────────▼────────────┐
                    │   合并与交付 (Deliver)   │
                    └─────────────────────────┘
```

### 3.1 第一步【RED 准入】：真伪测试准入检验（Pre-condition Ingress）

- **执行规程**：
  1. Test Agent 基于契约规格，独立编写测试套件（`tests/domain/test_feature.py`）。
  2. 自动化沙箱将该测试套件挂载到**当前主干（未修改的 Baseline 代码）**或**仅声明接口类型但内部抛出 `NotImplementedError` 的空桩**上运行。
- **裁决准则**：
  - **PASS（准入合格）**：测试必须全面失败（RED），且失败原因必须精准匹配契约所声明的功能缺口（如：端点返回 404/501、异常为 `NotImplementedError`、断言期望的新字段不存在）。
  - **REJECT（假绿打回）**：若测试在空桩或基线上直接通过（Exit Code = 0），表明测试存在致命缺陷：
    * **恒真测试（Tautology）**：测试写了 `assert True` 或断言了永真条件；
    * **零断言（Zero Assertion）**：仅调用接口却无断言语句；
    * **异常吞咽（Exception Swallowing）**：测试内部使用了宽泛捕获 `except Exception: pass`。
  - **处置动作**：沙箱立即熔断，直接打回 Test Agent 重写测试，测试套件绝不允许进入 Dev 环节。

### 3.2 第二步【GREEN 验证】：契约遵从性实现验证（Conformance Verification）

- **执行规程**：
  1. 将通过 RED 准入的测试套件作为只读基准（Frozen Baseline）注入执行环境。
  2. Dev Agent 获得需求契约以及测试用例的运行入口（但不可见内部测试源码的实现技巧），在授权的业务源码目录（`src/**` 或 `app/**`）内进行编码。
  3. 执行全量测试命令：`pytest tests/domain/test_feature.py -v`。
- **裁决准则**：
  - 测试用例必须 **100% 通过（All Green, Exit Code = 0）**。
  - **防作弊硬门禁（Tampering Prevention）**：Dev Agent 的写权限严格受限，若变更集（diff）触碰了 `tests/` 目录，CI/Guard 立即触发 `SECURITY_VIOLATION`，直接判定失败并强制回滚。

### 3.3 第三步【FALSIFICATION 反证/变异攻防】：变异对抗与防御力核验（Adversarial Falsification）

> **反证铁律**：未经历过故障注入检验的“绿”，只是脆弱的假象；不能敏锐感知代码破坏的测试，在生产环境中毫无防御力。

- **执行规程**：
  在代码全绿通过后，由 Test Agent 或独立质检裁判器（Arbitrator）对 Dev Agent 编写的被测业务代码进行**微变异注入（Surgical Mutation Injection）**。每次仅改动一处核心语义，生成变异体（Mutant）。
- **四大核心变异算子（Mutation Operators）**：
  1. **副作用抹除算子（Side-Effect Erasure）**：
     - 注释掉状态持久化、缓存刷新或外部事件广播。
     - *注入示例*：`db.session.commit()` -> `pass`；`message_queue.send(event)` -> `pass`。
     - *攻防意图*：击穿那些“只断言了函数返回值，但完全没验证数据落盘与事件广播”的肤浅断言。
  2. **分支与状态机翻转算子（Branch & State Inversion）**：
     - 翻转业务核心逻辑的判定条件。
     - *注入示例*：`if token.is_valid and not token.is_expired:` -> `if token.is_valid and token.is_expired:`；`status = OrderStatus.PAID` -> `status = OrderStatus.CANCELLED`。
     - *攻防意图*：验证测试是否真正覆盖了非法路径的阻断与状态流转的完备性。
  3. **边界值滑移算子（Off-by-One / Boundary Shift）**：
     - 将临界比较符与数值做微量偏移。
     - *注入示例*：`if retry_times >= max_retries:` -> `if retry_times > max_retries:`；`page_size = 50` -> `page_size = 49`。
     - *攻防意图*：检验测试用例是否包含边界等价类与极限值测试，杜绝粗放测试。
  4. **错误规约破坏算子（Error Suppression）**：
     - 将原本应当抛出的业务异常改为返回 `None` 或静默忽略。
     - *注入示例*：`raise ResourceNotFoundError(...)` -> `return None`。
     - *攻防意图*：验证调用方断言是否严格区分了正常返回值与异常契约。
- **反证裁决判定（Falsification Criteria）**：
  - **变异体全灭（All Mutants Killed -> RED）**：变异后的损坏代码重新运行测试，测试套件**必须全部敏锐失败（转红）**。这证明测试用例精准锁定了关键业务逻辑，具备极高的缺陷捕获能力。
  - **变异体存活（Mutant Survives -> GREEN）**：变异后的错误代码运行测试，测试竟然**依然全绿通过**！
    * **裁决结果**：判定该测试存在断言无力或严重盲区（Weak Assertion / False Green）。
    * **惩治动作**：打回 Test Agent，要求精准补充针对该变异算子的高力度断言（L2 等值/L3 性质断言），直至将存活变异体全部击杀。

---

## 4. DSH / AgentTeams 落地契约设计

### 4.1 锚定 DSH AgentTeams 架构事实与硬约束

落地协议必须基于 DeepSeek Harness 的真实底层契约设计，严禁违背系统事实：

| DSH 底层事实 | 协议落地机制 |
|---|---|
| **F4: `memberProvider: spawn`** | 成员不继承队长或其他成员的对话历史，天然实现**物理级双盲隔离**，消灭多 Agent 上下文互串与协同偏见。 |
| **F5: `protocol` 截断 240 字符** | 团队全局 protocol 必须压缩至最核心硬核铁律，不写冗长叙述。 |
| **F5: `executionPrompt` 替换而非追加** | 团队通用要求必须逐个硬编码写入每个成员专属的 `executionPrompt`。 |
| **F5: 依赖结果注入截断 2000 字符** | 上游任务完成后自动注入下游的数据仅保留前 2000 字符。**必须强制所有角色执行“结论前置 + 结构化 YAML/JSON 契约化摘要”**。 |
| **Quality Kinds 机制** | 严密结合系统原生生命周期：`requirements` -> `implementation` -> `verification` -> `review`。当 review 判定 `needs_revision` 时，DSH 引擎自动插入 `repair` 任务并重新链接依赖。 |

### 4.2 角色分工与职责（RACI 矩阵）

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                   RACI 职责分配矩阵                                      │
├────────────────────┬──────────────┬──────────────────┬──────────────────┬────────────────┤
│ 阶段 / 产出物      │ Captain      │ Dev Agent        │ Test Agent       │ Arbitrator     │
│                    │ (编排调度)   │ (engineer)       │ (adversary)      │ (reviewer)     │
├────────────────────┼──────────────┼──────────────────┼──────────────────┼────────────────┤
│ 需求与契约制定     │ Accountable  │ Consulted        │ Consulted        │ Informed       │
│ RED 准入测试编写   │ Informed     │ Out of Scope(禁) │ Responsible      │ Accountable    │
│ SUT 业务代码实现   │ Informed     │ Responsible      │ Out of Scope(禁) │ Accountable    │
│ GREEN 验绿执行     │ Informed     │ Consulted        │ Responsible      │ Accountable    │
│ FALSIFICATION 反证 │ Informed     │ Out of Scope(禁) │ Responsible      │ Accountable    │
│ 终审与争议裁决     │ Consulted    │ Informed         │ Informed         │ Responsible    │
└────────────────────┴──────────────┴──────────────────┴──────────────────┴────────────────┘
```

- **Captain (调度队长)**：负责规划 staged plan，设置超时时间与任务依赖，不直接参与业务编写。
- **Dev Agent (`developer`)**：负责业务代码实现与修复。边界硬约束：`inScope: ["src/**", "app/**"]`, `outOfScope: ["tests/**"]`。
- **Test Agent (`adversarial-tester`)**：负责对抗性测试编写与变异攻击。边界硬约束：`inScope: ["tests/**"]`, `outOfScope: ["src/**", "app/**"]`。
- **Arbitrator (`green-arbitrator`)**：中立质量裁判官。负责审计 RED、GREEN、FALSIFICATION 证据链并给出最终裁决。

### 4.3 DSH 任务 DAG 编排（Quality Kinds 完整配置）

在 AgentTeams staged plan 中完整编排的流水线 DAG：

```yaml
tasks:
  - id: t1-contract
    kind: requirements
    round: 1
    subject: "契约定义：接口签名、状态机与边界规约"
    assignee: green-arbitrator
    objective: "确定业务接口公共契约与不可变约束"
    acceptance: ["产出包含输入输出/错误码/边界约束的结构化契约"]

  - id: t2-red-ingress
    kind: verification
    round: 1
    subject: "RED准入：编写契约测试并在未实现基线上验红"
    assignee: adversarial-tester
    dependencies: [t1-contract]
    objective: "编写高断言力度测试，在基线上执行证明其失败"
    inScope: ["tests/**"]
    outOfScope: ["src/**", "app/**"]
    verify: ["pytest tests/ -v"]
    acceptance: ["测试运行结果严格为 FAILED", "失败原因确认为未实现/断言失败而非语法错误"]

  - id: t3-green-impl
    kind: implementation
    round: 1
    subject: "GREEN实现：根据契约与测试编写业务代码"
    assignee: developer
    dependencies: [t2-red-ingress]
    objective: "编写业务代码使准入测试全绿通过"
    inScope: ["src/**", "app/**"]
    outOfScope: ["tests/**"]
    verify: ["pytest tests/ -v"]
    acceptance: ["全量准入测试 100% PASS", "无新测试或篡改测试行为"]

  - id: t4-falsification
    kind: verification
    round: 2
    subject: "反证攻防：对实现代码实施变异注入"
    assignee: adversarial-tester
    dependencies: [t3-green-impl]
    objective: "注入副作用抹除/边界滑移等变异体，检验测试转红能力"
    inScope: ["tests/**"]
    acceptance: ["针对变异体测试必须敏锐失败 (RED)", "变异体存活率必须为 0%"]

  - id: t5-rgf-review
    kind: review
    round: 1
    subject: "RGF终审：双盲质检全闭环证据审计"
    assignee: green-arbitrator
    dependencies: [t4-falsification]
    reviewedTaskId: t3-green-impl
    objective: "审核 RED/GREEN/FALSIFICATION 完整证据链"
    acceptance: ["三步证据完备且无可疑假绿特征"]
```

---

## 5. 状态转移机、Finding 契约与防死锁上限保护

### 5.1 结构化 Finding 通信契约规范

在 GREEN 阶段发生断言失败，或在 FALSIFICATION 阶段发生变异体存活时，Test Agent 严禁发送非结构化的口头叙述，必须生成标准化 Finding 报文：

```json
{
  "finding_id": "FINDING-RGF-20260918-001",
  "severity": "blocker",
  "stage": "GREEN_VERIFICATION",
  "contract_ref": "SPEC-IDEMPOTENCY-§2.1",
  "target_file": "app/services/transfer.py",
  "problem": "相同 Idempotency-Key 的重试请求导致账户发生了二次扣款，未实现幂等阻断",
  "repro_command": "pytest tests/domain/test_transfer.py -k test_idempotent_duplicate_transfer -vv",
  "minimal_case": {
    "given": "账户 A 余额 1000 元，发起转账 100 元，带 UUID-Key-01；随后以相同 Key 再次重试",
    "expected": "第二次请求返回 200 OK，返回首次 transfer_id，账户 A 最终余额为 900 元",
    "actual": "第二次请求返回 200 OK，生成了新 transfer_id，账户 A 最终余额为 800 元"
  },
  "required_fix": "在 transfer.py 开启数据库事务前，原子性校验并占有 Idempotency-Key，命中冲突则直接返回已存订单快照"
}
```

### 5.2 状态转移机（State Machine）

```
                   ┌────────────────┐
                   │  RED_INGRESS   │
                   └───────┬────────┘
                           │ Baseline Test Fails (RED)
                           ▼
                   ┌────────────────┐  Tests Pass on Baseline
                   │ GREEN_VERIFY   ├────────────────────────┐
                   └───────┬────────┘                        │
                           │ All Tests Pass (GREEN)          │
                           ▼                                 │
                   ┌────────────────┐                        ▼
                   │  FALSIFICATION │                ┌───────────────┐
                   └───────┬────────┘                │ REJECT_TEST   │
                           │ All Mutants Killed (RED)| (假绿测试打回)│
                           ▼                         └───────────────┘
                   ┌────────────────┐
                   │ RGF_CERTIFIED  │
                   └────────────────┘
                           ▲
                           │ (修复成功通过)
       ┌───────────────────┴───────────────────┐
       │                                       │
Finding 驳回 (needs_revision)                  │ Finding 驳回 (Mutant Survives)
       │                                       │
       ▼                                       ▼
┌───────────────┐                       ┌───────────────┐
│ REPAIR (Dev)  │                       │ TEST_STRENGTH │
│  修复实现代码 │                       │  补齐断言力度 │
└───────┬───────┘                       └───────┬───────┘
        │                                       │
        └───────────────┬───────────────────────┘
                        ▼
                【轮次计数器 Round++】
                        │
                Round > 3 触发熔断
                        ▼
              ┌───────────────────┐
              │  ESCALATE_ARBITR  │
              │   (人工/队长仲裁)  │
              └───────────────────┘
```

### 5.3 超时与上限保护机制（Anti-Deadlock Guard）

为杜绝多 Agent 在自动化循环中出现“修复引入新 bug、测试不断变更规则”的无休止震荡，设立三道安全护栏：

1. **修复轮次熔断（Max Repair Rounds = 3）**：
   - 任务 `round` 严格受控。如果同一任务经历 3 轮 `repair` + `review` 后仍未通过，调度引擎直接触发 `ESCALATION` 挂起任务，禁止 Agent 自行继续空转消耗 token。
2. **三级仲裁定责机制（Three-Branch Arbitration）**：
   仲裁者（Captain / Arbitrator）在熔断后介入，按严密的因果决策树定责：
   - **分支 1（契约二义性）**：测试断言的逻辑未在契约规格中明确。
     * *判决*：Test Agent 越权，作废争议测试用例，更新契约。
   - **分支 2（实现死锁）**：Dev Agent 架构陷入局部最优解，无法在既有结构下修复。
     * *判决*：重构实现策略，派发新的架构重构子任务，重置轮次。
   - **分支 3（脆弱测试 Flaky Test）**：测试本身不稳定（如依赖未隔离的时间戳、并发竞争）。
     * *判决*：判定测试用例不符合 Hermetic 规范，强制由 Test Agent 重构测试夹具。
3. **确定性超时保护**：
   - 单元测试命令必须配置 `--timeout=30`（单测 30s 熔断），集成验证 `--timeout=120`。
   - 一旦超时自动判定为严重阻塞缺陷（Severity: blocker），防止死循环卡死整个流水线。

---

## 6. DSH 生产配置文件草案（cordis.patch.yml 生产集成）

可直接追加至 `~/.dsh/profiles/web/cordis.patch.yml` 的生产 Profile：

```yaml
      # ── 5) 双盲对抗质检：开发/测试双盲分离 + RGF 闭环 ──
      # 用法：/agent-teams --profile dev-test-rgf <需求目标或工单>
      dev-test-rgf:
        description: 双盲对抗研发质检：开发与测试信息双盲隔离，执行红-绿-反证(RGF)三步闭环
        protocol: 开发者禁写测试，测试者禁读源码。先验红准入，转绿后必经变异反证，变异存活即打回。
        taskPlanning: seed
        members:
          - name: adversarial-tester
            role: 对抗质检工程师：写失败测试准入(RED) + 执行变异攻防反证(FALSIFICATION)
            provider: baicai
            model: gpt-5.6-sol
            reasoning_effort: high
            fallback:
              provider: deepseek-official
              model: deepseek-flash
            executionPrompt: >-
              你是对抗质检测试者。禁止查阅开发者具体实现源码与私有方法，仅依据需求契约断言外部可观测行为。
              你的任务分为两步：
              1. RED 准入：在未修改基线或桩代码上运行测试，证明测试必须失败(RED)。在基线上直接通过的假绿测试视为严重违规。
              2. FALSIFICATION 反证：在实现代码转绿后，对业务逻辑注入微小变异(注释副作用保存/翻转条件/偏移边界)。
              测试用例必须能够精准捕获变异体并转红(RED)；若变异存活，必须补齐高断言力度(L2+/L3)。
              输出要求：结论前置(首段给测试清单与断言力度等级)，报告遵循 JSON 契约。

          - name: developer
            role: 敏捷实现工程师：仅根据契约与准入测试编写业务代码使测试转绿(GREEN)
            provider: agentrouter
            model: glm-5.3
            reasoning_effort: high
            fallback:
              provider: deepseek-official
              model: deepseek-flash
            executionPrompt: >-
              你是业务开发工程师。你的唯一职责是编写业务实现代码使得上游冻结的测试全绿通过(GREEN)。
              硬性红线：绝对禁止新增、修改或删除任何 tests/ 目录下的测试文件与断言逻辑！动测试文件直接判定不合格。
              测试未通过时，仔细阅读结构化 Finding 中的 repro_command 与 expected/actual 对比进行针对性修复。
              输出要求：结论前置，列出修改文件清单与核心改动摘要。

          - name: green-arbitrator
            role: 质量裁判长：契约发布、RGF 证据链审计与终审裁决(APPROVE/needs_revision)
            provider: xlf-responses
            model: gemini-3.8-flash
            reasoning_effort: high
            fallback:
              provider: deepseek-official
              model: deepseek-flash
            executionPrompt: >-
              你是质量仲裁官。严格依据 RGF 三步证据链进行裁决：
              1. 核查 RED 阶段是否具有真实失败日志；
              2. 核查 GREEN 阶段全量测试是否 100% 绿且 Dev 未碰测试代码；
              3. 核查 FALSIFICATION 阶段变异体是否全部被测试杀死。
              只有当三项证据完全闭环时方可给出 APPROVE。任何变异存活或假绿嫌疑，立即裁决 needs_revision 并附带 Finding 证据行。
              轮次上限保护：当修复轮次超过 3 轮时，启动仲裁者终审程序，排查契约歧义与测试越界。

        tasks:
          - id: red-ingress
            subject: RED准入：基于契约写测试并在基线上验红
            description: 依据需求编写用例，在未修改代码基线上运行，必须为 FAILED。结论前置输出测试清单与断言力度。
            assignee: adversarial-tester

          - id: green-implementation
            subject: GREEN实现：编写业务实现使测试全绿(禁动测试)
            description: 上游 red-ingress 结果自动注入。仅修改业务源码转绿。严禁修改 tests/ 目录。
            assignee: developer
            dependencies: [red-ingress]

          - id: falsification-attack
            subject: FALSIFICATION反证：注入变异验证测试防御力
            description: 对 green-implementation 实现代码进行变异注入(抹除副作用/翻转状态)，验证测试是否敏锐转红。
            assignee: adversarial-tester
            dependencies: [green-implementation]

          - id: rgf-final-audit
            subject: RGF终审：双盲质检全闭环证据审计与放行
            description: 汇总 RED/GREEN/FALSIFICATION 证据链。全部达标裁决 pass，存活/假绿裁决 needs_revision。
            assignee: green-arbitrator
            dependencies: [falsification-attack]
```

---

## 7. 典型推演实战案例：账户转账幂等性需求

为直观展示双盲分离与 RGF 闭环的落地全流程，以金融核心场景“转账幂等性与余额守恒”为例：

### 阶段 1：契约发布（Contract）
- 接口：`POST /api/v1/transfers`，Header：`X-Idempotency-Key: <UUID>`。
- 契约不变量：
  1. 余额守恒：`A.balance_after + B.balance_after == A.balance_before + B.balance_before`；
  2. 幂等阻断：相同 `X-Idempotency-Key` 提交两次，第二次必须返回 `200 OK` 且附带第一次相同的 `transfer_id`，但底层账本扣款只发生 1 次。

### 阶段 2：RED 准入（Test Agent）
- **Test Agent 动作**：编写 `tests/domain/test_transfer.py`，构造并发两个相同 Key 的转账请求，断言账本扣款记录数等于 1。
- **未修改基线执行**：在当前老版本代码上执行 `pytest tests/domain/test_transfer.py`。
- **运行结果**：`FAILED (Status 404: Endpoint /api/v1/transfers not found)`。
- **RED 准入通过**：证实测试真实感知到了功能缺失，并非虚假恒真测试，测试套件被锁定。

### 阶段 3：GREEN 验证（Dev Agent）
- **Dev Agent 动作**：在 `app/services/transfer.py` 中编写业务代码，引入 Redis 分布式锁与底层数据库唯一约束流水表。
- **运行测试**：执行 `pytest tests/domain/test_transfer.py`。
- **运行结果**：`PASSED (3 passed in 0.05s)`。
- **GREEN 验证通过**。

### 阶段 4：FALSIFICATION 反证（Test Agent / Arbitrator）
- **变异注入 1（副作用抹除）**：
  在 `transfer.py` 中注释掉写流水表代码：`# db.insert_idempotency_log(key, transfer.id)`。
  *重新运行测试* -> **FAILED**！测试精准报错：`AssertionError: Expected 1 transaction record, found 2`。变异体 1 被成功击杀！
- **变异注入 2（并发竞态破坏）**：
  在 `transfer.py` 中移除数据库悲观锁：将 `select_for_update()` 替换为普通的 `select()`。
  *重新运行测试* -> **FAILED**！并发用例下检测到余额不守恒，断言报错。变异体 2 被成功击杀！
- **反证通过**：变异体存活率为 0%，证明测试套件具备极高实战防御力。

### 阶段 5：终审放行（Arbitrator）
- Arbitrator 审查完整的 RED 日志、GREEN 日志与 Mutation 击杀报告，确认闭环，签名归档合并。
