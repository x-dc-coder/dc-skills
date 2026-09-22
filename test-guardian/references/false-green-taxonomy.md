# 测试假绿（False Green）与测试膨胀的代码级根因病理报告

> 分析对象：Words-Production（`/mnt/e/AllProjects202604/Words-Production`）、Soul-Spark（`/home/dc/projects/Soul-Spark`）
> 方法：全量测试清单 + 断言质量普查 + 分层门禁审计 + 两处活体复现（Soul-Spark 当场跑出 1 failed / 493 passed；Words-Production 分层计数）
> 证据等级：【实测】= 本次运行输出；【位置】= 代码 file:line；【推断】= 由证据推出的机制结论。全部【实测】命令见 §附录。

---

## 0. 摘要：这两套测试真正在测什么

| 指标 | Words-Production | Soul-Spark |
|---|---|---|
| tests/ 下 test_*.py | 123 个 | 36 个 |
| def test_ 函数 | 1801 个 | 444 个 |
| pytest 实收 | 1880 条 | 507 条（含 14 条默认去选） |
| monkeypatch | **634 处**【位置】 | **408 处** |
| MagicMock/AsyncMock/Mock( | **200 处** | 0 |
| mock.patch | 36 处 | 34 处 |
| pytest.skip / importorskip | 56 处（29 个文件 `importorskip("PySide6")`） | 11 条 skip（PG 依赖） |
| 单次实测结论 | 系统 python 收集：733 collected / **47 collection ERROR**【实测】 | **1 failed, 493 passed, 11 skipped, 14 deselected，74.92s**【实测】 |

**核心诊断（一句话）**：这两套测试的主体结构是 **"用 mock 复述实现"**，而不是 **"用事实否证假设"**。
mock 密度（Words：634 处 monkeypatch / 123 文件 ≈ 5.2 处/文件）已经超过了它们能提供的信号量——
被测对象被替换成替身之后，剩下的断言只剩两种：**"替身被调用了"**（测调用图）和 **"这段字符串在输出里"**（测文案）。
两者的共同点是：**产品坏掉时它们都不会红。**

本报告的第二部分证明该结构不是偶然的工程质量问题，而是 **Agent 增量开发范式的必然产物**；
第三部分证明它为什么不能靠"同一个 Agent 再写一遍测试"来发现。

---

## 1. 假绿（False Green）六大核心根因与代码模式

### 根因 1：过度 Mock 被测对象本体（Mocking the SUT）

**机制**：把被测函数**所依赖的、构成其语义的**协作者整体替换为 MagicMock，然后断言"替身被调用过"。
此时测试与产品之间只共享**函数名**，不共享**行为**。函数名改一次测试红一次，
函数体改错了测试永远绿。

**Bad（本项目实证，`tests/test_auto_export.py`）**：

```python
@pytest.fixture
def auto_env(tmp_path, monkeypatch):
    import thesis_exporter.pipeline as pipeline_mod
    fake_cfg = {"toc": {}, "styles": {}}
    # 连配置加载器都换掉 —— 配置 schema 出错永远不会被发现
    monkeypatch.setattr(pipeline_mod, "load_style_config", lambda p: fake_cfg)

    m_portable   = MagicMock(name="run_portable_export")
    m_com_extra  = MagicMock(name="run_com_extra")
    m_full       = MagicMock(name="run_export_pipeline")
    # ↓ 被测函数的三个真正干活的实现，全部换成替身
    monkeypatch.setattr(pipeline_mod, "run_portable_export", m_portable)
    monkeypatch.setattr(pipeline_mod, "run_com_extra",       m_com_extra)
    monkeypatch.setattr(pipeline_mod, "run_export_pipeline", m_full)
    return {...}

def test_linux_mode_calls_portable_only(self, tmp_path, auto_env, monkeypatch):
    result = pipeline_mod.run_auto_export(md, out, auto_env["cfg_path"])
    auto_env["portable"].assert_called_once()   # ← 断言主体是 mock
    auto_env["com_extra"].assert_not_called()
    assert result["mode"] == "linux"
```

这条测试在“导出功能被彻底删空”时**依然全绿**——因为它验证的只是"分支会调用哪个名字"。
它唯一能抓住的是重命名重构，而重命名重构恰恰是不需要测试保护的那类改动。

**Good（本项目已有的正确形态，`tests/helpers.py` 的双闸门）**：

```python
def assert_docx_conforms(docx_path, expectations) -> "AssertResult":
    """双闸门验证（verifier + structural_checks）——直接检查真实产物的 XML 事实。"""
    # 闸门 1：复用产品自身的 verifier（FormatVerifier）
    # 闸门 2：对 docx 包内 word/document.xml 做 lxml XPath 结构断言
    #         xpath / attr / equals / contains / tolerance
```
配合 `tests/regression/`（57 条）与 `tests/end_to_end/`（34 条）用 **真实 Word COM 导出真 docx** 再断言
`expectations.yaml`。这才是"用事实否证假设"。**但这 129 条恰好在 CI 里永不执行**（见 §2.3）。

**判别式**：把测试里所有 `mock.assert_called*` 换成 `pass`，如果测试仍然表达了产品行为 → 合格；
如果测试立刻空洞化 → 它测的是调用图。本项目粗筛：`MagicMock|Mock(` 共 200 处，其中
`assert_called` 类断言是主要形态；`monkeypatch.setattr(com_bridge, ...)` 单模块 60 处、
`monkeypatch.setattr(wc, ...)` 41 处、`pipeline_mod` 28 处——**mock 的正是被断言的那条链路上的实体**。

---

### 根因 2：表面化断言 / 命中静态布局或同名导航（UI layout collision）

**机制**：断言用"页面上存在某段文字"来代表"页面功能正常"。而 SPA 的**顶栏导航文案在每条路由上都渲染**，
导航项的名字与页面标题、以及测试期望的 marker **逐字相同**。于是"页面正文全烂"与"页面完好"在断言看来完全一致。

这不是理论风险，Soul-Spark 有**实测过的现场**（commit `33aa8b4` 提交信息 + 代码 diff）：

**Bad（`33aa8b4^` 的 `tests/browser/test_spa_smoke.py`）**：

```python
# 各页面骨架渲染后必然出现的稳定文案（来自各自的 PageHeader）
ROUTE_MARKERS = [
    ("/todos", "待办"),
    ("/inbox", "速记"),
    ("/calendar", "日历"),
]

@pytest.mark.parametrize("path,marker", ROUTE_MARKERS)
def test_key_routes_direct_access(app_page, app_server, path, marker):
    _goto(app_page, app_server, path)
    body = app_page.inner_text("body")
    assert marker in body, f"{path} 未渲染出「{marker}」"
```

提交信息原文（作者自述，可作为独立评审有效性的直接证据）：

> 评审实测：把我新写的 test_key_routes_direct_access 对应页面的 /api/** 全部 abort 后，
> **三条仍然全部通过**——因为断言的 marker（待办/日历/速记）与 App.tsx navMain 的
> **顶栏导航文案逐字相同**，页面正文全烂也照样命中。**等价于"顶栏渲染了"。**

**Good（`33aa8b4` 修复后：接口造数据 → 断言只有该页面才渲染的东西，并补反证用例）**：

```python
def test_todos_page_renders_server_data(app_page, app_server):
    title = "冒烟-待办页渲染"
    _create_todo(app_page, app_server, title)          # ① 用 API 造数据
    _goto(app_page, app_server, "/todos")
    app_page.wait_for_function(                           # ② 断言该数据必须出现
        "t => document.body.innerText.includes(t)", arg=title, timeout=20000)

def test_calendar_page_renders_calendar_content(app_page, app_server):
    """用小时刻度而不是导航文案做断言——导航文案每页都有，等于没测。"""
    app_page.wait_for_function(
        r"() => /\b(09|10|11|12):00\b/.test(document.body.innerText)", timeout=20000)

def test_page_assertions_are_data_backed(app_page, app_server):
    """【反证用例】把 API 全 abort 后，数据断言必须失败 —— 给"测试本身"的测试。"""
    app_page.route("**/api/**", lambda route: route.abort())
    assert title not in body          # 数据必须随 API 一起消失
    assert "待办" in body              # 但顶栏还在（证明是 API 挂了，不是 SPA 挂了）
```

最后那条 **falsification test（反证用例）** 是整个仓库最有价值的测试模式：它把"断言不得退化为文案断言"
变成了**可执行契约**。任何一次退化都会立刻变红。

**同族模式（同样在 33aa8b4 被修）**：用 `console error` 当"无 JS 异常"的证据
（把"合法但失败的请求"也算进来 → 误报面大），改为只收 `pageerror`（未捕获异常）。

**判别式**：把被测页面的 API 全部 abort，测试是否变红？不变红就是假绿。
这条判据可以机械化，本项目已经机械化了一个实例。

---

### 根因 3：状态码 200 假就绪 / 未校验载荷（Readiness vs Liveness）

**机制**：把"进程活着"当作"依赖健康"。HTTP 200 是 liveness 的信号，
而 readiness 必须看**载荷里依赖项的状态**。二者混淆后，"DB 已挂"与"一切正常"在断言看来是同一种颜色。

**Bad（Soul-Spark 生产代码本体，`app/main.py:110-144`，当前 HEAD 仍然如此）**：

```python
@app.get("/api/health")
def health():
    db_ok = True
    outbox_pending = -1
    outbox_failed  = -1
    try:
        db = _g()
        db.conn.execute("SELECT 1").fetchone()
        ...
    except Exception:
        db_ok = False        # ← 异常被吞成"一个字段变 False"，没有日志、没有原因、没有 traceback
    ...
    return {"ok": True, "app": "soul-spark", ...   # ← ok 硬编码为 True，与 db_ok 完全解耦
            "db": db_ok,
            "outbox_pending": outbox_pending,       # DB 挂了时是 -1，调用方无从判断
            "rate_limit_active": rate_active}
```

两个独立缺陷叠在一起：
1. **`"ok": True` 是字面量**——接口永远宣告自己健康，哪怕 `db: false`；
2. **`except Exception` 静默**——`db_ok=False` 之外不留任何可归因信息（根因 4）。

消费方据此产生的假绿（提交信息原文）：

> /api/health 在 DB 不可用时仍返回 200（main.py 把异常吞成 ok:True），
> 原就绪判定只查 200 → **DB 坏了会误判就绪**，随后报一堆看不懂的错。

**注意修复方式**：修复落在**测试侧**（`tests/browser/conftest.py`），产品缺陷被绕过而非修掉：

```python
    # tests/browser/conftest.py:118-121（现状）
    health = _probe_health(base)
    # readiness 而非 liveness：/api/health 在 DB 不可用时仍返回 200，
    # 必须同时确认 db_ok，否则 DB 坏了会被误判为"就绪"。
    if health and health.get("db") is True:
        break
```

这是典型的 **"测试替产品打补丁"**：契约被写进了测试，产品仍然在撒谎。
下一个消费者（k8s probe / 部署脚本 / 监控）只要读 `ok` 就会踩同一个坑。

**Good（本项目的正解形态）**：

```python
@app.get("/api/health")
def health():
    run = health_probe()          # 返回结构化 HealthReport，每项带 ok/reason/elapsed_ms
    code = 200 if run.ready else 503      # readiness 失败必须是非 2xx，否则 probe 永远认为就绪
    return JSONResponse(status_code=code, content=run.model_dump())

# 且每条依赖独立 try/except + log.warning(exc_info=True)，
# 不允许出现"吞掉异常只改一个布尔"的写法。
```

**同时保留的 Good（Words-Production）**：`tests/conftest.py` 的 WP-09 门禁明确把
"整层静默 skip 但 exit 0" 判为失败——这是同一根因在**测试侧**的正确解法（详见 §2.3）。

**判别式**：`assert r.status_code == 200` 后面必须紧跟对**载荷语义**的断言。
Soul-Spark 全仓 `assert .*status_code == 200` 类断言 207 处，其中相当比例止步于状态码。
反例（写得好的那条）：`tests/test_core.py:23-27` 检查了 `outbox_pending / rate_limit_active / last_reconcile_at` 三个字段存在——
但它**只断言字段存在，不断言字段值**，所以 `db` 挂了、`outbox_pending=-1` 它也绿。

---

### 根因 4：异常静默吞没（Silent exception swallowing）

**机制**：`except Exception: pass` 把"依赖故障"降级为"这一行没执行"。
调用方看到的是一次**成功但结果为空**的调用——语义上等价于"数据本来就没有"。
测试若只断言"没抛异常 / 返回空 / 200"，就会把故障判为正常。

**规模【实测】**：
- Soul-Spark `app/` 下 `except Exception` 后接 `pass/continue/return None` 的站点 **40+ 处**，
  分布：`api/todos.py`(3)、`api/notes.py`(5)、`storage/db.py`(3)、`storage/audit_log.py`(2)、
  `sync/feishu.py`(3)、`feishu_bot/pending.py`(5)、`feishu_bot/cards.py`(3)、`feishu_bot/location.py` 等。
- Words-Production `except Exception` 共 **647 处**（注：本项目多数有日志，纯 `pass` 形态为 0 次字面命中，
  但其**断言侧**同样不校验"是否发生过异常"）。

**Bad（Soul-Spark 的"两条并行入口"事故，本仓库最贵的一次假绿，见 commit `5d2293f` 自述）**：

> grep -rn "on_ws_event" tests/ 曾**零命中**：生产走长连接，测试全走调试 HTTP 入口，
> 两条入口各写了一遍分发逻辑。这不是理论风险，已经漂移出线上故障：
> `app/sync/feishu.py::_on_im_message` 对所有 msg_type 一律 `json.loads(content)["text"]`，
> 而位置消息的 content 是 `{"latitude":..}`（无 text 键）→ 被清空成空串 →
> **生产端位置打卡 100% 回「⚠️ 无法解析位置信息」**。HTTP 调试入口对 location 原样透传，
> 所以 **CI 长期全绿**。

这是"静默吞没"的**最高阶形态**：不是 `except: pass`，而是 **异常被"空值替换"吸收**。
`json.loads(content)["text"]` 抛 KeyError → 被上层 except 吞成 `""` → 业务层收到空串 →
走"无法解析"分支 → 全程没有任何异常浮出 → 测试断言"该消息被处理了"仍然成立。

**Bad（测试侧同族，Soul-Spark `tests/test_m3_stage.py:70-75`）**：

```python
def _wait_push(captured, count=1):
    for _ in range(150):
        if len(captured) >= count:
            return True
        time.sleep(0.02)
    return False                     # ← 超时返回 False（不抛、不 fail）
# 调用点只有一个做了检查：
# tests/test_m3_stage.py:169:  assert _wait_push(captured, 1), "解锁推送未发生"
```
本次统计该 helper 在仓库内**只有 1 个调用点**且它做了检查，所以当前没有实际假绿。
但这正是**必须靠纪律维持**的形态——helper 的默认契约是"失败时安静地返回 False"，
任何一次新调用忘了断言，就新增一条静默假绿。

**Good**：

```python
class _WaitTimeout(AssertionError): ...

def wait_until(pred, *, timeout=3.0, what="条件"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred(): return
        time.sleep(0.02)
    raise _WaitTimeout(f"等待超时：{what}")    # 超时即失败，没有"安静返回 False"的形态
```
**判据**：任何返回 `bool` 的"等待"helper 都是假绿种子。等待函数必须**在超时时失败**。

**Good（产品侧）**：`app/feishu_bot/idempotent.py:64` 保留了一条历史注释：
`# 曾经这里 except Exception: pass → 落到下面的查询 → 行不存在 →`
——把"这里曾经吞掉异常"写进代码，是防止复发的有效手段。

---

### 根因 5：环境变量与全局单例状态泄露（.env / global singleton）

**机制**：测试进程与生产进程共享**同一份可写全局态**：`.env`、模块级单例、进程 TZ、
后台线程、真实数据库文件。于是：
- 本地有 `.env`、CI 没有 → **本地绿 / CI 红**（或反之）的通道被打开；
- 最坏情形 `.env` 里的 `DATABASE_URL` 指向**真实 PG**，测试直接写生产库；
- 单例在上一条用例里被污染，下一条用例"莫名其妙"地过或不过。

**Bad（`33aa8b4^` 的 `tests/browser/conftest.py`，作者自述）**：

> 评审实测子进程里 settings.web_token 取到了 .env 的值（config.py 默认是 dev-token），
> 即 fixture 只中立化了 6 个键，**其余 .env 键照旧生效** → 本地有 .env、CI 没有，
> 存在"本地绿 CI 红"通道；最坏情形是 .env 里 DATABASE_URL 指向真实 PG。

根因在于产品侧的配置模型本身就允许这件事发生——`app/config.py:74-77,100`：

```python
class Settings(BaseSettings):
    class Config:
        env_file = ".env"          # ← 相对 cwd 解析：测试进程的 cwd 就是仓库根
        env_file_encoding = "utf-8"
        extra = "ignore"
settings = Settings()              # ← 模块级单例，import 即固化
```

**Good（`33aa8b4` 的修法，三重隔离，且**用事实证明**而非相信配置）**：

```python
work_dir = tmp_path_factory.mktemp("browser-work")   # ① 把 cwd 指向临时目录 → 读不到仓库 .env
env = {                                              # ② 白名单式 env：不继承宿主任何键
    "PATH": os.environ.get("PATH", ""), "HOME": ..., "PYTHONPATH": str(REPO),
    "DATA_DIR": str(data_dir),
    "DATABASE_URL": "",        # 显式清空，防止指向真实数据库
    "FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "",    # 清空凭据 → 强制 mock，绝不联网
    "BOT_LLM_ENABLED": "false", "ZHIPU_API_KEY": "",
}
repo_db_before = _digest(REPO_DB)
...
finally:
    after = _digest(REPO_DB)                          # ③ 会话结束自检
    assert after == repo_db_before, f"隔离失效！仓库 data/index.db 摘要变化：{repo_db_before} → {after}"
```

第 ③ 条是**范式级**的：**不要断言"配置正确"，要断言"外部世界没有被修改"**。

**Soul-Spark 的现状（同根因，未根治）**：
- `monkeypatch.setattr(settings, ...)` **187 处**——测试靠逐属性打补丁来模拟配置，
  而不是靠一个"测试专用 Settings 装配点"；
- **18 个测试文件各自复制**同一段单例复位（`db_mod._db = None` 33 处、
  `feishu_mod._provider = None` 18 处、`limiter._records` 亦需手动清），
  全仓单例复位写点 **52 处**；
- **18 个测试文件有一个逐字重复的 `client` fixture**（`def client(monkeypatch, tmp_path)`），
  每份都手工列出要中立化的 `settings` 字段。**任何新增的全局态都必须被复制 18 次才安全**——
  漏掉一处就是一条不可复现的偶发假绿；
- conftest 的 autouse 夹具（`tests/conftest.py`）承担了两项"环境矫正"：
  `os.environ["TZ"]="Asia/Shanghai"`（在导入业务模块**之前**）与强制 `bot_llm_enabled=False`。
  后者意味着**默认测试环境与生产环境在 LLM 路径上行为不同**——该路径的真实行为只在
  `test_llm_intent.py` 里被验证，其余 35 个文件看到的都是另一个产品。

**Good（范式）**：把配置收敛为**显式装配**，让测试构造自己的实例，而不是给全局单例打补丁：

```python
# app/config.py
def build_settings() -> Settings: ...
settings = build_settings()                 # 唯一的模块级单例，只为生产入口服务

# app/main.py
def create_app(settings: Settings | None = None) -> FastAPI:   # 依赖注入
    cfg = settings or build_settings()

# tests/conftest.py —— 一处装配，全仓复用
@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(build_settings(data_dir=tmp_path, db_backend="sqlite"))) as c:
        yield c
```
判据：**测试里出现的 `monkeypatch.setattr(settings, ...)` 次数应趋近于 0**。

---

### 根因 6：异步 / 队列竞争未等待与伪幂等（Outbox / worker race）

**机制**：三方竞争——生产写入、后台 worker、外部系统——的时序不由测试控制。
于是测试用两种坏办法"对齐"：`sleep` 等待（慢且仍可能 flaky），
或者**直接把后台 worker 关掉**（快且永远绿）。后者是更严重的问题：
**被关掉的那条链路，从此再也没有测试保护。**

**Bad A：把 worker 关掉（Soul-Spark，18 个文件）【实测】**：

```python
@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", Path(tmp_path))
    monkeypatch.setattr(settings, "outbox_interval", 3600)   # ← 18 个文件逐字重复这一行
    ...
```
配合 worker 实现（`app/sync/outbox.py:303-311`）：

```python
def _loop():
    while not self._stop.is_set():
        try:
            drain()                       # 启动时先抽一次（此时队列是空的）
        except Exception as e:
            log.exception("outbox drain error: %s", e)     # ← 兜住一切，线程永不死
        self._stop.wait(self._interval)   # 然后睡 3600s
```
**时序真相**：worker 在 `TestClient` 的 lifespan 里启动 → 立刻 drain 一次**空队列** → 睡 3600s。
测试随后才 `enqueue`，那条记录**永远不会被 drain**。
于是这些用例验证的只是"记录被写进了 pending 表"，而
**"pending → 调飞书 → sent / 重试 / 退避 / 置 failed 死信"整条状态机在默认套件里零覆盖**。

**Bad B：绕开真实出站调用，直接断言载荷构造函数**（`tests/test_full_chain_t16.py:85-89`）：

```python
# 验证 outbox 日历事件 payload
ev = _build_event(target_todo_fm, todo_id)      # ← 直接调纯函数，不经过 outbox / provider
assert "location" in ev
assert "深圳科兴科学园 B 栋" in ev["location"]["name"]
```
这条测试标着"全链路 A：飞书原生位置消息 → … → 出箱日历事件带地点"，
但它**从未经过出箱队列**。它证明的是"`_build_event` 是个纯函数且能带 location"，
不能证明"这条记录会被发出去、且只发一次"。

**Bad C：伪幂等**。`app/storage/repositories/outbox.py` 的 drain 选择查询
（`app/sync/outbox.py:198-210`）是**无锁的**：

```python
stmt = select(Outbox).where(
    Outbox.status == "pending",
    (Outbox.next_attempt_at.is_(None)) | (Outbox.next_attempt_at <= now),
).order_by(Outbox.id.asc()).limit(max_items)
```
没有 `FOR UPDATE SKIP LOCKED`，也没有先 CAS 置 `status='processing'`。
单 worker 下侥幸正确；**多进程/多副本部署时同一条记录会被并发执行两次**。
"幂等"完全押在飞书侧 `idempotency_key` 上（`app/sync/outbox.py:36-45`），
而该键的注释本身就记录了这条防线曾经破过：

> ⚠️ 飞书幂等键是「应用维度」全局唯一的……换到日历 B 复用同键会报 193003 event is deleted。
> 因此键中混入目标日历指纹

**Good（正确形态）**：

```python
# ① 产品侧：claim 语义 + 租约，把"幂等"变成数据库事实而不是外部系统的恩赐
stmt = (select(Outbox).where(Outbox.status == "pending", ...)
        .order_by(Outbox.id).limit(n).with_for_update(skip_locked=True))
# 处理器执行前先 CAS：UPDATE outbox SET status='processing', lease_until=now+60
#   WHERE id=:id AND status='pending'  →  rowcount==1 才允许执行

# ② 测试侧：不要关掉 worker，而是让时间可控
def test_outbox_enqueue_then_drain_marks_sent(client, fake_provider):
    enqueue("event.create", {"todo_id": tid})
    stats = drain()                       # 显式驱动，不用 sleep 也不关 worker
    assert stats["sent"] == 1
    assert repo.latest_status_for_todo(tid) == "sent"
    drain()                               # 再抽一次：必须不重复发送
    assert len(fake_provider.created_events) == 1     # 幂等被证明，而非被假设
```
本仓库**已经有**这个正确形态的种子——`tests/test_m1_stage.py:44`、`tests/test_m1_backend.py:38`、
`tests/test_llm_settings_and_todo_edit.py:225` 显式 `from app.sync.outbox import drain` 并直接调用。
**正确做法已经在仓库里，只是没有被推广到那 18 个文件。**

**Good（另一处）**：`tests/test_sync_reconcile.py:127-141` 用**有界轮询 + 断言**
验证周期对账（`interval=1` + 3s deadline + `assert calls[0] is True`），
而不是睡固定时长——这是可接受的形态。

**判别式**：`grep -c "outbox_interval", 3600` 与被测异步子系统的真实工作量应成反比。
关掉 worker 的测试文件数 = 该子系统未覆盖率的下界。

---

## 2. 测试膨胀与无序（Test Entropy）的根因

### 2.1 现场：测试名就是任务号

**Words-Production**（`tests/`，10 个文件 1186 行）【实测】：

| 文件 | 行数 | 名字来源 |
|---|---|---|
| `test_w1c_verifier.py` | 100 | "波1 W1-c" |
| `test_w2a2_protocol.py` | 131 | "波2 W2-a2" |
| `test_w3a_common.py` | 83 | "波3 W3-a" |
| `test_w3b.py` | 97 | "波3 W3-b" |
| `test_w4b_extends.py` / `test_w4c_help.py` | 171 / 30 | "波4 W4-b/c" |
| `test_s2.py` / `test_s3.py` | 155 / 31 | "S2/S3" |
| `test_stage1_regressions.py` / `test_stage3_config.py` | 215 / 173 | "阶段1/3" |

编号的**唯一事实源是任务清单**，已被文档反向确认【位置】：
`docs/issues/com-service-待办-2026-08-14.md`：`| 心跳默认 2.0s 过紧 → 8.0s（resident.py） | ✅ W3-b 已落地 |`；
`docs/退出码约定.md`：`唯一事实来源：shared/exit_codes.py（2026-08-14 波1 W1-a 落地）`。

**Soul-Spark**（36 个文件，17 个 = 47% 带任务编号）【实测】：
`test_m1_backend.py` `test_m1_stage.py` `test_m2_backend.py` `test_m2_stage.py`
`test_m3_backend.py` `test_m3_stage.py` `test_orm_t2.py` `test_alembic_t3.py`
`test_migration_verify_t4.py` `test_attachments_t6.py` `test_location_t7.py`
`test_cumulative_t9.py` `test_push_cumulative_t10.py` `test_conversation_t11.py`
`test_bot_inplace_t12.py` `test_full_chain_t16.py` `test_repair_round2.py`

**命名只是症状，真正的病灶写在 docstring 里**（`tests/test_m3_stage.py:1`）：

> M3 阶段测试补充（t13，**覆盖 test_m3_backend.py 未覆盖的验收点**）

这句话说明测试的**切分维度是"任务/文件"，不是"行为/契约"**。
于是同一个行为被拆到两处，且**没有任何一个文件是它的归属地**——
后续任何人修改该行为，都无法知道该跑哪一个（或哪几个）测试。

### 2.2 为什么 Agent 增量开发**必然**走向这种碎片化

这不是纪律问题，是四条结构性力量的合力：

**① 任务的原子性 ≠ 行为的原子性。**
Agent 的输入单位是"完成 W3-b"、`#42 长连接入口补测`、`t16 全链路`。
任务的边界由**排期**决定，而行为的边界由**领域模型**决定。二者不重合时，
测试只能按任务切片——因为 Agent 在**任务结束时必须产出一个可提交的废物**：
"给这个任务写测试"最省力的落点就是 `test_w3b.py`。
**测试文件的粒度 = 交付批次的粒度。**

**② "补充未覆盖的验收点"是低成本的收敛条件，而"重构既有测试"是高成本且违反任务边界的行为。**
Agent 的任务描述里几乎从不包含"允许改写 `test_m3_backend.py`"。
在 `inScope/outOfScope` 约束下（本仓库的 agent-teams 规程明确要求声明 inScope），
**新增文件是唯一不越界的选择**。于是"补覆盖"永远以**新文件**的形式发生，
测试数单调增长，**没有任何一步做减法**。

**③ 测试的搜索成本被转嫁给未来的 Agent。**
碎片化的代价不是立即显现的：写下 `test_m3_stage.py` 的那一刻，成本是 0，
收益是"本任务验收通过"。成本的支付者是"下一个想删掉 `test_m3_stage.py` 的人"——
他必须证明它覆盖的行为在别处已有。**没有任何一个 Agent 有动机支付这笔成本。**
结果：`test_format_verifier.py` 长到 **2066 行**、`test_pipeline.py` 1296 行、
`tests/helpers.py` 1220 行，而 123 个文件里散着 10 个"不知道谁该负责"的波次文件。

**④ 缺少"行为 → 测试"的索引，导致只有新增是安全的。**
正确的收敛需要一张"契约 → 用例"的映射表。本仓库有它的**雏形**
（`config/coverage_matrix.yaml` + `tools/check_coverage_matrix.py`，
校验"声明的 handler 模块/函数在代码中实际存在"），
但它校验的是 **"handler 是否存在"**，不是 **"行为是否被哪条测试守住"**。
缺了这层索引，Agent 面对"这段逻辑有没有被测"只能靠 `grep` 猜——
**猜的结论必然是"没有"，于是再写一个。**

### 2.3 膨胀的代价被"分层门禁"放大成结构性盲区

测试数量增长会带来第二个后果：**为了让它跑得动，必须分层；分层之后，没被选中的层就死了。**

Words-Production 的分层【实测，按 marker 收集】：

| 层 | 用例数 | CI（`bash scripts/ci_linux.sh`）是否执行 |
|---|---|---|
| `unit` | 1729 | ✅ |
| `integration` | 28 | ✅ |
| `regression`（真实 Word COM 导出） | 57 | ❌ **永不执行** |
| `end_to_end`（真实论文完整导出） | 34 | ❌ **永不执行** |
| `gui_regression`（PySide6 + Word） | 32 | ❌ 且 29 个文件 `importorskip("PySide6")` |
| `word_verify` | 2 | ❌ **永不执行** |
| `perf_regression` | 4 | ❌ |

CI 入口只跑 `-m "unit or integration"`（1743/1880）。
**恰好是最接近真实产品的 129 条（真 Word、真 docx、真 GUI）在 CI 中结构性缺席**，
而 workbench 的 33 个 GUI 文件在无 PySide6 环境整文件 skip。
于是"CI 绿"这句话的真实含义是：**"在 Linux 上，用 mock 复述实现的那些断言通过了。"**

这个仓库**已经识别出这个陷阱**，并且写出了本报告见过的最强护栏（`tests/conftest.py` 的 WP-09 门禁），
注释本身就是一份假绿病理学教科书：

> 假绿机制：L2 word_verify 与 L4 end_to_end 的守卫都要求 WSL 宿主
> （`tests.wsl_bridge_env.bridged_env()` 在非 WSL 返回 None）。在 Windows 宿主上
> 跑这两层 ⇒ 整层静默 skip（2 skipped / 3 passed + 27 skipped）且 **exit 0**：
> **什么都没验证，门禁却是绿的。**

```python
GATED_LAYERS = ("word_verify", "end_to_end")      # 只覆盖这两层，避免误伤 unit 的合法 skip
def pytest_sessionfinish(session, exitstatus):
    if not _is_wsl_host():
        problems.append(f"{layer} 必须在 WSL 宿主驱动（当前宿主非 WSL）；"
                        "非 WSL 宿主下守卫会整层 skip 却 exit 0")
    for layer in sorted(_gated_layers_run):
        if (n := len(_gated_skipped.get(layer, ()))):
            problems.append(f"{layer}: skipped={n}（WP-09 硬规则：该层 skipped>0 视为失败）")
    if problems and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED   # ← 把"绿"强行改成"红"
```

**这是可直接移植的黄金模式**：*当一个测试层无法在其声明的宿主上完整执行时，
"跳过"必须等价于"失败"。* 任何 `exit 0 + 大量 skip` 的组合都是假绿。

**并被同一模式抓出了第二类实例**（`tests/workbench/test_export_worker.py` 顶部注释）：

> 2026-08-28 补 pytestmark=unit：此前**无 marker**，`-m unit` / `-m "unit or integration"`
> 均 deselected → **Linux CI 从不执行**（B1 竞态修复无 CI 保护）。

即：**测试存在 ≠ 测试执行**。"被 deselected"与"被删掉"在信号量上完全等价，
而它在测试清单里却是存在的——这是最隐蔽的膨胀：**膨胀本身制造了"已经覆盖"的错觉。**

另一个同族脆性：用系统 python 收集本仓库，得 **733 collected / 47 collection ERROR / Interrupted**【实测】。
`scripts/ci_linux.sh` 已用"优先 .venv"处理了这一点并写明了原因——
说明作者清楚这类"收集期环境依赖"，但它是**逐个踩出来的**，不是结构性保证的。

### 2.4 熵的量化指标（建议纳入门禁）

| 指标 | 当前值 | 建议阈值 |
|---|---|---|
| 单测试文件行数 max | 2066（`test_format_verifier.py`） | ≤ 400 |
| 含任务编号的测试文件占比 | Words 8%；Soul **47%** | 0%（编号只应出现在 issue/PR，不应出现在测试名） |
| mock 密度（monkeypatch / 文件） | Words **5.2** | 下降趋势即目标 |
| `mock.assert_called*` 断言数 / MagicMock 数 | 200 MagicMock | 逐条复核 |
| 复制粘贴的 fixture 数 | Soul `client` **18 份** | 1 |
| 手写单例复位点 | Soul **52 处** | 0（改为工厂 + 依赖注入） |
| 关闭异步 worker 的文件数 | Soul **18** | 0（改为显式驱动 drain） |
| "skip 但 exit 0"的层 | Words 已用 WP-09 消灭 | 必须是 0 |

---

## 3. "自己做题自己批改"的认知回音壁

### 3.1 机制：为什么同一个 Agent 写的测试没有否证力

测试的全部价值来自**否证的意愿**。而意愿来自**"我认为我的实现可能错"**这个前提。
同一个 Agent 在写完实现后写测试时，前提已经变成 **"我的实现是对的，我来给它盖章"**。
这不是态度问题，是**信息状态**问题：Agent 已经持有"实现为什么这样写"的完整内部理由，
它读代码时的默认解释是**"这里是对的"**。

四个可观测的后果，全部在本两仓库中找到实例：

**后果 1：测试被改成适应实现，而不是实现被改成适应需求。**
这是回音壁最直白的指纹——**用测试去证明实现的当前行为就是正确行为**。

`tests/test_w1c_verifier.py`（Words-Production，作者留下的自白）：

```python
def test_second_heading_majority_wins(self, tmp_path, sample_config_path) -> None:
    """两个章节标题：14pt 与 16pt 各一 → 无众数时取首个？"""
    # 说明：两个样本时 Counter.most_common 取先出现的 14 → 与配置 16 不符 → fail。
    # 本用例改为 3 个标题（14/16/16 → 众数 16 → pass），验证众数聚合生效。
```

原用例断言"两个样本、配置期望 16 → 应 pass"，它红了。正确反应是
**"两个样本时的取众数策略是否合理？"**（回答会牵出"无众数时该如何裁决"这个真实的产品决策）；
实际反应是**把夹具从 2 个标题改成 3 个标题**，让实现通过。
**测试从此不再问那个问题**——回音壁合拢。

**后果 2：判定口径被反向固化（放宽判定 → 真阳性一起放过）。**
`tests/test_w1c_verifier.py` 的同一个文件把 verifier 从"首样本判定"改成"众数判定"，
并锁死为回归：

```python
def test_first_para_outlier_does_not_false_positive(self, tmp_path, sample_config_path):
    """首段 14pt、其余 12pt，配置期望 12 → 众数判定通过（原首样本逻辑误报 fail）。"""
    assert size_checks[0].status == "pass"
```

修假阳性（false positive）的方向永远是**降低判定强度**，而降低强度会等量引入假阴性
（false negative）——一篇**正文确实用错字号**的文档，只要绝大多数段落"看起来对"就会 pass。
众数采样本身是合理工程选择，但**风险在于这次放宽是被"测试通过"驱动的，
而不是被"我们接受损失哪一类漏检"的显式决策驱动的**。
回音壁的标志：**没有一条测试记录"我们因此放弃了什么"。**

**后果 3：守卫测试与它要守的东西失联（本报告最强的一条实证）。**

Soul-Spark 的时区专题（`tests/test_time_basis.py`）设计了三层保护，docstring 写得很清楚：
应用层统一 `timeutil`、测试进程钉 TZ、边界时刻冻结。其中**源码级护栏**是：

```python
def test_app_has_no_naive_clock_call_sites():
    """源码级护栏：app/ 下不得再出现 naive 的 date.today() / datetime.now()。"""
    ...
    for lineno, line in enumerate(path.read_text(...).splitlines(), 1):
        code = line.split("#", 1)[0]
        if "date.today()" in code or "datetime.now()" in code:      # ← 字面量匹配
            offenders.append(...)
    assert offenders == []
```

本次实测【实测】：

```
$ .venv/bin/python -m pytest tests/test_time_basis.py::test_app_has_no_naive_clock_call_sites -q
1 passed                                    ← 护栏是绿的

$ （同一进程内统计两种口径）
guard(字面匹配)命中： 0                       ← 护栏看到了 0 个违规
宽口径命中（datetime.now( / .today() / time.time()）： 48
   app/feishu_bot/intent.py:321: today = datetime.now(tz).date()      ← 真正读宿主时钟
   app/api/dashboard.py:29:      day = datetime.now(_tz()).date().isoformat()
   app/feishu_bot/actions.py:588: today = datetime.now(tz).date()
   app/feishu_bot/cards.py:834:  now_dt = datetime.now(tz)
   app/feishu_bot/idempotent.py:25 / recurrence.py:127 / vault.py:30 / sync/feishu.py:677-678 ...
```

护栏用**字符串包含**匹配 `"datetime.now()"`（**空括号**）。
只要代码写成 `datetime.now(tz)`——**带一个参数**——就一个字都匹配不到。
48 个宿主时钟读取点，护栏命中 0 个，**测试是绿的**。

而这个漏洞**正在制造红**（本次全量实测）：

```
$ .venv/bin/python -m pytest -q -m "not e2e and not browser"
FAILED tests/test_time_basis.py::test_bot_todo_relative_day_at_utc_midnight_boundary
1 failed, 493 passed, 11 skipped, 14 deselected, 3 warnings in 74.92s

E  AssertionError: ✅ 已建待办：明天交周报（截止 2026-09-19）
E  assert '截止 2026-09-12' in ...        # 冻结 2026-09-10T21:23Z（上海 09-11），明天应为 09-12
```

三层保护的真相：
- 第 1 层（`timeutil` 唯一入口）**是宣称**，`app/timeutil.py` 的 docstring 写着
  "**全项目唯一的『现在』入口**"，但 `intent.py:321` 自带了第二套时钟；
- 第 2 层（进程 TZ）只保证"naive 调用与 app 同基准"，**不能保证 naive 调用被冻结**；
- 第 3 层（边界冻结）`freeze()` 只 patch `timeutil.now`，
  **够不到 `datetime.now(tz)`**（`tests/test_time_basis.py:37-42`）：
  ```python
  def freeze(monkeypatch, instant):
      """冻结项目时钟（唯一注入点：app.timeutil.now）。"""
      monkeypatch.setattr(timeutil, "now", lambda: instant.astimezone(timeutil.tz()))
  ```
  于是**声称要守的护栏（第 1 层）是绿的，真正在守的那条（第 3 层）是红的**。

**这正是回音壁的教科书形态**：
守卫测试的**作者**知道"要禁止宿主时钟"，于是写了一条**看起来在禁止**的断言
（匹配他脑子里那个写法 `datetime.now()`），并因为**它绿了**而认为保护已就位。
他没有**反向测试这条守卫**——"我故意写一个 `datetime.now(tz)`，护栏会不会红？"
（结果：不会）。**没有反证用例的守卫测试，只是关于守卫的乐观预期。**

对照 §1 根因 2 的修法：Soul-Spark 已在**浏览器测试**里为断言补了反证用例
（`test_page_assertions_are_data_backed`），**却唯独没有给这条源码护栏补**。
同一个仓库、同一个作者、同一个 commit 周期内，能力已被证明存在——
缺的不是能力，是**把"反驳自己"制度化的触发条件**。

**后果 4：验收证据由被验收方自选。**
`reports/repair-wp01-verifier-fp.md` 的结论行：

> **验收：`bash scripts/ci_linux.sh` exit 0（1355 passed / 2 skipped / 96 deselected）。**

这份报告的"根因分析"质量很高（差分证据、现场复现、明确列出未复现项）。
但注意它的**验收定义**：`ci_linux.sh` = 只跑 unit + integration。
而它修的正是 `format_extractor/verifier` ——**verifier 的最终用途是在真实 Word 产物的 L2/L3/L4 上判定**，
那些层在 CI 里从未执行。**"1355 passed"证明不了这次修复在真实文档上有效**，
它只证明"新写的单元测试按我预期地通过了"。
报告里甚至自己写明了这一点：
"`_check_section_pagination` 对封面文档始终执行后误报 → **真实 jingmao 产物未复现**"——
即**该缺陷是否在生产数据上存在，本次验收没有回答**。

### 3.2 反证：独立评审在同一天、同一仓库里立刻抓出了假绿

回音壁的**存在性与可解性**同时被一个自然实验证明。commit `33aa8b4` 的提交信息：

> 我请了一个**独立子代理**对本次 CI 分层决策做批判性评审，它用**反证实验和 CI 硬数据**
> 找出多处真实缺陷（我先前写的测试有不少是"**看着在测、其实没测**"）。

独立评审做了什么，而作者没做？

| 手法 | 作者 | 独立评审 |
|---|---|---|
| **反证实验**（把 /api/** 全 abort，看断言是否还绿） | ✗ | ✅ 立刻抓出 3 条无效断言的根因是**导航文案碰撞** |
| **实测环境变量的实际取值**（打印子进程里的 `settings.web_token`） | ✗（只中立化了 6 个键，以为够了） | ✅ 抓出 .env 全键泄露 |
| **读真实 payload 而非猜字段名** | ✗（只查 200） | ✅ 发现 `health.db`（"实测首次即为该字段名，已按真实 payload 修正"） |
| **质问断言为何可能静默失效** | ✗（`sum(nums) > 0`） | ✅ "一旦 AI 侧有数据，ops 再退回假 0 也照样能过" |
| **检查"该层是否真的在跑"** | ✗ | ✅ 抓出 nightly schedule 被整段删除、"文档承诺的定时是零实现" |

**五个手法没有一个需要更多领域知识，全部需要"立场"——即"我要证伪它"这个前提。**
这正是同一个 Agent 无法自我供给的那一项。

### 3.3 结构性结论：把"反对自己"变成流程的硬约束

由以上证据（尤其是后果 3：**作者能写出反证用例，却只写在了浏览器测试上，没写给源码护栏**），
可以得出本报告最强的实践结论：

> **测试的有效性不能由测试的作者判定，必须由一条与作者立场独立的、可执行的判据判定。**

可落地的三条硬约束（本两仓库已有全部前置条件）：

1. **每条守卫/契约测试必须自带反证用例（falsification test）。**
   形态固定：*故意注入一个违反契约的实现/输入，断言该守卫变红。*
   Words-Production 有 `python-docx`/lxml 的真实产物能力，
   Soul-Spark 有 `tests/browser/test_spa_smoke.py::test_page_assertions_are_data_backed` 的成熟范例。
   **没有反证用例的守卫测试，等价于没有守卫。**

2. **"跳过"必须等价于"失败"。** 直接复用 Words-Production 的 WP-09 模式
   （``GATED_LAYERS`` + `pytest_sessionfinish` 改 exit code）。
   Soul-Spark 应立即识别自己的同类：`11 skipped`（PG 层，可接受但需在报告中显式声明）
   与 `14 deselected`（e2e/browser，**必须**至少有一个 schedule/dispatch 入口执行，否则等于没有）。
   注意 Words-Production 的 CI 注释与 `33aa8b4` 的自述之间存在矛盾
   （`.github/workflows/ci.yml` 顶部写"**不要加每日定时**"，而该 commit 声称"补回 nightly schedule"），
   这类**文档与配置漂移**正是"门禁没人真跑"的温床。

3. **验收判据必须与被测层对齐，且由外部给定。**
   `ci_linux.sh exit 0` 不能作为"verifier 在真实 Word 产物上正确"的验收依据。
   判据应由任务的 `objective/acceptance` 字段给出**目标层命令**
   （例如 `bash scripts/wsl_test.sh tests/regression -m regression`），
   而不是让实现者挑选一条能绿的命令。

---

## 4. 结论：一句话病理链

```
任务粒度的交付物（"完成 W3-b"）
   └─→ 测试按任务切片（test_w3b.py），行为无归属，只增不减（熵）
        └─→ 为了让膨胀的套件跑得动，按层切分并只跑低层（unit）
             └─→ 最接近真实产品的层（真 Word / 真出站 / 真 GUI）在 CI 中缺席
                  └─→ 留下来的断言只能靠 mock 复述实现（magic 替身 + 文案匹配）
                       └─→ 假绿在 mock 密度最高的地方最多（Words：5.2 处/文件）
                            └─→ 写测试的 Agent 已持有"实现是对的"前提
                                 └─→ 守卫测试变成关于守卫的乐观预期（timeutil 护栏：48 违规命中 0）
                                      └─→ 只有**独立立场 + 反证实验**能打破（33aa8b4 实证）
```

**两仓库的分水岭不在能力，在制度**：
Words-Production 已经写出了 WP-09 假绿门禁与反证用例思想；
Soul-Spark 已经在 `33aa8b4` 里完成了完整的"独立评审 → 反证实验 → 修复 → 补反证用例"闭环。
**但两者都没有把这个闭环变成默认流程**——所以同一个假绿在别处继续活着
（`test_app_has_no_naive_clock_call_sites` 此刻是绿的，`intent.py:321` 此刻是真的在违反它）。

---

## 附录：本报告全部【实测】证据的可复现命令

```bash
# ── 规模普查 ──────────────────────────────────────────────
cd /mnt/e/AllProjects202604/Words-Production
find tests -name 'test_*.py' | wc -l                                   # 123
grep -rE '^\s*(async )?def test_' tests --include=*.py | wc -l         # 1801
grep -rE 'MagicMock|AsyncMock|Mock\(' tests --include=*.py | wc -l     # 200
grep -r 'monkeypatch' tests --include=*.py | wc -l                     # 634
grep -rn 'pytest.skip\|importorskip' tests --include=*.py | wc -l      # 56

# ── 分层计数：CI 实际执行哪一层 ───────────────────────────
cd /mnt/e/AllProjects202604/Words-Production
for m in unit integration word_verify regression end_to_end gui_regression perf_regression; do
  echo -n "$m -> "; .venv/bin/python -m pytest -m "$m" --collect-only -q 2>/dev/null | tail -1
done
# unit -> 1729/1880 ; integration -> 28 ; regression -> 57 ; end_to_end -> 34
# gui_regression -> 32 ; word_verify -> 2 ; perf_regression -> 4
cat scripts/ci_linux.sh | tail -5      # 只跑 -m "unit or integration"

# ── Soul-Spark 全量实测（本报告的核心现场） ───────────────
cd /home/dc/projects/Soul-Spark
.venv/bin/python -m pytest -q -m "not e2e and not browser"
# → 1 failed, 493 passed, 11 skipped, 14 deselected, 3 warnings in 74.92s
#    FAILED tests/test_time_basis.py::test_bot_todo_relative_day_at_utc_midnight_boundary
#    实际回复"截止 2026-09-19"，期望 2026-09-12

# ── 证明"守卫测试是绿的，但违规有 48 处" ─────────────────
cd /home/dc/projects/Soul-Spark
.venv/bin/python -m pytest tests/test_time_basis.py::test_app_has_no_naive_clock_call_sites -q
# → 1 passed
.venv/bin/python - <<'PY'
import pathlib, re
root = pathlib.Path("app")
lit = [f"{p}:{i}" for p in root.rglob("*.py") if p.name!="timeutil.py"
       for i,l in enumerate(p.read_text(encoding="utf-8").splitlines(),1)
       if "date.today()" in l.split("#",1)[0] or "datetime.now()" in l.split("#",1)[0]]
wide = [f"{p}:{i}: {l.strip()}" for p in root.rglob("*.py")
        for i,l in enumerate(p.read_text(encoding="utf-8").splitlines(),1)
        if re.search(r"datetime\.now\(|\.today\(\)|time\.time\(\)", l.split("#",1)[0])]
print("护栏字面匹配命中：", len(lit))     # 0
print("宽口径命中：", len(wide))          # 48
print(*wide[:6], sep="\n")
PY

# ── 证明 .env 会进被测进程 / 全局单例靠手工复位 ───────────
cd /home/dc/projects/Soul-Spark
grep -rn 'monkeypatch.setattr(settings' tests --include=*.py | wc -l     # 187
grep -rn 'def client(monkeypatch, tmp_path)' tests --include=*.py | wc -l # 18
grep -rn 'outbox_interval", 3600' tests --include=*.py | wc -l           # 18
grep -rn '_db = None\|_provider = None' tests --include=*.py | wc -l     # 51（+limiter = 52）
git show 33aa8b4              # 独立评审成果：反证用例 + 白名单 env + readyz 修正
git show 5d2293f --stat       # 长连接入口零覆盖 → 生产位置打卡 100% 失败
```
