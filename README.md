# 逻辑图审查

把方案或代码里的分支、循环、状态、并发和数据流画成可离线查看的逻辑图。输入语义 JSON（结构化数据格式），输出 HTML（网页）、SVG（矢量图）和 Mermaid（文本图表语言）源文件，并检查不可达节点、缺失分支、重试上限及证据关联。

支持 Codex 和 Claude Code。渲染仅依赖 Python 3.10+；Graphviz（图布局工具）可选，缺失时自动使用内置布局。截图需要本机 Chrome、Chromium、Edge 或 Firefox。

## 安装

```bash
git clone https://github.com/Molun0301/visualize-logic-review.git
cd visualize-logic-review
python3 scripts/install.py install --target both
```

`--target codex` 只安装到 `~/.agents/skills/visualize-logic-review`；`--target claude` 只安装到 `~/.claude/skills/visualize-logic-review`。`--home` 可指定隔离主目录。若已有手工安装的同名技能，先检查内容，再加 `--replace` 迁移。新会话加载生效。

也可以在 Claude 中采用原生插件方式（二选一，避免重复加载）：

```bash
claude plugin marketplace add Molun0301/visualize-logic-review
claude plugin install visualize-logic-review@visualize-logic-review
```

仓库提供根 `plugin.json`、Codex 兼容清单和 Claude 清单。规范依据：[OpenAI 插件打包说明](https://developers.openai.com/plugins/build/plugins)、[Claude 插件清单](https://code.claude.com/docs/en/plugins-reference)。GitHub 发布与官方插件目录收录是两个独立流程。

## 使用

向智能体提出“先画逻辑图审查这个方案”或使用 `visualize-logic-review` 技能。直接生成示例：

```bash
python3 -I skills/visualize-logic-review/scripts/render_review.py \
  skills/visualize-logic-review/examples/order-payment.json --out work/示例
python3 -I skills/visualize-logic-review/scripts/snapshot.py work/示例
```

打开 `work/示例/index.html`。输入说明在 [格式说明](skills/visualize-logic-review/references/input-format.md)。图中的结构警告只是审查线索，不能代替运行验证。

## 更新与卸载

```bash
git pull --ff-only
python3 scripts/install.py install --target both
# 卸载相同安装方式：
python3 scripts/install.py uninstall --target both
```

原生插件安装使用 `claude plugin marketplace update visualize-logic-review` 和 `claude plugin update visualize-logic-review@visualize-logic-review`；卸载用 `claude plugin uninstall visualize-logic-review@visualize-logic-review`。已有会话重新加载插件或新开会话。

## 维护

```bash
python3 scripts/check.py
```

版本 `1.0.0`，MIT 许可。[维护与发布](docs/公开文档/维护与发布.md) · [更新记录](docs/公开文档/更新记录.md)。
