# dc-skill-creator 验收清单（提交前逐项）

```
[0] dogfooding：dc-skill-creator 自身通过 validate.py（退出 0）
[1] R1-R13 全部 E 级通过（validate.py 退出 0；--Werror 可选升级）
[2] check_triggers.py 的 ≥0.18 重叠已人工裁决（改词或写明分工/B6 优先级）
[3] agent-map.yaml 已登记（validate R8 通过）+ family-apply --apply 已跑
[4] skills-sync 物化成功 + --check 零漂移（含 families 一致性）
[5] skillctl lint 过检（venv/死链/环境体检）
[6] 新外部二进制已登记 docs/arch/ENVIRONMENT.md（R6）
[7] README.md 目录结构/环境分类表已更新（若新增技能或改变分类）
[8] docs/INDEX.md 已同步（若新增/移动文档）
[9] git 提交模板：
    feat(skills): 新增 <name> 技能（<一句话能力>）

    - 族群：<family|standalone>；环境分类：<A|B|C>；启用：<base|on_demand|extra/preset>
    - 触发词：<前 3 个关键词>（check_triggers 无高重叠）
    - 验证：validate.py / skillctl lint / skills-sync --check / pytest 全过
```
