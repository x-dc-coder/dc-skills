# 期刊/会议论文图片 / 表格 / 公式占位符规范

本文件定义期刊论文模式（Mode B）下的图表公式占位符语法和类型词汇。出图时 Read `~/projects/dc-skills/diagram/SKILL.md`，按下表类型执行。机械化规范见 `shared-markdown-norms.md`。

## 占位符语法（与本科毕设模式一致）

```
> [图X-Y 图片标题]
> 描述：详细描述图片应展示的内容
```

- X 为章节号，Y 为章内顺序号
- 编号全章连续，不得跳缺或重复
- 描述需足够详细，以便后续调用 skill 生成图片时明确知道要画什么

## 图片类型词汇（journal 模式新增）

### framework-overview（方法总览图）

- **何时使用**：Method 章节开头，展示整体方法/框架的工作流
- **描述必须包含**：输入数据流向、核心模块名称、模块间数据/控制流、输出位置、（如适用）CPU/GPU 协作边界、（如适用）训练/推理阶段标注
- **对接**：diagram 类型 `flow`（Mermaid 分层架构图，无独立技能）
- **占位符示例**：
  ```
  > [图4-1 TGA 框架总览图]
  > 描述：分层架构图。顶层为输入（VRP 实例：客户节点 + 距离矩阵 + 容量约束），
  > 经"属性矩阵预计算"模块生成每条路线的属性矩阵 →
  > 中间为 GPU 张量化算子层（含 Extraction / Concatenation / Differencing / Evaluation 四个子模块），
  > 双向箭头标注 CPU↔GPU 数据传输 →
  > 底层为 Local Search Controller（CPU 侧，决定接受/拒绝 move），
  > 右侧标注 PyTorch 作为张量库
  ```

### network-structure（神经网络结构图）

- **何时使用**：深度学习方法类论文，展示模型各层（编码器、注意力、解码器）
- **描述必须包含**：各层名称、输入/输出维度、连接方式（残差、跳跃连接）、激活函数位置、参数量标注
- **对接**：diagram 类型 `flow`（Mermaid 分层 subgraph 模型图）
- **占位符示例**：
  ```
  > [图3-2 编码器-解码器网络结构]
  > 描述：从左到右展示网络。输入层（n×d 维节点特征）→
  > 4 层 Multi-Head Attention（每层 8 头，d=128）→
  > 残差连接 + LayerNorm →
  > 解码器：自回归输出路由序列 →
  > 右侧标注每层参数量（共 12.4M）
  ```

### algorithm-flow（算法流程图）

- **何时使用**：复杂算法的工作流（如遗传算法主循环、强化学习训练流程）
- **描述必须包含**：起点/终点、每个判断分支的"是/否"走向、循环回边、并行分支（如有）、子过程调用
- **对接**：diagram 类型 `flow`（Mermaid 流程图）

### data-plot-curve / data-plot-bar / data-plot-heatmap / data-plot-scatter（实验结果图）

- **何时使用**：Experiments 章节展示量化结果
- **描述必须包含**：横轴/纵轴物理量与单位、图例标签（每个系列一行）、数据来源文件路径、（如适用）对数坐标标注、（如适用）误差棒含义
- **对接 skill**：**无 diagram skill**——这些是数据驱动的图，由用户后续用 matplotlib / seaborn 渲染。占位符中应写明"由 `<result.csv>` 列 X / Y 渲染"
- **占位符示例**：
  ```
  > [图5-2 收敛曲线对比图]
  > 描述：折线图。横轴：迭代次数（0-10000，线性刻度）；
  > 纵轴：目标函数值（归一化，0-1）；
  > 4 条系列：TGA-ours（蓝实线）、CPU-baseline（红虚线）、LNS-3opt（绿点线）、Or-opt（橙点划线）；
  > 数据来源：`experiments/convergence_cvrp_C100.json`；
  > 每条曲线带 ±1 标准差阴影（10 次随机种子）
  ```

### concept-diagram（概念示意图）

- **何时使用**：Introduction 章节说明研究动机、问题定义
- **描述必须包含**：核心概念名称、要素关系（因果、包含、对比）
- **对接**：diagram 类型 `flow`，或手绘

## 表格类型词汇

| 子类型 | 何时使用 | 列约定 |
|--------|---------|--------|
| `benchmark-comparison` | Experiments 主结果 | 方法名 \| 数据集 \| 指标1 \| 指标2 \| ... \| 加粗最优 |
| `ablation` | 消融实验 | 配置 \| 各组件开关 \| 指标 \| Δ vs 完整模型 |
| `hyperparameter` | 实验设置 | 超参名 \| 取值范围 \| 搜索方法 \| 最终值 \| 来源 |
| `dataset-statistics` | 实验设置 | 数据集名 \| 实例数 \| 节点范围 \| 容量 \| 来源 |
| `hardware-spec` | 实验设置 / 算力分析 | 型号 \| 架构 \| 核心数 \| 显存 \| 带宽 \| TFLOPS |
| `notation-table` | Preliminaries | 符号 \| 类型 \| 含义 \| 首次出现章节 |

## 公式类型词汇

| 子类型 | 何时使用 | LaTeX 骨架 |
|--------|---------|-----------|
| `objective-function` | Preliminaries / Method 开头 | `$$\min_{x \in \mathcal{X}} f(x) \tag{2-1}$$` |
| `constraint` | Preliminaries（约束优化） | `$$\text{s.t.} \quad g_i(x) \leq 0, \quad i = 1, \dots, m \tag{2-2}$$` |
| `loss-function` | Method（深度学习） | `$$\mathcal{L} = -\sum_{i} y_i \log \hat{y}_i + \lambda \|\theta\|^2 \tag{3-1}$$` |
| `attention-mechanism` | Method（Transformer 类） | `$$\text{Attn}(Q,K,V) = \text{softmax}\left(\frac{QK^\top}{\sqrt{d_k}}\right)V \tag{3-2}$$` |
| `state-transition` | Method（强化学习 / 动态规划） | `$$V(s) \leftarrow V(s) + \alpha [r + \gamma \max_a V(s') - V(s)] \tag{4-1}$$` |
| `complexity-bound` | Method 末尾或附录 | `$$T_{\text{eval}} = O\left(\frac{n^2}{p}\right) \quad \text{(p threads)} \tag{5-1}$$` |

公式编号一律使用 `\tag{X-Y}` 置于 `$$...$$` 块内右侧。

## diagram 类型路由表

| 占位符中的图片类型 | diagram 类型 | 所需输入 |
|-------------------|---------------|---------|
| 方法总览图 / 系统架构图 | `flow` | 文字描述（层名 + 数据流） |
| 神经网络结构图 | `flow` | 文字描述（层名 + 维度 + 连接） |
| 算法流程图 / 业务流程 | `flow` | 直接生成 Mermaid 代码 |
| 概念示意图 / 分类法图 | `flow`（或手绘） | 文字描述 |
| 实验结果图（折线/柱状/热力/散点） | 不适用（用户后续用 matplotlib 渲染） | 数据文件路径 + 轴/系列说明 |
| 实验对比表 / 消融表 / 超参表 | 不适用（直接写 Markdown 表格） | - |
| （仅本科毕设）用例图 / ER图 / 功能模块图 / 界面截图 | 见 `undergrad-image-spec.md` | - |
