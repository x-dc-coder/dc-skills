---
name: md-to-thesis-latex
description: >
  将用户撰写的 Markdown 格式本科毕业论文转换为符合电子科技大学中山学院（ZSC）
  LaTeX 模板规范的 .tex 文件并编译生成 PDF。当用户提到以下任何场景时触发：
  "把 md 转成 latex"、"markdown 转 tex"、"帮我生成论文 pdf"、"md 转 latex"、
  "论文转 latex"、"把 markdown 编译成 pdf"、"生成毕设 pdf"、"论文排版"、
  "把写好的 md 转成论文"，或者用户给出 md 文件要求生成论文时。
  即使只说了"帮我处理论文"或"转成 pdf"，也应该主动使用此 skill。
metadata:
  family: thesis
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Markdown 转 ZSC 毕业论文 LaTeX Skill

> **使用方式（渐进式披露）**：本文件是导航页——先读它执行工作流；具体要素（图片/表格/代码/公式/参考文献）转换时读
> `references/conversion-rules.md`；章节特殊处理与撰写规范读 `references/chapter-specs.md`。

## 1. 核心职责

将用户提供的 Markdown 格式论文内容（含图片引用、表格、代码块、公式等），转换为符合电子科技大学中山学院（ZSC）毕业论文 LaTeX 模板（`zsc-cs-latex-thesis`）规范的 `.tex` 文件，并编译生成最终 PDF。

**你必须仅参考 Markdown 中的内容，不要添加或扩展任何内容。** 你的工作是格式转换，不是内容创作。

## 2. 前置检查

开始转换前，按顺序确认：

1. **模板文件是否存在**（`zscthesis.cls`）：
   a. **`--template-dir` 参数**：用户指定则直接用其 `style/`、`logo/`、`bib/`
   b. **`$PROJECT_ROOT/style/`**：检查项目根目录
   c. **Gitee 克隆**：从 `https://gitee.com/yeyunxiaopan/zsc-cs-latex-thesis` 克隆 `style/`、`logo/`、`bib/` 到 `$PROJECT_ROOT/template/`。若 Gitee 不可达，提示"模板仓库不可达，请手动放置 `style/zscthesis.cls` 到项目目录"，**继续转换而非中断**。
2. **图片文件是否存在**：检查 Markdown 引用的所有 `img/xxx.png`（或 jpg/pdf）。缺失不中断，用占位符代替（`\fbox{\parbox{0.7\textwidth}{... [占位符: xxx.png] ...}}`），并在日志列出缺失清单。
3. **编码确认**：所有 `.tex` 文件以 **UTF-8 无 BOM** 保存。

## 3. 总体要求（2024 版新增）

| 要求 | 指标 |
|------|------|
| 撰写工具 | **必须使用 LaTeX** |
| 正文篇幅 | **不少于 30 页**，主要篇幅集中在第 3、4、5 章 |
| 查重率 | **不高于 25%** |
| 编译链 | `xelatex → bibtex → xelatex → xelatex` |
| 编码 | UTF-8 无 BOM |

**重要约束**：
- 论文题目确定后**不能再更改**（任务书下达后锁定）。
- 所有图建议**导出**而非截屏；同类型图缩放比例一致，图中文字大小一致。
- **不能随意加回车换行、空行或空格**，否则会导致格式错乱。
- 英文标题及英文关键字**首字母要大写**。
- 论文内容要侧重于阐述**系统特有的核心业务**，尽量不写所有系统都通用的内容（如登录、注册等）。

## 4. 目录结构与文件映射

```
thesis-output/latex/
├── main.tex                    # 主文件，引用各章节
├── tex/
│   ├── frontinfo.tex           # 封面信息（2024年起通常注释掉）
│   ├── declaration.tex         # 独创性声明
│   ├── abstract-ch.tex         # 中文摘要
│   ├── abstract-en.tex         # 英文摘要
│   ├── content.tex             # 目录（自动生成，通常不修改）
│   ├── chap-1.tex ~ chap-N.tex # 各章节
│   ├── reference.tex           # 参考文献
│   ├── acknowledgement.tex     # 致谢
│   └── appendix.tex            # 附录（无内容则注释）
├── img/                        # 图片目录
├── style/                      # 模板样式文件
├── logo/                       # 学校logo
└── bib/
    └── ref.bib                 # BibTeX 参考文献
```

## 5. 转换规则（速览）

**标题层级**：

| Markdown | LaTeX | 格式说明 |
|----------|-------|---------|
| `# 绪论` | `\chapter{绪论}` | 三号黑体，居中，自动编号"第X章" |
| `## 课题背景` | `\section{课题背景}` | 小三黑体，左对齐 |
| `### 子标题` | `\subsection{子标题}` | 四号黑体，左对齐 |
| `#### 小小节` | `\subsubsection{小小节}` | 小四黑体，左对齐 |

**注意**：Markdown 章节编号（如 `## 1.1 课题背景`）转 LaTeX 时**去掉数字编号**（LaTeX 自动编号）；第 1 章正文前必须包含 `\clearpage \setcounter{page}{1} \pagenumbering{arabic}`。

**图片/表格/测试用例表/代码块/公式/列表/文本格式/参考文献的完整转换规则 → `references/conversion-rules.md`**（含 `[H]` 浮动参数、`\toprule` booktabs、`\multicolumn` 测试表、代码语言环境映射表、`\label`/`\ref`/`\eqref` 引用规范、BibTeX 等）。

## 6. 各章节特殊处理与撰写规范

封面/摘要/目录/正文章节/参考文献/致谢的 LaTeX 模板，以及第 1-6 章内容撰写规范（字数、图表要求、UML/时序图、数据库设计、测试用例表格式）→ **`references/chapter-specs.md`**。main.tex 主文件模板也在该文件末尾。

## 7. 编译流程

必须执行以下编译链（共 3~4 次 xelatex）：

```bash
xelatex -interaction=nonstopmode main.tex
bibtex main                    # 如果使用 BibTeX
xelatex -interaction=nonstopmode main.tex
xelatex -interaction=nonstopmode main.tex  # 确保交叉引用正确
```

或使用 `latexmk`：`latexmk -xelatex -interaction=nonstopmode main.tex`

## 8. 质量检查清单

生成 PDF 后，逐项检查：

### 8.1 格式检查
- [ ] 页边距：上2.5cm、下2.5cm、左2.5cm、右2cm
- [ ] 字号：章标题三号黑体，节标题小三黑体，正文小四
- [ ] 图编号格式：`图1-1`、`图2-3`（章号-序号）
- [ ] 表编号格式：`表1-1`、`表2-3`
- [ ] 图表标题：宋体小五号，编号与标题间用空格（无冒号）
- [ ] 所有图片/表格/参考文献在正文中用 `\ref` / `\cite` 引用，无"如下图"字样
- [ ] 公式编号格式：`(1-1)`、`(2-3)`，用 `\eqref` 引用
- [ ] 页眉显示"电子科技大学中山学院毕业设计(论文)"和当前章标题
- [ ] 页码：摘要用大写罗马数字，正文用阿拉伯数字
- [ ] 目录、图目录、表目录正确生成；代码高亮环境正确渲染
- [ ] 无编译错误和警告（字体警告除外）

### 8.2 内容完整性检查
- [ ] 正文篇幅 **≥ 30 页**，第 3、4、5 章为主要篇幅
- [ ] 摘要分为 **3 个自然段**（背景、技术、结构），300-500 字
- [ ] 第 1 章"目的意义"分条列点；第 2 章篇幅 2-3 页、结合项目说明选型理由
- [ ] 第 3 章：总体功能需求分析图 + 每角色 UML 用例图（含 `<<use>>`/`<<extend>>`/`<<include>>`）+ 非功能需求
- [ ] 第 4 章：总体设计 4 图（架构/前后端分离/MVC）+ 5 核心模块流程图 + 1 张 ER 图 + 数据库总表/分表
- [ ] 第 5 章：5 核心模块，每模块 = 核心代码（≤半页、上方 `#功能说明` 注释）+ 时序图 + 界面截图；测试用例表纵向特殊格式（A001~A005）
- [ ] 第 6 章：总结+展望两小节，展望分条列出，**无个人感想**

## 9. 常见错误与修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 图片不显示 | 路径错误或未放 `img/` 目录 | 确保图片在 `img/`，引用时不写 `img/` 前缀 |
| 大段空白 | `[H]` 浮动参数导致 | 改为 `[htbp]` |
| 参考文献不显示 | 未在正文中 `\cite` 引用 | 确保每条参考文献都被引用 |
| 第 2 章篇幅过长 | 技术介绍过于百科全书式 | 控制 2-3 页，结合项目说明选型理由 |
| 第 4 章表格过多 | 数据库设计堆砌所有表 | 只保留核心业务表，用一张 ER 图代替 |
| 第 5 章像操作手册 | 从用户操作角度描述 | 改为从代码实现角度，描述前后端交互逻辑和核心算法 |
| 第 6 章像日记 | 写了个人感想和心路历程 | 删除感想，总结用技术语言，展望分条列点 |
| 图片引用不规范 | 写了"如下图"、"如下表" | 统一改为"如图/表 `\ref{...}` 所示" |