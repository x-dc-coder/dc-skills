
# Mermaid 流程图

## 核心目标

根据用户描述生成**可直接渲染、尽量不报错**的 Mermaid 流程图代码，专用于软件工程系统论文。

## 设计风格

- **背景**：白色
- **节点**：纯白色背景 + 黑色边框
- **复杂度**：适中，有适当分支，不要过于复杂
- **场景**：本科课程设计论文、毕业设计论文

### 白色背景配置（必须）

所有流程图必须包含以下样式定义，确保白色背景和白色节点：

```mermaid
%%{init: {'themeVariables': { 'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'stepAfter', 'padding': 0, 'nodeSpacing': 10, 'rankSpacing': 15, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    %% 定义默认样式：白色背景，黑色边框
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    classDef diamond fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000

    %% 你的流程图节点...
```

**关键样式规则：**
- `fill:#ffffff` - 节点填充纯白色
- `stroke:#000000` - 黑色边框
- `stroke-width:2px` - 边框宽度2像素
- `color:#000000` - 黑色文字
- 字体统一为宋体 **16px**
- `flowchart` 紧凑配置：`padding: 0, nodeSpacing: 10, rankSpacing: 15`（最小留白）
- 菱形判断节点需要额外定义 `diamond` 类并应用

## 输出格式

**只输出一个 Mermaid 代码块**，格式如下：

```mermaid
flowchart TD
    %% 流程图内容
```

**禁止**：
- 代码块前后添加任何解释文字
- 输出多个版本
- 使用 "下面是代码" 之类的说明

## 稳定性优先规则

### 图类型选择
- **默认使用** `flowchart TD`（自上而下流程图）
- 其他可选：`sequenceDiagram`、`classDiagram`、`stateDiagram-v2`
- 不要混用不同图类型的语法
- 分层架构图（表现层→业务逻辑层→模型层→数据层）也用 flowchart TD + subgraph，三个技巧见「论文流程图常见场景 → 4. 分层系统架构图」

### 节点 ID 规则
- **只能使用**：英文字母、数字、下划线
- **示例**：`start_node`、`step_1`、`user_input`、`check_login`
- **禁止**：空格、中文、连字符 `-`、括号、特殊字符

### 节点显示文本规则
- 文本尽量简短（2-8 个字为佳）
- 中文可以放在显示文本中，但要简洁
- 避免：双引号、反引号、尖括号、多层括号、分号

### 连线规则
- **默认**：`A --> B`
- **带标签**：`A -->|是| B`、`A -->|否| C`
- 不要滥用特殊箭头类型

### 其他稳定性规则
1. 判断节点可用菱形 `{}`，但保持简单
2. 子图 `subgraph` 只有明显必要时才使用
3. 每个节点先定义再引用，避免悬空节点
4. 避免交叉依赖和过多回环
5. 不使用实验性特性

## 降级策略

如果需求复杂，按以下顺序降级：
1. 保留核心节点和主干流程
2. 删除次要说明文字
3. 删除装饰性分组
4. 简化为基础 `flowchart TD`

## 论文流程图常见场景

### 1. 系统总体流程
```mermaid
%%{init: {'themeVariables': { 'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'stepAfter', 'padding': 0, 'nodeSpacing': 10, 'rankSpacing': 15, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    %% 定义样式：白色背景，黑色边框
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    classDef diamond fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    
    start([开始]) --> input[用户输入]
    input --> validate{验证}
    validate -->|有效| process[处理业务]
    validate -->|无效| error[提示错误]
    error --> input
    process --> save[保存数据]
    save --> output[返回结果]
    output --> end_node([结束])
    
    class validate diamond
```

### 2. 登录流程
```mermaid
%%{init: {'themeVariables': { 'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'stepAfter', 'padding': 0, 'nodeSpacing': 10, 'rankSpacing': 15, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    %% 定义样式：白色背景，黑色边框
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    classDef diamond fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    
    start([开始]) --> input[输入账号密码]
    input --> check{验证账号}
    check -->|存在| check_pwd{验证密码}
    check -->|不存在| error1[账号不存在]
    check_pwd -->|正确| login[登录成功]
    check_pwd -->|错误| error2[密码错误]
    error1 --> input
    error2 --> input
    login --> end_node([结束])
    
    class check,check_pwd diamond
```

### 3. 数据增删改查流程
```mermaid
%%{init: {'themeVariables': { 'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'stepAfter', 'padding': 0, 'nodeSpacing': 10, 'rankSpacing': 15, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    %% 定义样式：白色背景，黑色边框
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    classDef diamond fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    
    start([开始]) --> show[展示列表]
    show --> op{选择操作}
    op -->|新增| add[添加数据]
    op -->|查询| search[搜索数据]
    op -->|修改| edit[编辑数据]
    op -->|删除| delete[删除数据]
    add --> save[保存]
    edit --> save
    delete --> confirm{确认删除}
    confirm -->|是| del_save[执行删除]
    confirm -->|否| show
    search --> show
    save --> show
    del_save --> show
    
    class op,confirm diamond
```

### 4. 分层系统架构图（论文最常用）

分层架构图（表现层→业务逻辑层→模型层→数据层）是论文「系统设计」章节最常见的图。Mermaid 画分层架构有**三个必用技巧，缺一不可**：

1. **subgraph 间链式连接**：`P --> B --> M --> D` 强制四层自上而下垂直堆叠（否则 dagre 会把 subgraph 左右分栏）。
2. **层内 `direction LR`**：让每层节点横向排列。
3. **`~~~` 不可见连接**：强制无连接关系的同层节点也水平一排（否则会竖排成窄高框）。

```mermaid
%%{init: {'themeVariables': {'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'stepAfter', 'padding': 0, 'nodeSpacing': 20, 'rankSpacing': 25, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000

    subgraph P["表现层"]
        direction LR
        web["Web 前端"] ~~~ mobile["移动端"] ~~~ api["REST API"]
    end

    subgraph B["业务逻辑层"]
        direction LR
        user["用户服务"] ~~~ analysis["情感分析服务"] ~~~ result["结果管理"]
    end

    subgraph M["模型层"]
        direction LR
        preprocess["文本预处理"] --> bert["BERT 编码器"] --> bilstm["BiLSTM"] --> attention["注意力机制"] --> softmax["Softmax 分类"]
    end

    subgraph D["数据层"]
        direction LR
        mysql[("MySQL")] ~~~ vecdb[("词向量库")] ~~~ redis[("Redis 缓存")]
    end

    P --> B --> M --> D
```

**要点**：
- 数据库/存储节点用 `[("名称")]`（圆柱形）
- 层内有先后顺序（如模型层流水线）用 `-->`，同层并列节点用 `~~~`
- subgraph ID 用单个大写字母（P/B/M/D），避免与节点 ID 冲突
- 数据层必须放最底（`P --> B --> M --> D` 的链式方向决定层级顺序）
- **架构图/模型图必须用 `curve: 'linear'`**（直线箭头）；`curve: 'stepAfter'`（阶梯折线）只适合流程图，用于架构图会导致箭头拐弯「未打直」

### 5. 模型/网络结构图（深度学习论文必备）

神经网络结构图（BiLSTM+Attention、CNN、Transformer）用 flowchart TD + 分层 subgraph，层间主线 + 层内细节，curve: linear。

```mermaid
%%{init: {'themeVariables': {'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'linear', 'padding': 0, 'nodeSpacing': 20, 'rankSpacing': 25, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000

    subgraph L1["输入层"]
        text["评论文本 服务态度很好"]
    end
    subgraph L2["词嵌入层"]
        direction LR
        e1["e₁"] ~~~ e2["e₂"] ~~~ e3["e₃"]
    end
    subgraph L3["BiLSTM 双向编码层"]
        direction LR
        f1["h₁→"] --> f2["h₂→"] --> f3["h₃→"]
        b3["←h₃"] --> b2["←h₂"] --> b1["←h₁"]
    end
    subgraph L4["隐状态拼接"]
        direction LR
        h1["h₁"] ~~~ h2["h₂"] ~~~ h3["h₃"]
    end
    subgraph L5["注意力机制"]
        direction TB
        a1["α₁"] & a2["α₂"] & a3["α₃"] --> s["s = Σαᵢhᵢ"]
    end
    subgraph L6["输出层"]
        direction LR
        dense["全连接"] --> softmax["Softmax"] --> out["正面/负面/中性"]
    end

    L1 --> L2 --> L3 --> L4 --> L5 --> L6
```

**要点**：
- 双向结构用两条方向相反的链（前向 h₁→-->h₂→，后向 ←h₃-->←h₂）
- 多对一汇聚（注意力加权求和）用 & --> 语法：a1 & a2 & a3 --> s
- 层内并列节点用 ~~~，层内有先后顺序用 -->
- 已知局限：Mermaid 的 dagre 路由会让「多对一汇聚」的斜线箭头带轻微折角（绕过中间节点），属正常现象；若要求三个箭头完全垂直直线，改用 Graphviz（rank=same 精确对齐）

## 渲染为图片

使用 mermaid-cli 将生成的 Mermaid 代码渲染为 PNG 或 SVG：

```bash
# 安装 mermaid-cli（如未安装）
npm install -g @mermaid-js/mermaid-cli

# 渲染为 PNG（白色背景 + 2x 分辨率，保证论文清晰度）
mmdc -i input.mmd -o output.png -b white -s 2

# 或指定更高分辨率
mmdc -i input.mmd -o output.png -b white -s 3

# 渲染为 SVG（矢量格式，Word 中任意缩放不模糊，推荐）
mmdc -i input.mmd -o output.svg -e svg
```

**关键参数：**
- `-i` 输入的 .mmd 文件路径
- `-o` 输出的图片路径
- `-e svg` 输出 SVG 矢量格式（**推荐**，Word 中无损缩放）
- `-b white` 设置白色背景（PNG 时必须）
- `-s` 缩放比例（PNG 建议 2 起步，确保插入论文后不模糊）

## 自检清单

输出前检查：
- [ ] 图类型唯一且正确
- [ ] 包含白色背景样式定义：`classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000`
- [ ] 菱形节点定义样式：`classDef diamond fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000`
- [ ] 所有菱形节点应用 diamond 类：`class 节点名1,节点名2 diamond`
- [ ] 只有 Mermaid 代码块
- [ ] 节点 ID 无非法字符
- [ ] 无未闭合结构
- [ ] 无解释文字混入
- [ ] 是否可用更保守写法
- [ ] 分层架构图：subgraph 间有链式连接 P-->B-->M-->D；层内 direction LR；并列节点用 ~~~；数据库节点用 [(名称)] 圆柱形

## 示例对话

**用户**：帮我画一个用户注册的流程图

**输出**：
```mermaid
%%{init: {'themeVariables': { 'fontFamily': 'SimSun, Noto Serif CJK SC, serif', 'fontSize': '16px', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'curve': 'stepAfter', 'padding': 0, 'nodeSpacing': 10, 'rankSpacing': 15, 'useMaxWidth': true, 'htmlLabels': false}}}%%
flowchart TD
    %% 定义样式：白色背景，黑色边框
    classDef default fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    classDef diamond fill:#ffffff,stroke:#000000,stroke-width:2px,color:#000000
    
    start([开始]) --> input[填写注册信息]
    input --> check{信息验证}
    check -->|通过| check_dup{检查重复}
    check -->|失败| error[提示错误]
    check_dup -->|无重复| create[创建账号]
    check_dup -->|已存在| dup_error[账号已存在]
    error --> input
    dup_error --> input
    create --> success[注册成功]
    success --> end_node([结束])
    
    class check,check_dup diamond
```
