# description 写法指南（B2/B3 + 1024 字符预算）

## 预算分配（总计 ≤1024 字符）

| 段 | 预算 | 内容 |
|---|---|---|
| 前 80 字符 | 80 | 能力关键词（CC listing 截断线内必须可读） |
| 中段 | ~600 | 能力范围 + 覆盖场景枚举（用户会说的原话，中英双语） |
| 末段 | ~340 | 边界与分工（"X 归 Y 技能""仅当…时使用"） |

## 写法对照

- ❌ "生成 ER 图"（只描述能力，无触发场景）
- ✅ "当用户需要生成 ER 图/实体关系图/E-R 图/表结构图时使用；输出 er-diagram.png 到
  thesis-output/；多实体关系图走 ers 类型"（能力 + 触发词 + 边界）
- ❌ "This skill does diagram generation"（陈述式，CC 官方不推荐）
- ✅ "Use when the user asks for ER/module/usecase/sequence diagrams or ASCII drafts"（祈使句）

## 硬约束（validate R3 自动检查）

- ≤1024 字符；无尖括号；无 `[TODO:]` 占位
- 触发句式启发：含"当用户…时"/"Use when"/"触发"之一（缺失仅 W 级）
- 兜底/通用技能必须含 B6 优先级声明（"仅当其他专用 skill 无法满足时使用"）

## 触发词预检

```bash
uv run python dc-skill-creator/scripts/check_triggers.py "<候选 description>"
```
Jaccard ≥0.30 退出码 1（必须改写或确认分工）；0.18-0.30 人工裁决。
