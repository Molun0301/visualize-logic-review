# review.json 输入格式

目录：顶层字段 · 流程类图（flowchart/state/architecture/dfd/call）· 时序图 · 发现与测试 · 锚点 · 命令行 · 旧格式兼容

只写语义，不写坐标。脚本校验全部字段后才写文件；有错误时一次列出所有问题。ID 一律以字母开头，只含字母、数字、`_`、`-`；节点、连线、参与者、消息的 ID 不能用 Mermaid 关键字（如 `end`、`state`、`class`），推荐 `N1`、`E1`、`M1`。

## 顶层字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `title` | 是 | 报告标题 |
| `summary` | 否 | 一两句结论，显示在报告顶部 |
| `language` | 否 | `zh`（默认）或 `en`，决定界面文字 |
| `sources` | 否 | `{证据ID: 来源描述}`，如 `{"S1": "order/pay.py:submit() 第 40–88 行（a1b2c3d）"}`。元素引用的证据必须在这里 |
| `diagrams` | 是 | 1–12 张图，通常 1–3 张 |
| `findings` | 否 | 审查发现，见下文 |
| `tests` | 否 | 验证计划，见下文 |
| `decisions` | 否 | 字符串数组：需要用户决定的事 |
| `limitations` | 否 | 字符串数组：分析范围和盲区 |

每张图都有：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 图 ID，同一报告内唯一；也是 HTML 章节锚点（`index.html#图ID`），不能用 `findings`、`tests`、`lint`、`evidence`，也不能以 `f-`、`t-`、`v-` 开头 |
| `kind` | 是 | `flowchart` `state` `architecture` `dfd` `call` `sequence` |
| `title` | 是 | 写明“现状 / 提案 / 对比”和主题，如“提案 · 支付重试” |
| `basis` | 是 | 依据和范围：用了哪些证据、哪个版本、是否运行过 |
| `question` | 否 | 本图回答的问题，强烈建议填写 |
| `status` | 建议 | 图内元素的默认状态：`fact` 事实、`proposal` 提案、`assumption` 假设、`unknown` 未知。元素未写 status 时用它；两者都没有会报错 |
| `show_ids` | 否 | 默认 `true`，在节点上显示编号徽标 |

状态的显示方式：与默认状态相同的元素不加标记；不同的元素在编号徽标里注明，假设用虚线框，未知用点线框。

## 流程类图

`flowchart`、`state`、`architecture`、`dfd`、`call` 共用 `nodes`、`edges`、`groups`。可选 `direction`：`TB` 从上到下（流程图、状态机、调用图默认）或 `LR` 从左到右（架构图、数据流图默认）。

### nodes

| 字段 | 说明 |
| --- | --- |
| `id`、`label` | 必填。标签写短动作或短问题，一般不超过 16 个字；初态 `initial`、终态 `final` 可省略 label |
| `detail` | 第二行小字：函数名、文件位置、参数等 |
| `shape` | 默认 `process`（状态机默认 `state`）。可选值见下表 |
| `tone` | 色调，覆盖形状默认色：`info` 蓝、`success` 绿、`warning` 琥珀、`danger` 红、`neutral` 灰、`data` 紫 |
| `status`、`refs` | 状态和证据 ID 数组 |
| `group` | 所属分组 ID |

| shape | 外观 | 用途 |
| --- | --- | --- |
| `start` | 绿色胶囊 | 流程入口 |
| `end` | 粗边胶囊 | 流程出口；成功用 `tone: success`，失败用 `tone: danger` |
| `process` | 圆角矩形 | 步骤、模块、函数 |
| `decision` | 菱形 | 判断；每条出边都要写条件 |
| `store` | 圆柱 | 数据库、队列、缓存、文件 |
| `external` | 双线矩形 | 外部系统、用户、第三方 |
| `state` | 大圆角矩形 | 状态机中的状态 |
| `initial` / `final` | 实心圆 / 靶心圆 | 状态机的初态和终态 |

### edges

| 字段 | 说明 |
| --- | --- |
| `from`、`to` | 必填，节点 ID；起止相同即自环 |
| `id` | 可省略，自动编为 E1、E2…（按出现顺序）。被发现或测试引用的连线建议显式写 id，图上会显示该编号 |
| `label` | 条件、事件、协议或数据名。状态机写“事件 [守卫] / 动作” |
| `type` | 连线类型，决定颜色和线型，见下表。数据流图默认 `data`，其余默认 `normal` |
| `status`、`refs` | 状态和证据。起点节点已有证据时，连线可以不写，分支条件视为出自起点节点引用的源码 |

| type | 外观 | 用途 |
| --- | --- | --- |
| `main` | 粗蓝实线 | 正常主路径，让读者先看懂主干 |
| `normal` | 灰实线 | 普通流转 |
| `error` | 红虚线 | 失败、异常、拒绝 |
| `retry` | 橙色点划线 | 重试、回环；标签里写上限，否则会有警告 |
| `cancel` | 灰点线 | 取消、超时 |
| `async` | 紫虚线 + 空心箭头 | 异步消息、事件 |
| `data` | 青色实线 | 数据流 |
| `depends` | 灰虚线 + 空心箭头 | 依赖关系 |
| `return` | 浅灰虚线 + 空心箭头 | 返回 |

### groups

`{"id": "G1", "label": "订单服务", "parent": "G0", "style": "default"}`。`parent` 用于嵌套；`style` 为 `default`、`trust`（红色虚线，标“信任边界”）或 `external`（灰色虚线，标“外部”）。架构图在标题或 basis 里写明 C4 层级；只是简化框图时写“C4 风格概览”。

### 最小示例

```json
{
  "title": "提案：输入校验",
  "sources": {"R1": "用户需求：无效输入应被拒绝"},
  "diagrams": [{
    "id": "validate", "kind": "flowchart", "status": "proposal",
    "title": "提案 · 输入校验", "basis": "需求 R1；尚无代码",
    "question": "无效输入会不会进入处理逻辑？",
    "nodes": [
      {"id": "N1", "label": "收到请求", "shape": "start"},
      {"id": "N2", "label": "输入有效？", "shape": "decision", "refs": ["R1"]},
      {"id": "N3", "label": "处理请求"},
      {"id": "N4", "label": "返回 400", "shape": "end", "tone": "danger"},
      {"id": "N5", "label": "返回结果", "shape": "end", "tone": "success"}
    ],
    "edges": [
      {"from": "N1", "to": "N2", "type": "main"},
      {"from": "N2", "to": "N3", "label": "是", "type": "main"},
      {"from": "N2", "to": "N4", "label": "否", "type": "error", "refs": ["R1"]},
      {"from": "N3", "to": "N5", "type": "main"}
    ]
  }]
}
```

## 时序图

`kind: "sequence"`，使用 `participants`、`messages`、`frames`。

- `participants`：有序数组，2–8 个。字段 `id`、`label`、`detail`、`shape`（`service` 默认、`actor` 人、`store` 存储、`external` 外部）、`status`、`refs`。
- `messages`：按显示顺序排列，最多 40 条（超过 24 条会建议拆图）。
  - 消息：`id`、`from`、`to`、`label`；`type` 为 `call`（同步，实心箭头，默认）、`async`（异步，空心箭头）或 `return`（返回，虚线）；`tone` 为 `normal`、`main`、`error`、`retry`、`cancel`，只改变颜色；`from` 与 `to` 相同时画成自调用回环。
  - 注释：`{"id": "M6", "note": "最多等待 5 秒", "over": ["API", "PG"]}`，`over` 写 1–2 个参与者。用来写超时、等待点、锁持有范围。
- `frames`：`type` 为 `alt`（分支）、`opt`（可选）、`loop`（循环，标签写终止条件或上限）、`par`（并行）、`critical`、`break`；`label` 写条件；`start`、`end` 写消息 ID 或从 1 开始的序号（含两端）。`alt`、`par`、`critical` 可加 `branches: [{"before": "M6", "label": "超时"}]`，从 before 那条消息开始新分支。frame 只能嵌套或分离，不能交叉，内层 frame 不能跨过外层的分支分界。

消息的上下顺序只表示设计中的顺序，不能证明跨线程的先后关系。不要把“界面停止等待”画成“后台副作用已停止”。

```json
{
  "id": "submit", "kind": "sequence", "status": "proposal",
  "title": "提案 · 提交与超时", "basis": "R1；消息顺序是设计意图",
  "participants": [
    {"id": "UI", "label": "界面", "shape": "actor"},
    {"id": "API", "label": "订单服务"}
  ],
  "messages": [
    {"id": "M1", "from": "UI", "to": "API", "label": "提交订单"},
    {"id": "M2", "from": "API", "to": "UI", "label": "成功", "type": "return"},
    {"id": "M3", "note": "等待超过 5 秒", "over": ["UI"]},
    {"id": "M4", "from": "UI", "to": "API", "label": "取消请求", "type": "async", "tone": "cancel"}
  ],
  "frames": [{"type": "alt", "label": "5 秒内返回", "start": "M2", "end": "M4",
              "branches": [{"before": "M3", "label": "超时"}]}]
}
```

## 发现与测试

`findings` 每项：

| 字段 | 说明 |
| --- | --- |
| `id` | 可省略，自动编为 F1、F2… |
| `level` | `confirmed` 已证实问题、`risk` 设计风险（默认）、`verify` 待验证 |
| `title` | 一句话说明问题 |
| `anchors` | 指向图上元素的锚点数组，图上会出现对应编号的标记 |
| `trigger`、`impact`、`suggestion`、`tradeoff` | 触发条件、影响、建议、代价或权衡 |
| `refs` | 证据；`confirmed` 没有证据会有警告 |

`tests` 每项：`id`（可省略，自动编为 T1…）、`title`（场景）、`covers`（锚点数组）、`expect`（可验证的预期结果，如“第 3 次失败后进入 N10，不再排队”）、`status`（`pass` 已运行通过、`fail` 已运行失败、`todo` 未运行（默认）、`blocked` 受阻）、`command`（可选）。

两者都可以直接写字符串，只作为文字条目显示，不与图关联。

## 锚点

- `"图ID:元素ID"`，如 `"flow:E9"`、`"seq:M7"`、`"arch:G2"`
- 元素 ID 在全部图里唯一时可省略图 ID，如 `"E9"`
- 只写图 ID 表示整张图

可锚定节点、连线、分组、参与者、消息和注释。

## 命令行

    python3 -I scripts/render_review.py review.json --out 输出目录 [--engine auto|dot|builtin] [--strict]

- `--engine auto`（默认）：有 `dot` 用 Graphviz，否则用内置分层排版；`builtin` 强制内置排版
- `--strict`：有结构警告时以退出码 2 失败
- 退出码 0 成功（警告打印到标准错误），2 为输入错误
- 输出：`index.html`（含全部图、发现、测试、结构检查、证据）、每图 `<id>.svg` 和 `<id>.mmd`、原样保存的 `review.json`

    python3 -I scripts/snapshot.py 输出目录 [--html] [--scale 2]

把目录下每个 SVG（加 `--html` 时还有 index.html）截成同名 PNG。

Mermaid 源保留节点、连线、条件、分组、frame；状态和证据写在 `%%` 注释里，颜色用 `classDef`/`linkStyle` 近似表达。Mermaid 重新排版后的布局会与 SVG 不同；状态机里跨分组连接内部状态的迁移，Mermaid 可能不支持。

## 旧格式兼容

旧版输入中的 `x`、`y`、`w`、`h`、`via`、`label_at`、`from_port`、`to_port`、`dashed`、`width`、`height` 会被忽略并给出警告，其余字段照常使用。
