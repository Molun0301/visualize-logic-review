#!/usr/bin/env python3
"""把结构化审查输入渲染成离线 SVG、HTML 和 Mermaid。

只依赖 Python 3 标准库；本机有 Graphviz（dot）时用它排版，否则用内置分层排版。
用法：python3 render_review.py review.json --out 输出目录 [--engine auto|dot|builtin] [--strict]
"""
import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

GRAPH_KINDS = ('flowchart', 'state', 'architecture', 'dfd', 'call')
KINDS = GRAPH_KINDS + ('sequence',)
STATUSES = ('fact', 'proposal', 'assumption', 'unknown')
SHAPES = ('process', 'decision', 'start', 'end', 'store', 'external', 'state', 'initial', 'final')
PARTICIPANT_SHAPES = ('service', 'actor', 'store', 'external')
# 色调：(填充, 边框, 强调文字)
TONES = {
    'info': ('#eef4ff', '#5b8def', '#1e40af'),
    'success': ('#ecfdf3', '#3ba56f', '#166534'),
    'warning': ('#fff8e6', '#dba325', '#92400e'),
    'danger': ('#fff1f1', '#e05555', '#991b1b'),
    'neutral': ('#f5f6f8', '#8a96a8', '#334155'),
    'data': ('#f5f1ff', '#9271e0', '#5b21b6'),
}
SHAPE_TONE = {'process': 'info', 'decision': 'warning', 'start': 'success', 'end': 'neutral', 'store': 'data',
              'external': 'neutral', 'state': 'info', 'initial': 'neutral', 'final': 'neutral'}
# 连线类型：(颜色, 线宽, 虚线样式, 箭头)
EDGE_TYPES = {
    'main': ('#1d4ed8', 2.6, '', 'solid'),
    'normal': ('#4b5563', 1.6, '', 'solid'),
    'error': ('#dc2626', 1.8, '7 4', 'solid'),
    'retry': ('#c2410c', 1.8, '10 3 2 3', 'solid'),
    'cancel': ('#6b7280', 1.6, '2 4', 'solid'),
    'async': ('#7c3aed', 1.6, '6 4', 'open'),
    'data': ('#0f766e', 1.8, '', 'solid'),
    'depends': ('#4b5563', 1.4, '5 4', 'open'),
    'return': ('#64748b', 1.4, '4 3', 'open'),
}
DEFAULT_EDGE = {'flowchart': 'normal', 'state': 'normal', 'architecture': 'normal', 'dfd': 'data', 'call': 'normal'}
MESSAGE_TYPES = ('call', 'async', 'return')
MESSAGE_TONES = ('main', 'normal', 'error', 'retry', 'cancel')
FRAME_TYPES = ('alt', 'opt', 'loop', 'par', 'critical', 'break')
LEVEL_COLORS = {'confirmed': '#dc2626', 'risk': '#d97706', 'verify': '#2563eb'}
TEST_STATES = ('pass', 'fail', 'todo', 'blocked')
GROUP_STYLES = ('default', 'trust', 'external')
STATUS_COLORS = {'fact': '#334155', 'proposal': '#1d4ed8', 'assumption': '#b45309', 'unknown': '#7c3aed'}
# Mermaid 关键字不能作为 ID
RESERVED = {'end', 'subgraph', 'graph', 'flowchart', 'state', 'class', 'classdef', 'click', 'style', 'linkstyle',
            'direction', 'participant', 'actor', 'note', 'loop', 'alt', 'else', 'opt', 'par', 'and', 'critical',
            'break', 'rect', 'option', 'default'}
LEGACY_KEYS = ('x', 'y', 'w', 'h', 'via', 'label_at', 'from_port', 'to_port', 'dashed', 'width', 'height')
ID_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
# HTML 报告里固定使用的章节锚点；图的章节直接用图 ID，便于链接到 index.html#图ID
SECTION_IDS = {'findings', 'tests', 'lint', 'evidence'}
FONT = "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Source Han Sans SC','WenQuanYi Micro Hei',system-ui,sans-serif"

TEXT = {
    'zh': {
        'status': {'fact': '事实', 'proposal': '提案', 'assumption': '假设', 'unknown': '未知'},
        'kind': {'flowchart': '控制流程图', 'state': '状态机', 'architecture': '架构图', 'dfd': '数据流图',
                 'call': '调用图', 'sequence': '时序图'},
        'edge': {'main': '主路径', 'normal': '流转', 'error': '失败 / 异常', 'retry': '重试 / 回环',
                 'cancel': '取消 / 超时', 'async': '异步', 'data': '数据流', 'depends': '依赖', 'return': '返回'},
        'message': {'call': '同步调用', 'async': '异步消息', 'return': '返回'},
        'level': {'confirmed': '已证实问题', 'risk': '设计风险', 'verify': '待验证'},
        'test': {'pass': '已运行 · 通过', 'fail': '已运行 · 失败', 'todo': '未运行', 'blocked': '受阻'},
        'group': {'trust': '信任边界', 'external': '外部'},
        'default_status': '未标注的元素均为「{0}」',
        'question': '本图回答：', 'basis': '依据：', 'legend_status': '{0}（{1}）',
        'note': '注释', 'findings': '审查发现', 'tests': '验证计划', 'decisions': '需要决定', 'limitations': '分析限制',
        'evidence': '证据', 'lint': '结构检查', 'elements': '元素与证据', 'mermaid': 'Mermaid 可编辑源',
        'col_id': '编号', 'col_label': '内容', 'col_status': '状态', 'col_refs': '证据', 'col_where': '位置',
        'col_scenario': '场景', 'col_covers': '覆盖', 'col_expect': '预期结果', 'col_state': '运行状态',
        'trigger': '触发条件', 'impact': '影响', 'suggestion': '建议', 'tradeoff': '代价 / 权衡', 'command': '命令',
        'diagrams': '{0} 张图', 'n_findings': '{0} 条发现', 'n_tests': '{0} 项测试', 'none': '无',
        'engine': '排版：{0}', 'lint_ok': '未发现结构问题。', 'element_kinds': {'node': '节点', 'edge': '连线',
            'group': '分组', 'participant': '参与者', 'message': '消息', 'note': '注释'},
        'files': '单独文件：', 'builtin': '内置分层排版',
    },
    'en': {
        'status': {'fact': 'Fact', 'proposal': 'Proposal', 'assumption': 'Assumption', 'unknown': 'Unknown'},
        'kind': {'flowchart': 'Control flow', 'state': 'State machine', 'architecture': 'Architecture',
                 'dfd': 'Data flow', 'call': 'Call graph', 'sequence': 'Sequence'},
        'edge': {'main': 'Main path', 'normal': 'Flow', 'error': 'Failure / error', 'retry': 'Retry / loop',
                 'cancel': 'Cancel / timeout', 'async': 'Async', 'data': 'Data', 'depends': 'Depends on',
                 'return': 'Return'},
        'message': {'call': 'Sync call', 'async': 'Async message', 'return': 'Return'},
        'level': {'confirmed': 'Confirmed issue', 'risk': 'Design risk', 'verify': 'To verify'},
        'test': {'pass': 'Ran · passed', 'fail': 'Ran · failed', 'todo': 'Not run', 'blocked': 'Blocked'},
        'group': {'trust': 'Trust boundary', 'external': 'External'},
        'default_status': 'Unmarked elements are “{0}”',
        'question': 'Answers: ', 'basis': 'Basis: ', 'legend_status': '{0} ({1})',
        'note': 'Note', 'findings': 'Findings', 'tests': 'Test plan', 'decisions': 'Decisions', 'limitations': 'Limits',
        'evidence': 'Evidence', 'lint': 'Structure checks', 'elements': 'Elements & evidence', 'mermaid': 'Mermaid source',
        'col_id': 'ID', 'col_label': 'Content', 'col_status': 'Status', 'col_refs': 'Evidence', 'col_where': 'Where',
        'col_scenario': 'Scenario', 'col_covers': 'Covers', 'col_expect': 'Expected', 'col_state': 'Run status',
        'trigger': 'Trigger', 'impact': 'Impact', 'suggestion': 'Suggestion', 'tradeoff': 'Trade-off', 'command': 'Command',
        'diagrams': '{0} diagrams', 'n_findings': '{0} findings', 'n_tests': '{0} tests', 'none': 'None',
        'engine': 'Layout: {0}', 'lint_ok': 'No structural issues found.', 'element_kinds': {'node': 'Node',
            'edge': 'Edge', 'group': 'Group', 'participant': 'Participant', 'message': 'Message', 'note': 'Note'},
        'files': 'Files: ', 'builtin': 'built-in layered layout',
    },
}


class InputError(Exception):
    pass


class Problems:
    """收集全部错误和警告，一次性报告，方便一轮改完输入。"""

    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, where, message):
        self.errors.append('%s: %s' % (where, message))

    def warn(self, where, message):
        self.warnings.append('%s: %s' % (where, message))

    def raise_if_any(self):
        if self.errors:
            raise InputError('\n'.join(self.errors))


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    """坐标保留一位小数，减小 SVG 体积。"""
    return ('%.1f' % value).rstrip('0').rstrip('.')


# ---------- 文字度量与折行 ----------

NARROW = set('iljtfrI.,:;\'"|!()[]{} `')
WIDE_LATIN = set('mwMW@%&')
NO_LINE_START = set('，。、；：？！）】」』》,.;:?!)]}')
TOKEN_RE = re.compile(r'[A-Za-z0-9_\-./#@]+|\s+|.', re.S)


def char_width(char, size):
    if unicodedata.east_asian_width(char) in 'WF':
        return size
    if char in NARROW:
        return size * 0.32
    if char in WIDE_LATIN:
        return size * 0.86
    if char.isupper() or char.isdigit():
        return size * 0.64
    return size * 0.56


def text_width(value, size):
    return sum(char_width(c, size) for c in str(value))


def wrap(value, size, max_width):
    """按显示宽度折行，并尽量让各行等长，避免末行只剩一两个字。"""
    lines = []
    for paragraph in str(value).split('\n'):
        greedy = wrap_greedy(paragraph, size, max_width)
        if len(greedy) > 1:
            total = text_width(paragraph, size)
            for slack in (1.06, 1.15, 1.3):
                balanced = wrap_greedy(paragraph, size, min(max_width, total / len(greedy) * slack))
                if len(balanced) == len(greedy):
                    greedy = balanced
                    break
        lines.extend(greedy)
    return lines


def wrap_greedy(paragraph, size, max_width):
    """英文单词尽量整体保留，中文逐字断开，行首避开标点。"""
    lines = []
    line, used = '', 0.0
    for token in TOKEN_RE.findall(paragraph):
        if token.isspace():
            if line:
                line, used = line + ' ', used + text_width(' ', size)
            continue
        width = text_width(token, size)
        if used + width > max_width and line.strip() and token not in NO_LINE_START:
            lines.append(line.rstrip())
            line, used = '', 0.0
        while width > max_width:
            cut, cut_width = '', 0.0
            for char in token:
                step = char_width(char, size)
                if cut_width + step > max_width and cut:
                    break
                cut, cut_width = cut + char, cut_width + step
            lines.append(cut)
            token = token[len(cut):]
            width = text_width(token, size)
        line, used = line + token, used + width
    lines.append(line.rstrip())
    return lines


def block_width(lines, size):
    return max([text_width(line, size) for line in lines] or [0])


# ---------- 输入校验与归一化 ----------

def get_text(item, key, where, problems, required=True):
    value = item.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            problems.error(where, '缺少非空文本字段 %s' % key)
        return ''
    if not isinstance(value, str):
        problems.error(where, '%s 必须是文本' % key)
        return ''
    return value.strip()


def check_id(value, where, problems, used, mermaid=True):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        problems.error(where, 'ID %r 必须以字母开头，只含字母、数字、_ 或 -' % (value,))
        return False
    if mermaid and value.lower() in RESERVED:
        problems.error(where, 'ID %r 是 Mermaid 关键字，请改用 N1、S1 之类的编号' % value)
        return False
    if value in used:
        problems.error(where, 'ID %r 重复' % value)
        return False
    used.add(value)
    return True


def get_status(item, default, where, problems):
    status = item.get('status', default)
    if status is None:
        problems.error(where, '缺少 status；在图上设置默认 status，或为该元素单独设置')
        return 'unknown'
    if status not in STATUSES:
        problems.error(where, 'status 只能是 %s' % '/'.join(STATUSES))
        return 'unknown'
    return status


def get_refs(item, sources, where, problems):
    refs = item.get('refs', [])
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        problems.error(where, 'refs 必须是证据 ID 数组')
        return []
    for ref in refs:
        if ref not in sources:
            problems.error(where, '证据 %s 不在 sources 中；不要虚构证据' % ref)
    return refs


def choice(item, key, options, default, where, problems):
    value = item.get(key, default)
    if value not in options:
        problems.error(where, '%s 只能是 %s' % (key, '/'.join(options)))
        return default
    return value


def warn_legacy(raw_items, where, problems):
    found = sorted({k for item in raw_items if isinstance(item, dict) for k in LEGACY_KEYS if k in item})
    if found:
        problems.warn(where, '已忽略旧版手工排版字段 %s；现在自动排版' % ', '.join(found))


def normalize_graph(raw, where, sources, problems):
    kind = raw['kind']
    default = raw.get('status')
    if default is not None and default not in STATUSES:
        problems.error(where, '图的 status 只能是 %s' % '/'.join(STATUSES))
        default = None
    direction = str(raw.get('direction', 'LR' if kind in ('architecture', 'dfd') else 'TB')).upper()
    direction = 'TB' if direction == 'TD' else direction
    if direction not in ('TB', 'LR'):
        problems.error(where, 'direction 只能是 TB 或 LR')
        direction = 'TB'
    used, groups, nodes, edges = set(), [], [], []
    raw_groups = raw.get('groups', []) or []
    raw_nodes = raw.get('nodes', []) or []
    raw_edges = raw.get('edges', []) or []
    for name, value in (('groups', raw_groups), ('nodes', raw_nodes), ('edges', raw_edges)):
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            problems.error(where, '%s 必须是对象数组' % name)
            return None
    warn_legacy([raw] + raw_groups + raw_nodes + raw_edges, where, problems)
    if not raw_nodes:
        problems.error(where, '至少需要一个节点')
    elif len(raw_nodes) > 30:
        problems.error(where, '节点 %d 个，超过 30 个上限；按边界拆成多张图' % len(raw_nodes))
    elif len(raw_nodes) > 14:
        problems.warn(where, '节点 %d 个，一屏难以读清；建议按模块或阶段拆图' % len(raw_nodes))
    for i, g in enumerate(raw_groups):
        w = '%s.groups[%d]' % (where, i)
        if check_id(g.get('id'), w, problems, used):
            groups.append({'id': g['id'], 'label': get_text(g, 'label', w, problems), 'parent': g.get('parent'),
                           'style': choice(g, 'style', GROUP_STYLES, 'default', w, problems)})
    group_ids = {g['id'] for g in groups}
    for g in groups:
        if g['parent'] is not None and g['parent'] not in group_ids:
            problems.error(where, '分组 %s 的 parent %r 不存在' % (g['id'], g['parent']))
            g['parent'] = None
    for g in groups:
        seen, cur = set(), g
        while cur and cur['parent']:
            if cur['id'] in seen:
                problems.error(where, '分组 %s 的 parent 形成环' % g['id'])
                g['parent'] = None
                break
            seen.add(cur['id'])
            cur = next((x for x in groups if x['id'] == cur['parent']), None)
    default_shape = 'state' if kind == 'state' else 'process'
    for i, n in enumerate(raw_nodes):
        w = '%s.nodes[%d]' % (where, i)
        if not check_id(n.get('id'), w, problems, used):
            continue
        shape = choice(n, 'shape', SHAPES, default_shape, w, problems)
        label = get_text(n, 'label', w, problems, required=shape not in ('initial', 'final'))
        group = n.get('group')
        if group is not None and group not in group_ids:
            problems.error(w, '分组 %r 不存在' % group)
            group = None
        nodes.append({'id': n['id'], 'label': label, 'detail': get_text(n, 'detail', w, problems, False),
                      'shape': shape, 'tone': choice(n, 'tone', tuple(TONES), SHAPE_TONE[shape], w, problems),
                      'status': get_status(n, default, w, problems), 'refs': get_refs(n, sources, w, problems),
                      'group': group})
    node_ids = {n['id'] for n in nodes}
    for i, e in enumerate(raw_edges):
        w = '%s.edges[%d]' % (where, i)
        eid = e.get('id')
        if eid is None:
            k = i + 1
            while 'E%d' % k in used or any(x.get('id') == 'E%d' % k for x in raw_edges):
                k += 1
            eid = 'E%d' % k
        if not check_id(eid, w, problems, used):
            continue
        for end in ('from', 'to'):
            if e.get(end) not in node_ids:
                problems.error(w, '%s 指向不存在的节点 %r' % (end, e.get(end)))
        edges.append({'id': eid, 'from': e.get('from'), 'to': e.get('to'),
                      'label': get_text(e, 'label', w, problems, False),
                      'type': choice(e, 'type', tuple(EDGE_TYPES), DEFAULT_EDGE[kind], w, problems),
                      'status': get_status(e, default, w, problems), 'refs': get_refs(e, sources, w, problems)})
    return {'kind': kind, 'direction': direction, 'status': default, 'groups': groups, 'nodes': nodes,
            'edges': edges, 'show_ids': raw.get('show_ids', True) is not False}


def item_index(value, ids, where, problems, name):
    """frame 的 start/end/before 可写 1 起始序号，也可写消息 ID。"""
    if isinstance(value, bool):
        value = None
    if isinstance(value, int) and 1 <= value <= len(ids):
        return value
    if isinstance(value, str) and value in ids:
        return ids.index(value) + 1
    problems.error(where, '%s 必须是 1–%d 的序号或已存在的消息 ID' % (name, len(ids)))
    return None


def normalize_sequence(raw, where, sources, problems):
    default = raw.get('status')
    if default is not None and default not in STATUSES:
        problems.error(where, '图的 status 只能是 %s' % '/'.join(STATUSES))
        default = None
    raw_parts, raw_items, raw_frames = raw.get('participants', []), raw.get('messages', []), raw.get('frames', [])
    for name, value in (('participants', raw_parts), ('messages', raw_items), ('frames', raw_frames)):
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            problems.error(where, '%s 必须是对象数组' % name)
            return None
    if not 2 <= len(raw_parts) <= 8:
        problems.error(where, '参与者需要 2–8 个；更多时按边界拆图')
    if not 1 <= len(raw_items) <= 40:
        problems.error(where, '消息需要 1–40 条；更长的交互按阶段拆图')
    elif len(raw_items) > 24:
        problems.warn(where, '消息 %d 条，建议按阶段拆图' % len(raw_items))
    used, parts, items = set(), [], []
    for i, p in enumerate(raw_parts):
        w = '%s.participants[%d]' % (where, i)
        if check_id(p.get('id'), w, problems, used):
            parts.append({'id': p['id'], 'label': get_text(p, 'label', w, problems),
                          'detail': get_text(p, 'detail', w, problems, False),
                          'shape': choice(p, 'shape', PARTICIPANT_SHAPES, 'service', w, problems),
                          'status': get_status(p, default, w, problems), 'refs': get_refs(p, sources, w, problems)})
    part_ids = [p['id'] for p in parts]
    for i, m in enumerate(raw_items):
        w = '%s.messages[%d]' % (where, i)
        if not check_id(m.get('id'), w, problems, used):
            continue
        common = {'id': m['id'], 'status': get_status(m, default, w, problems),
                  'refs': get_refs(m, sources, w, problems)}
        if 'note' in m:
            over = m.get('over', [])
            over = [over] if isinstance(over, str) else over
            if not isinstance(over, list) or not 1 <= len(over) <= 2 or not all(o in part_ids for o in over):
                problems.error(w, 'note 的 over 必须是 1–2 个已存在的参与者 ID')
                over = part_ids[:1]
            common.update({'kind': 'note', 'label': get_text(m, 'note', w, problems), 'over': over})
        else:
            for end in ('from', 'to'):
                if m.get(end) not in part_ids:
                    problems.error(w, '%s 指向不存在的参与者 %r' % (end, m.get(end)))
            common.update({'kind': 'message', 'from': m.get('from'), 'to': m.get('to'),
                           'label': get_text(m, 'label', w, problems),
                           'type': choice(m, 'type', MESSAGE_TYPES, 'call', w, problems),
                           'tone': choice(m, 'tone', MESSAGE_TONES, 'normal', w, problems)})
        items.append(common)
    item_ids = [m['id'] for m in items]
    frames = []
    for i, f in enumerate(raw_frames):
        w = '%s.frames[%d]' % (where, i)
        start = item_index(f.get('start'), item_ids, w, problems, 'start')
        end = item_index(f.get('end'), item_ids, w, problems, 'end')
        frame = {'type': choice(f, 'type', FRAME_TYPES, 'opt', w, problems), 'label': get_text(f, 'label', w, problems),
                 'start': start, 'end': end, 'branches': []}
        if start is None or end is None:
            continue
        if start > end:
            problems.error(w, 'start 不能晚于 end')
            continue
        for j, b in enumerate(f.get('branches', []) or []):
            bw = '%s.branches[%d]' % (w, j)
            before = item_index(b.get('before'), item_ids, bw, problems, 'before') if isinstance(b, dict) else None
            if before is None:
                continue
            if frame['type'] not in ('alt', 'par', 'critical'):
                problems.error(bw, '只有 alt/par/critical 可以分支')
            elif not start < before <= end:
                problems.error(bw, 'before 必须落在 frame 内且不是第一条')
            else:
                frame['branches'].append({'before': before, 'label': get_text(b, 'label', bw, problems)})
        frame['branches'].sort(key=lambda b: b['before'])
        frames.append(frame)
    for i, a in enumerate(frames):
        for j, b in enumerate(frames):
            if i == j:
                continue
            if i < j and (a['start'], a['end']) == (b['start'], b['end']):
                problems.error(where, 'frame 范围 %d–%d 重复；合并成一个 frame' % (a['start'], a['end']))
            if a['start'] < b['start'] <= a['end'] < b['end']:
                problems.error(where, 'frame %d–%d 与 %d–%d 交叉；frame 只能嵌套或分离' % (a['start'], a['end'], b['start'], b['end']))
            for br in a['branches']:
                if a['start'] <= b['start'] < br['before'] <= b['end'] <= a['end']:
                    problems.error(where, '内层 frame 跨过了外层分支分界（第 %d 条）' % br['before'])
    return {'kind': 'sequence', 'status': default, 'participants': parts, 'items': items, 'frames': frames,
            'show_ids': raw.get('show_ids', True) is not False}


def resolve_anchor(anchor, elements, where, problems):
    """锚点写 '图ID:元素ID'，元素 ID 全局唯一时可省略图 ID；只写图 ID 表示整张图。"""
    if not isinstance(anchor, str):
        problems.error(where, '锚点必须是文本')
        return None
    if ':' in anchor:
        did, eid = anchor.split(':', 1)
        if did in elements and eid in elements[did]:
            return did, eid
        problems.error(where, '锚点 %r 找不到对应元素' % anchor)
        return None
    hits = [did for did in elements if anchor in elements[did]]
    if len(hits) == 1:
        return hits[0], anchor
    if len(hits) > 1:
        problems.error(where, '锚点 %r 在多张图中都存在，请写成 图ID:%s' % (anchor, anchor))
        return None
    if anchor in elements:
        return anchor, None
    problems.error(where, '锚点 %r 找不到对应元素' % anchor)
    return None


def normalize_entries(raw, prefix, where, problems, build):
    """findings/tests 既可写字符串，也可写对象；字符串只作为文字条目展示。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.error(where, '必须是数组')
        return []
    entries, ids = [], set()
    for i, item in enumerate(raw):
        w = '%s[%d]' % (where, i)
        if isinstance(item, str) and item.strip():
            entry = {'id': '%s%d' % (prefix, i + 1), 'title': item.strip(), 'plain': True, 'anchors': [], 'refs': []}
        elif isinstance(item, dict):
            entry = build(item, w)
            entry['id'] = item.get('id', '%s%d' % (prefix, i + 1))
            entry['plain'] = False
            if not isinstance(entry['id'], str) or not ID_RE.fullmatch(entry['id']):
                problems.error(w, 'id 必须以字母开头，只含字母、数字、_ 或 -')
        else:
            problems.error(w, '必须是非空字符串或对象')
            continue
        if entry['id'] in ids:
            problems.error(w, 'id %r 重复' % entry['id'])
        ids.add(entry['id'])
        entries.append(entry)
    return entries


def normalize_report(report, problems):
    if not isinstance(report, dict):
        raise InputError('顶层必须是 JSON 对象')
    lang = 'en' if report.get('language') == 'en' else 'zh'
    sources = report.get('sources', {})
    if not isinstance(sources, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in sources.items()):
        problems.error('sources', '必须是 {证据ID: 来源描述} 对象')
        sources = {}
    for key in sources:
        if not ID_RE.fullmatch(key):
            problems.error('sources', '证据 ID %r 必须以字母开头，只含字母、数字、_ 或 -' % key)
    raw_diagrams = report.get('diagrams')
    if not isinstance(raw_diagrams, list) or not 1 <= len(raw_diagrams) <= 12:
        problems.error('diagrams', '需要 1–12 张图')
        raw_diagrams = []
    diagrams, diagram_ids = [], set()
    for i, raw in enumerate(raw_diagrams):
        where = 'diagrams[%d]' % i
        if not isinstance(raw, dict) or not check_id(raw.get('id'), where, problems, diagram_ids, mermaid=False):
            continue
        where = 'diagrams[%d](%s)' % (i, raw['id'])
        if raw['id'] in SECTION_IDS or re.match(r'^[ftv]-', raw['id']):
            problems.error(where, '图 ID %r 与报告内的章节锚点冲突，请换一个' % raw['id'])
            continue
        if raw.get('kind') not in KINDS:
            problems.error(where, 'kind 只能是 %s' % '/'.join(KINDS))
            continue
        body = normalize_sequence(raw, where, sources, problems) if raw['kind'] == 'sequence' \
            else normalize_graph(raw, where, sources, problems)
        if body is None:
            continue
        body.update({'id': raw['id'], 'title': get_text(raw, 'title', where, problems),
                     'basis': get_text(raw, 'basis', where, problems),
                     'question': get_text(raw, 'question', where, problems, False)})
        diagrams.append(body)
    elements = {}
    for d in diagrams:
        table = elements[d['id']] = {}
        if d['kind'] == 'sequence':
            for p in d['participants']:
                table[p['id']] = ('participant', p)
            for m in d['items']:
                table[m['id']] = (m['kind'], m)
        else:
            for kind, key in (('group', 'groups'), ('node', 'nodes'), ('edge', 'edges')):
                for x in d[key]:
                    table[x['id']] = (kind, x)

    def anchors_of(item, key, w):
        raw = item.get(key, [])
        raw = [raw] if isinstance(raw, str) else raw
        if not isinstance(raw, list):
            problems.error(w, '%s 必须是锚点数组' % key)
            return []
        return [a for a in (resolve_anchor(x, elements, w, problems) for x in raw) if a]

    def finding(item, w):
        return {'level': choice(item, 'level', tuple(LEVEL_COLORS), 'risk', w, problems),
                'title': get_text(item, 'title', w, problems), 'anchors': anchors_of(item, 'anchors', w),
                'trigger': get_text(item, 'trigger', w, problems, False),
                'impact': get_text(item, 'impact', w, problems, False),
                'suggestion': get_text(item, 'suggestion', w, problems, False),
                'tradeoff': get_text(item, 'tradeoff', w, problems, False),
                'refs': get_refs(item, sources, w, problems)}

    def test(item, w):
        return {'title': get_text(item, 'title', w, problems), 'anchors': anchors_of(item, 'covers', w),
                'expect': get_text(item, 'expect', w, problems, False),
                'status': choice(item, 'status', TEST_STATES, 'todo', w, problems),
                'command': get_text(item, 'command', w, problems, False), 'refs': get_refs(item, sources, w, problems)}

    lists = {}
    for key in ('decisions', 'limitations'):
        value = report.get(key, []) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            problems.error(key, '必须是字符串数组')
            value = []
        lists[key] = value
    return {'title': get_text(report, 'title', 'report', problems),
            'summary': get_text(report, 'summary', 'report', problems, False),
            'lang': lang, 'texts': TEXT[lang], 'sources': sources, 'diagrams': diagrams, 'elements': elements,
            'findings': normalize_entries(report.get('findings'), 'F', 'findings', problems, finding),
            'tests': normalize_entries(report.get('tests'), 'T', 'tests', problems, test), **lists}


# ---------- 尺寸 ----------

LABEL_SIZE, LABEL_LH = 14, 20
DETAIL_SIZE, DETAIL_LH = 12, 16
REF_SIZE, REF_LH = 11, 15
EDGE_SIZE, EDGE_LH = 12.5, 16
META_SIZE, META_LH = 10.5, 14
GROUP_SIZE, GROUP_HEAD = 12.5, 22


def node_text_height(n):
    h = len(n['lines']) * LABEL_LH
    if n['dlines']:
        h += 4 + len(n['dlines']) * DETAIL_LH
    if n['rlines']:
        h += 6 + len(n['rlines']) * REF_LH
    return h


def measure_node(n):
    shape = n['shape']
    n['lines'], n['dlines'], n['rlines'] = [], [], []
    if shape in ('initial', 'final'):
        n['w'] = n['h'] = 24 if shape == 'initial' else 28
        return
    max_w = 150 if shape == 'decision' else 196
    n['lines'] = wrap(n['label'], LABEL_SIZE, max_w)
    n['dlines'] = wrap(n['detail'], DETAIL_SIZE, max_w) if n['detail'] else []
    n['rlines'] = wrap(' '.join(n['refs']), REF_SIZE, max_w) if n['refs'] else []
    tw = max(block_width(n['lines'], LABEL_SIZE), block_width(n['dlines'], DETAIL_SIZE),
             block_width(n['rlines'], REF_SIZE))
    th = node_text_height(n)
    if shape == 'decision':
        # 文字框 tw×th 要放进菱形：tw/W + th/H ≤ 1
        h = max(68, 2 * th + 24)
        w = max(128, tw / (1 - th / h) + 30)
    elif shape in ('start', 'end'):
        w, h = max(108, tw + 46), max(42, th + 20)
    elif shape == 'store':
        w, h = max(124, tw + 36), max(60, th + 34)
    else:
        w, h = max(124, tw + 34), max(48, th + 26)
    n['w'], n['h'] = math.ceil(w / 2) * 2, math.ceil(h / 2) * 2


def edge_meta(e, default_status, texts, show_id):
    meta = [e['id']] if show_id else []
    if e['status'] != default_status:
        meta.append(texts['status'][e['status']])
    return ' · '.join(meta + e['refs'])


def markers_width(found):
    return sum(text_width(f['id'], 10.5) + 14 for f in found)


def measure_edge(e, default_status, texts, show_id, found):
    """lw/lh 为标签框尺寸；bw 额外包含右侧发现标记，排版按 bw 预留位置。"""
    e['lines'] = wrap(e['label'], EDGE_SIZE, 150) if e['label'] else []
    e['meta'] = edge_meta(e, default_status, texts, show_id)
    if not e['lines'] and not e['meta']:
        e['lw'] = e['lh'] = e['bw'] = 0
        return
    w = max(block_width(e['lines'], EDGE_SIZE), text_width(e['meta'], META_SIZE))
    e['lw'] = math.ceil(w + 16)
    e['lh'] = len(e['lines']) * EDGE_LH + (META_LH if e['meta'] else 0) + 8
    e['bw'] = e['lw'] + math.ceil(markers_width(found))


def group_label(gr, texts):
    tag = texts['group'].get(gr['style'])
    return gr['label'] + ('  · ' + tag if tag else '')


# ---------- Graphviz 排版 ----------

def spacer(w, h):
    """用固定尺寸的空白 HTML 标签让 dot 预留出文字位置，文字由本脚本自己绘制。"""
    return '<<table border="0" cellpadding="0" cellspacing="0"><tr><td width="%d" height="%d" fixedsize="true"></td></tr></table>>' % (
        max(1, math.ceil(w)), max(1, math.ceil(h)))


def parse_point(value, height):
    x, y = value.split(',')[:2]
    return float(x), height - float(y)


def layout_dot(g, texts):
    dot_shapes = {'decision': 'diamond', 'store': 'cylinder', 'initial': 'circle', 'final': 'circle'}
    rounded = ('start', 'end', 'state')
    lines = ['digraph G {',
             'graph [rankdir=%s, nodesep=0.42, ranksep=0.48, newrank=true, pad=0, margin=0];' % g['direction'],
             'node [fixedsize=true, label="", margin=0];', 'edge [arrowsize=0.9];']
    children, members = {}, {}
    for gr in g['groups']:
        children.setdefault(gr['parent'], []).append(gr)
    for n in g['nodes']:
        members.setdefault(n['group'], []).append(n)

    def emit_node(n):
        style = ', style=rounded' if n['shape'] in rounded else ''
        lines.append('"n_%s" [id="n_%s", shape=%s, width=%.4f, height=%.4f%s];' % (
            n['id'], n['id'], dot_shapes.get(n['shape'], 'box'), n['w'] / 72, n['h'] / 72, style))

    def emit_group(gr):
        lines.append('subgraph "cluster_%s" {' % gr['id'])
        label_w = text_width(group_label(gr, texts), GROUP_SIZE) + 20
        lines.append('graph [id="g_%s", label=%s, labeljust=l, labelloc=t, margin=14];' % (gr['id'], spacer(label_w, GROUP_HEAD - 6)))
        for n in members.get(gr['id'], []):
            lines.append('"n_%s";' % n['id'])
        for child in children.get(gr['id'], []):
            emit_group(child)
        lines.append('}')

    # 先按输入顺序声明全部节点，dot 破环时从入口开始遍历；分组里只列成员
    for n in g['nodes']:
        emit_node(n)
    for gr in children.get(None, []):
        emit_group(gr)
    # 两点互连时，dot 会把两条线画在同一位置；把后一方向的边按同方向提交并标记 dir=back，让它们成为平行边
    order = {n['id']: i for i, n in enumerate(g['nodes'])}
    pairs = {(e['from'], e['to']) for e in g['edges']}
    flipped = {e['id'] for e in g['edges']
               if e['from'] != e['to'] and (e['to'], e['from']) in pairs and order[e['from']] > order[e['to']]}
    twins = {e['id'] for e in g['edges'] if e['from'] != e['to'] and (e['to'], e['from']) in pairs}
    for e in g['edges']:
        label = ', label=%s' % spacer(e['bw'] + 6, e['lh'] + 2) if e['lw'] else ''
        if not label and e['id'] in twins:
            # 无标签的互连边也占一点宽度，否则 dot 只把两条线错开几个像素
            label = ', label=%s' % spacer(28, 2)
        a, b = (e['to'], e['from']) if e['id'] in flipped else (e['from'], e['to'])
        lines.append('"n_%s" -> "n_%s" [id="e_%s"%s%s];' % (a, b, e['id'], label, ', dir=back' if e['id'] in flipped else ''))
    lines.append('}')
    result = subprocess.run(['dot', '-Tjson'], input='\n'.join(lines), capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or 'dot 运行失败')
    data = json.loads(result.stdout)
    bb = [float(v) for v in data['bb'].split(',')]
    height = bb[3]
    pos, boxes, routes = {}, {}, {}
    for obj in data.get('objects', []):
        name = obj.get('name', '')
        if name.startswith('cluster_') and obj.get('bb'):
            x1, y1, x2, y2 = (float(v) for v in obj['bb'].split(','))
            boxes[name[8:]] = (x1, height - y2, x2 - x1, y2 - y1)
        elif name.startswith('n_') and obj.get('pos'):
            pos[name[2:]] = parse_point(obj['pos'], height)
    for edge in data.get('edges', []):
        eid = edge.get('id', '')[2:]
        tip, start_tip, points = None, None, []
        for token in edge.get('pos', '').split():
            if token.startswith('e,'):
                tip = parse_point(token[2:], height)
            elif token.startswith('s,'):
                start_tip = parse_point(token[2:], height)
            else:
                points.append(parse_point(token, height))
        if not points:
            continue
        if eid in flipped:
            points.reverse()
            tip = start_tip
        d = 'M %s %s' % (fmt(points[0][0]), fmt(points[0][1]))
        for i in range(1, len(points) - 2, 3):
            d += ' C ' + ' '.join('%s %s' % (fmt(x), fmt(y)) for x, y in points[i:i + 3])
        if tip:
            d += ' L %s %s' % (fmt(tip[0]), fmt(tip[1]))
        routes[eid] = {'d': d, 'label': parse_point(edge['lp'], height) if edge.get('lp') else None,
                       'mid': points[len(points) // 2]}
    return {'width': bb[2], 'height': height, 'pos': pos, 'groups': boxes, 'edges': routes, 'engine': 'Graphviz dot'}


# ---------- 内置分层排版（没有 Graphviz 时使用） ----------

def pav(targets, weights):
    """保序回归：保持左右顺序不变，让各点尽量靠近目标位置。"""
    blocks = []
    for target, weight in zip(targets, weights):
        blocks.append([target, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    result = []
    for value, _, count in blocks:
        result.extend([value] * count)
    return result


def common_prefix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def layout_builtin(g, texts):
    horizontal = g['direction'] == 'LR'
    nodes = {n['id']: n for n in g['nodes']}
    order = {nid: i for i, nid in enumerate(nodes)}
    parent = {gr['id']: gr['parent'] for gr in g['groups']}

    def group_path(gid):
        path = []
        while gid:
            path.insert(0, gid)
            gid = parent[gid]
        return tuple(path)

    def along_size(n):
        return n['h'] if horizontal else n['w']

    def across_size(n):
        return n['w'] if horizontal else n['h']

    # 1. 破环：从入口深度优先遍历，回边在排版时反向
    edges = [e for e in g['edges'] if e['from'] != e['to']]
    loops = [e for e in g['edges'] if e['from'] == e['to']]
    out, indeg = {nid: [] for nid in nodes}, {nid: 0 for nid in nodes}
    for e in edges:
        out[e['from']].append(e)
        indeg[e['to']] += 1
    roots = [nid for nid in nodes if nodes[nid]['shape'] in ('start', 'initial')]
    roots += [nid for nid in nodes if indeg[nid] == 0] + list(nodes)
    state, reverse = {}, set()

    def visit(nid):
        state[nid] = 1
        for e in out[nid]:
            seen = state.get(e['to'], 0)
            if seen == 1:
                reverse.add(e['id'])
            elif seen == 0:
                visit(e['to'])
        state[nid] = 2

    for nid in roots:
        if nid not in state:
            visit(nid)

    def ends(e):
        return (e['to'], e['from']) if e['id'] in reverse else (e['from'], e['to'])

    # 2. 分层：最长路径；没有前驱的非入口节点贴近其后继
    preds, succs = {nid: [] for nid in nodes}, {nid: [] for nid in nodes}
    for e in edges:
        a, b = ends(e)
        succs[a].append(b)
        preds[b].append(a)
    remaining = {nid: len(preds[nid]) for nid in nodes}
    queue = [nid for nid in nodes if remaining[nid] == 0]
    topo, rank = [], {}
    while queue:
        nid = queue.pop(0)
        topo.append(nid)
        rank[nid] = max([rank[p] + 1 for p in preds[nid]] or [0])
        for s in succs[nid]:
            remaining[s] -= 1
            if remaining[s] == 0:
                queue.append(s)
    for nid in reversed(topo):
        if not preds[nid] and succs[nid] and nodes[nid]['shape'] not in ('start', 'initial'):
            rank[nid] = min(rank[s] for s in succs[nid]) - 1

    # 3. 实节点放在偶数层，连线经过的奇数层放虚节点；中间那个虚节点承载连线标签
    vertices = {}
    for nid, n in nodes.items():
        vertices[('n', nid)] = {'along': along_size(n), 'across': across_size(n), 'layer': 2 * rank[nid],
                                'path': group_path(n['group']), 'real': True, 'extra': 0, 'seed': order[nid]}
    for e in loops:
        v = vertices[('n', e['from'])]
        v['extra'] = max(v['extra'], 46 + (e['lh'] if horizontal else e['bw']))
    links, chains = [], {}
    for e in edges:
        a, b = ends(e)
        la, lb = 2 * rank[a], 2 * rank[b]
        path = vertices[('n', a)]['path'][:common_prefix(vertices[('n', a)]['path'], vertices[('n', b)]['path'])]
        prev, chain, mid = ('n', a), [], (la + lb) // 2
        for layer in range(la + 1, lb):
            key = ('d', e['id'], layer)
            is_label = layer == mid and e['lw'] > 0
            vertices[key] = {'along': ((e['lh'] if horizontal else e['bw']) if is_label else 8),
                             'across': ((e['bw'] if horizontal else e['lh']) if is_label else 0),
                             'layer': layer, 'path': path, 'real': False, 'extra': 0,
                             'seed': order[a] + 0.5, 'label': is_label}
            links.append((prev, key))
            prev = key
            chain.append(key)
        links.append((prev, ('n', b)))
        chains[e['id']] = chain
    count = max(v['layer'] for v in vertices.values()) + 1
    layers = [[] for _ in range(count)]
    for key, v in vertices.items():
        layers[v['layer']].append(key)
    up, down = {k: [] for k in vertices}, {k: [] for k in vertices}
    for a, b in links:
        down[a].append(b)
        up[b].append(a)

    # 4. 排序：重心法上下扫描，同一分组保持连续，保留交叉最少的结果
    index = {}

    def assign(layer):
        for i, k in enumerate(layer):
            index[k] = i

    for layer in layers:
        layer.sort(key=lambda k: vertices[k]['seed'])
        assign(layer)

    def sort_layer(layer, neighbors):
        bary = {}
        for k in layer:
            ns = neighbors[k]
            bary[k] = sum(index[n] for n in ns) / len(ns) if ns else index[k]
        group_bary = {}
        for k in layer:
            path = vertices[k]['path']
            for depth in range(1, len(path) + 1):
                group_bary.setdefault(path[:depth], []).append(bary[k])

        def key(k):
            path = vertices[k]['path']
            return tuple(sum(group_bary[path[:d]]) / len(group_bary[path[:d]]) for d in range(1, len(path) + 1)) + (bary[k], index[k])

        layer.sort(key=key)
        assign(layer)

    def crossings():
        total = 0
        for layer in layers[:-1]:
            pairs = [(index[a], index[b]) for a in layer for b in down[a]]
            for i in range(len(pairs)):
                for j in range(i + 1, len(pairs)):
                    if (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1]) < 0:
                        total += 1
        return total

    best = (crossings(), [list(layer) for layer in layers])
    for sweep in range(16):
        if sweep % 2 == 0:
            for i in range(1, count):
                sort_layer(layers[i], up)
        else:
            for i in range(count - 2, -1, -1):
                sort_layer(layers[i], down)
        score = crossings()
        if score < best[0]:
            best = (score, [list(layer) for layer in layers])
    layers = best[1]
    for layer in layers:
        assign(layer)

    # 5. 层内坐标：保序回归迭代，让节点对齐相邻层的连接点，长连线尽量拉直
    def sep(a, b):
        va, vb = vertices[a], vertices[b]
        base = 40 if va['real'] and vb['real'] else (22 if va['real'] or vb['real'] else 10)
        pa, pb = va['path'], vb['path']
        common = common_prefix(pa, pb)
        extra = 16 * (len(pa) - common + len(pb) - common)
        if horizontal:
            extra += GROUP_HEAD * (len(pb) - common)
        return va['along'] / 2 + va['extra'] + base + extra + vb['along'] / 2

    def weight(a, b):
        real = vertices[a]['real'] + vertices[b]['real']
        return 1 if real == 2 else (2 if real == 1 else 8)

    pos = {}
    for layer in layers:
        cur = 0.0
        for i, k in enumerate(layer):
            cur += sep(layer[i - 1], k) if i else 0
            pos[k] = cur
    for sweep in range(30):
        for i in (range(count) if sweep % 2 == 0 else range(count - 1, -1, -1)):
            layer = layers[i]
            if not layer:
                continue
            targets, weights, offsets = [], [], [0.0]
            for j in range(1, len(layer)):
                offsets.append(offsets[-1] + sep(layer[j - 1], layer[j]))
            for k, offset in zip(layer, offsets):
                ns = up[k] + down[k]
                total = sum(weight(k, n) for n in ns)
                target = sum(pos[n] * weight(k, n) for n in ns) / total if ns else pos[k]
                targets.append(target - offset)
                weights.append(total or 0.5)
            for k, value, offset in zip(layer, pav(targets, weights), offsets):
                pos[k] = value + offset

    # 6. 层间坐标：分组在首层前留标题和边距，在末层后留边距
    first, last = {}, {}
    for v in vertices.values():
        if v['real']:
            for gid in v['path']:
                first[gid] = min(first.get(gid, v['layer']), v['layer'])
                last[gid] = max(last.get(gid, v['layer']), v['layer'])
    thickness = [max([vertices[k]['across'] for k in layer] or [0]) for layer in layers]
    centers, cur = [], 0.0
    for i in range(count):
        if i:
            cur += 18 + 16 * sum(1 for gid in last if last[gid] == i - 1)
            cur += sum(16 + (0 if horizontal else GROUP_HEAD) for gid in first if first[gid] == i)
        centers.append(cur + thickness[i] / 2)
        cur += thickness[i]

    # 组外节点若落进分组的层内范围，就连同同层外侧的节点一起推出去
    depth_of = {gr['id']: len(group_path(gr['id'])) for gr in g['groups']}
    for _ in range(6):
        spans = {}
        for gid in sorted(depth_of, key=lambda gid: -depth_of[gid]):
            lo = [pos[k] - v['along'] / 2 for k, v in vertices.items() if v['real'] and gid in v['path']]
            hi = [pos[k] + v['along'] / 2 + v['extra'] for k, v in vertices.items() if v['real'] and gid in v['path']]
            for child in g['groups']:
                if child['parent'] == gid and child['id'] in spans:
                    lo.append(spans[child['id']][0])
                    hi.append(spans[child['id']][1])
            if lo:
                spans[gid] = (min(lo) - 14 - (GROUP_HEAD if horizontal else 0), max(hi) + 14)
        moved = False
        for gid, (lo, hi) in spans.items():
            for layer in layers[first[gid]:last[gid] + 1]:
                members = [i for i, k in enumerate(layer) if gid in vertices[k]['path']]
                for i, k in enumerate(layer):
                    v = vertices[k]
                    if not v['real'] or gid in v['path']:
                        continue
                    left, right = pos[k] - v['along'] / 2, pos[k] + v['along'] / 2 + v['extra']
                    if right <= lo or left >= hi:
                        continue
                    after = i > members[-1] if members else pos[k] >= (lo + hi) / 2
                    if after:
                        shift = hi + 16 - left
                        for k2 in layer[i:]:
                            pos[k2] += shift
                    else:
                        shift = right - (lo - 16)
                        for k2 in layer[:i + 1]:
                            pos[k2] -= shift
                    moved = True
        if not moved:
            break

    def point(k):
        a, c = pos[k], centers[vertices[k]['layer']]
        return (c, a) if horizontal else (a, c)

    # 7. 分组边框：先算内层，再算外层
    rects = {}
    for nid, n in nodes.items():
        x, y = point(('n', nid))
        extra = vertices[('n', nid)]['extra']
        rects[nid] = (x - n['w'] / 2, y - n['h'] / 2, n['w'] + (0 if horizontal else extra), n['h'] + (extra if horizontal else 0))
    label_rects = {}
    for e in edges:
        for key in chains[e['id']]:
            if vertices[key].get('label'):
                x, y = point(key)
                label_rects[e['id']] = (x - e['bw'] / 2, y - e['lh'] / 2, e['bw'], e['lh'])
    boxes = {}
    for gr in sorted(g['groups'], key=lambda gr: -len(group_path(gr['id']))):
        gid = gr['id']
        parts = [rects[nid] for nid in nodes if gid in vertices[('n', nid)]['path']]
        parts += [label_rects[e['id']] for e in edges if e['id'] in label_rects and gid in vertices[chains[e['id']][0]]['path']]
        parts += [boxes[c['id']] for c in g['groups'] if c['parent'] == gid and c['id'] in boxes]
        if not parts:
            continue
        x1 = min(r[0] for r in parts) - 14
        y1 = min(r[1] for r in parts) - 14 - GROUP_HEAD
        x2 = max(r[0] + r[2] for r in parts) + 14
        y2 = max(r[1] + r[3] for r in parts) + 14
        boxes[gid] = (x1, y1, x2 - x1, y2 - y1)
    overlaps = []
    for gid, (bx, by, bw, bh) in boxes.items():
        for nid, (x, y, w, h) in rects.items():
            if gid not in vertices[('n', nid)]['path'] and x < bx + bw and bx < x + w and y < by + bh and by < y + h:
                overlaps.append('分组 %s 的边框压到了组外节点 %s' % (gid, nid))

    # 8. 平移到原点，生成连线路径
    every = list(rects.values()) + list(label_rects.values()) + list(boxes.values())
    for key, v in vertices.items():
        if not v['real']:
            x, y = point(key)
            every.append((x - 6, y - 6, 12, 12))
    for e in loops:
        n = nodes[e['from']]
        x, y = point(('n', e['from']))
        if horizontal:
            every.append((x - e['bw'] / 2, y + n['h'] / 2, e['bw'], 46 + e['lh']))
        else:
            every.append((x + n['w'] / 2, y - e['lh'] / 2, 46 + e['bw'], e['lh']))
    min_x, min_y = min(r[0] for r in every), min(r[1] for r in every)
    width = max(r[0] + r[2] for r in every) - min_x
    height = max(r[1] + r[3] for r in every) - min_y

    def shifted(p):
        return p[0] - min_x, p[1] - min_y

    def boundary(n, offset):
        """节点在 offset（沿层方向的偏移）处到边界的层间距离。"""
        half_across, half_along = across_size(n) / 2, along_size(n) / 2
        offset = min(abs(offset), half_along)
        if n['shape'] == 'decision':
            return half_across * (1 - offset / half_along)
        if n['shape'] in ('initial', 'final'):
            return math.sqrt(max(half_along ** 2 - offset ** 2, 0))
        if n['shape'] in ('start', 'end'):
            r = n['h'] / 2
            if horizontal:
                return n['w'] / 2 - r + math.sqrt(max(r * r - offset ** 2, 0))
            straight = n['w'] / 2 - r
            return half_across if offset <= straight else math.sqrt(max(r * r - (offset - straight) ** 2, 0))
        return half_across

    slots = {}
    for e in edges:
        a, b = ends(e)
        chain = chains[e['id']]
        slots.setdefault((a, 1), []).append((pos[chain[0]], e['id']))
        slots.setdefault((b, -1), []).append((pos[chain[-1]], e['id']))
    offsets = {}
    for (nid, side), items in slots.items():
        items.sort()
        spread = (0.3 if nodes[nid]['shape'] == 'decision' else 0.6) * along_size(nodes[nid])
        span = min(spread, (len(items) - 1) * 18)
        for i, (_, eid) in enumerate(items):
            offsets[(eid, side)] = -span / 2 + (span * i / (len(items) - 1) if len(items) > 1 else 0)

    def port(nid, offset, side):
        n = nodes[nid]
        a = pos[('n', nid)] + offset
        c = centers[vertices[('n', nid)]['layer']] + side * boundary(n, offset)
        return (c, a) if horizontal else (a, c)

    def smooth(points):
        d = 'M %s %s' % (fmt(points[0][0]), fmt(points[0][1]))
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            if horizontal:
                c = (x2 - x1) / 2
                d += ' C %s %s %s %s %s %s' % tuple(fmt(v) for v in (x1 + c, y1, x2 - c, y2, x2, y2))
            else:
                c = (y2 - y1) / 2
                d += ' C %s %s %s %s %s %s' % tuple(fmt(v) for v in (x1, y1 + c, x2, y2 - c, x2, y2))
        return d

    def band_points(chain):
        """虚节点处先竖直（横向图为水平）穿过整层，只在层间空隙转弯，避免擦到同层节点。"""
        result = []
        for k in chain:
            layer = vertices[k]['layer']
            half, a, c = thickness[layer] / 2, pos[k], centers[layer]
            for cc in ((c - half, c + half) if half > 0 else (c,)):
                result.append((cc, a) if horizontal else (a, cc))
        return result

    routes = {}
    for e in edges:
        a, b = ends(e)
        chain = chains[e['id']]
        points = [port(a, offsets[(e['id'], 1)], 1)] + band_points(chain) + [port(b, offsets[(e['id'], -1)], -1)]
        points = [shifted(p) for p in points]
        if e['id'] in reverse:
            points.reverse()
        label = next((shifted(point(k)) for k in chain if vertices[k].get('label')), None)
        routes[e['id']] = {'d': smooth(points), 'label': label, 'mid': points[len(points) // 2]}
    for e in loops:
        n = nodes[e['from']]
        x, y = shifted(point(('n', e['from'])))
        if horizontal:
            by = y + (n['h'] / 2) * ((1 - 9 / (n['w'] / 2)) if n['shape'] == 'decision' else 1)
            d = 'M %s %s C %s %s %s %s %s %s' % tuple(fmt(v) for v in (x - 9, by, x - 40, by + 40, x + 40, by + 40, x + 9, by))
            label = (x, by + 46 + e['lh'] / 2)
        else:
            rx = x + (n['w'] / 2) * ((1 - 9 / (n['h'] / 2)) if n['shape'] == 'decision' else 1)
            d = 'M %s %s C %s %s %s %s %s %s' % tuple(fmt(v) for v in (rx, y - 9, rx + 40, y - 40, rx + 40, y + 40, rx, y + 9))
            label = (x + n['w'] / 2 + 46 + e['bw'] / 2, y)
        routes[e['id']] = {'d': d, 'label': label if e['lw'] else None, 'mid': label}
    return {'width': width, 'height': height, 'engine': texts['builtin'],
            'pos': {nid: shifted(point(('n', nid))) for nid in nodes},
            'groups': {gid: (x - min_x, y - min_y, w, h) for gid, (x, y, w, h) in boxes.items()},
            'edges': routes, 'overlaps': overlaps}


# ---------- SVG 通用部件 ----------

MARGIN = 28
TEXT_DARK, TEXT_MID, TEXT_SOFT = '#0f172a', '#475569', '#64748b'


def text_lines(x, top, lines, size, line_height, fill, anchor='middle', weight=None, extra=''):
    out = []
    for i, line in enumerate(lines):
        base = top + i * line_height + line_height / 2 + size * 0.35
        out.append('<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s"%s%s>%s</text>' % (
            fmt(x), fmt(base), fmt(size), fill, anchor, ' font-weight="%s"' % weight if weight else '', extra, esc(line)))
    return ''.join(out)


def pill(x, y, label, fill, stroke, color, size=10.5, anchor='start', bold=False):
    """小标签：(x, y) 为左端（anchor=start）或右端（anchor=end）的垂直中心。"""
    w, h = text_width(label, size) + 10, 16
    left = x if anchor == 'start' else x - w
    return w, '<rect x="%s" y="%s" width="%s" height="%s" rx="8" fill="%s" stroke="%s"/>%s' % (
        fmt(left), fmt(y - h / 2), fmt(w), h, fill, stroke,
        text_lines(left + w / 2, y - h / 2, [label], size, h, color, weight='700' if bold else None))


def finding_markers(x, y, findings, direction=1):
    """在 (x, y) 处依次排开发现编号；direction=1 向右排，-1 向左排。"""
    out, cur = [], x
    for f in findings:
        color = LEVEL_COLORS.get(f['level'], '#475569')
        w, body = pill(cur, y, f['id'], color, '#ffffff', '#ffffff', anchor='start' if direction > 0 else 'end', bold=True)
        out.append('<a href="#f-%s"><g class="fm"><title>%s %s</title>%s</g></a>' % (esc(f['id']), esc(f['id']), esc(f['title']), body))
        cur += direction * (w + 4)
    return ''.join(out)


def marker_url(prefix, kind, head=None):
    return 'url(#%s-ar-%s-%s)' % (prefix, kind, head or EDGE_TYPES[kind][3])


def arrow_defs(prefix, pairs):
    """pairs 为 (连线类型, 箭头形状) 集合；颜色取自连线类型。"""
    out = []
    for kind, head in sorted(pairs):
        color = EDGE_TYPES[kind][0]
        shape = ('<path d="M1,1.2 L9.6,5 L1,8.8" fill="none" stroke="%s" stroke-width="1.6" stroke-linejoin="round"/>' % color
                 if head == 'open' else '<path d="M0,0.6 L10,5 L0,9.4 Z" fill="%s"/>' % color)
        out.append('<marker id="%s-ar-%s-%s" viewBox="0 0 10 10" refX="9.6" refY="5" markerWidth="10" markerHeight="10" '
                   'markerUnits="userSpaceOnUse" orient="auto">%s</marker>' % (prefix, kind, head, shape))
    return '<defs>%s</defs>' % ''.join(out)


def header(d, texts, width):
    """标题区：标题、本图回答的问题、依据、图类型与默认状态。返回 (svg, 高度)。"""
    inner = width - 2 * MARGIN
    out, y = [], 16
    title = wrap(d['title'], 19, inner)
    out.append(text_lines(MARGIN, y, title, 19, 26, TEXT_DARK, 'start', '700'))
    y += len(title) * 26 + 2
    if d['question']:
        lines = wrap(texts['question'] + d['question'], 13, inner)
        out.append(text_lines(MARGIN, y, lines, 13, 19, '#1e3a8a', 'start'))
        y += len(lines) * 19
    lines = wrap(texts['basis'] + d['basis'], 12, inner)
    out.append(text_lines(MARGIN, y, lines, 12, 18, TEXT_MID, 'start'))
    y += len(lines) * 18 + 6
    meta = texts['kind'][d['kind']]
    if d['status']:
        meta += '  ·  ' + texts['default_status'].format(texts['status'][d['status']])
    out.append(text_lines(MARGIN, y, [meta], 11.5, 18, TEXT_SOFT, 'start'))
    y += 26
    out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#e2e8f0"/>' % (MARGIN, fmt(y), fmt(width - MARGIN), fmt(y)))
    return ''.join(out), y


def legend(prefix, texts, width, edge_kinds=(), message_kinds=(), statuses=(), levels=()):
    """图例只列本图用到的内容。返回 (svg, 高度)。"""
    items = []
    for kind in [k for k in EDGE_TYPES if k in edge_kinds]:
        color, line_w, dash, _ = EDGE_TYPES[kind]
        items.append((34, '<line x1="0" y1="0" x2="28" y2="0" stroke="%s" stroke-width="%s"%s marker-end="%s"/>' % (
            color, line_w, ' stroke-dasharray="%s"' % dash if dash else '', marker_url(prefix, kind)), texts['edge'][kind]))
    for kind in [k for k in MESSAGE_TYPES if k in message_kinds]:
        dash = '5 4' if kind == 'return' else ''
        items.append((34, '<line x1="0" y1="0" x2="28" y2="0" stroke="#4b5563" stroke-width="1.5"%s marker-end="%s"/>' % (
            ' stroke-dasharray="%s"' % dash if dash else '', marker_url(prefix, 'normal', 'solid' if kind == 'call' else 'open')),
            texts['message'][kind]))
    for status in [s for s in STATUSES if s in statuses]:
        dash = {'assumption': '5 3', 'unknown': '2 3'}.get(status, '')
        items.append((26, '<rect x="0" y="-7" width="20" height="14" rx="3" fill="#ffffff" stroke="%s" stroke-width="1.4"%s/>' % (
            STATUS_COLORS[status], ' stroke-dasharray="%s"' % dash if dash else ''), texts['status'][status]))
    for level in [lv for lv in LEVEL_COLORS if lv in levels]:
        items.append((22, '<circle cx="8" cy="0" r="7" fill="%s"/>' % LEVEL_COLORS[level], texts['level'][level]))
    if not items:
        return '', 0
    out, x, y = [], MARGIN, 12
    for sample_w, sample, label in items:
        w = sample_w + text_width(label, 11.5) + 22
        if x + w > width - MARGIN and x > MARGIN:
            x, y = MARGIN, y + 22
        out.append('<g transform="translate(%s %s)">%s%s</g>' % (fmt(x), fmt(y), sample,
                                                                  text_lines(sample_w, -9, [label], 11.5, 18, TEXT_MID, 'start')))
        x += w
    return ''.join(out), y + 14


def tooltip(item, label, report, findings, texts, extra=()):
    lines = [item['id'] + ' · ' + label]
    lines += list(extra)
    lines.append(texts['col_status'] + ': ' + texts['status'][item['status']])
    for ref in item.get('refs', []):
        lines.append(ref + ' — ' + report['sources'].get(ref, ''))
    for f in findings:
        lines.append(f['id'] + ' · ' + texts['level'].get(f['level'], '') + ' · ' + f['title'])
    return '<title>%s</title>' % esc('\n'.join(lines))


# ---------- 流程类图绘制 ----------

def draw_shape(n, x, y):
    fill, stroke, _ = TONES[n['tone']]
    w, h, shape = n['w'], n['h'], n['shape']
    dash = {'assumption': ' stroke-dasharray="6 4"', 'unknown': ' stroke-dasharray="2 3"'}.get(n['status'], '')
    line_w = 2.6 if shape == 'end' else 1.6
    common = 'fill="%s" stroke="%s" stroke-width="%s"%s' % (fill, stroke, line_w, dash)
    left, top = x - w / 2, y - h / 2
    if shape == 'decision':
        return '<polygon points="%s,%s %s,%s %s,%s %s,%s" %s/>' % (
            fmt(x), fmt(top), fmt(left + w), fmt(y), fmt(x), fmt(top + h), fmt(left), fmt(y), common)
    if shape == 'store':
        ry = 7
        return ('<path d="M %s %s A %s %s 0 0 1 %s %s V %s A %s %s 0 0 1 %s %s Z" %s/>'
                '<path d="M %s %s A %s %s 0 0 0 %s %s" fill="none" stroke="%s" stroke-width="1.4"%s/>') % (
            fmt(left), fmt(top + ry), fmt(w / 2), ry, fmt(left + w), fmt(top + ry), fmt(top + h - ry), fmt(w / 2), ry,
            fmt(left), fmt(top + h - ry), common, fmt(left), fmt(top + ry), fmt(w / 2), ry, fmt(left + w), fmt(top + ry), stroke, dash)
    if shape == 'initial':
        return '<circle cx="%s" cy="%s" r="%s" fill="#334155"/>' % (fmt(x), fmt(y), fmt(w / 2))
    if shape == 'final':
        return '<circle cx="%s" cy="%s" r="%s" fill="#ffffff" stroke="#334155" stroke-width="1.8"/><circle cx="%s" cy="%s" r="%s" fill="#334155"/>' % (
            fmt(x), fmt(y), fmt(w / 2), fmt(x), fmt(y), fmt(w / 2 - 5))
    radius = {'start': h / 2, 'end': h / 2, 'state': 14, 'external': 3}.get(shape, 8)
    out = '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" %s/>' % (fmt(left), fmt(top), w, h, fmt(radius), common)
    if shape == 'external':
        out += '<rect x="%s" y="%s" width="%s" height="%s" rx="2" fill="none" stroke="%s" stroke-width="1"%s/>' % (
            fmt(left + 4), fmt(top + 4), w - 8, h - 8, stroke, dash)
    return out


def draw_node(n, x, y, ctx):
    texts, default = ctx['texts'], ctx['diagram']['status']
    found = ctx['findings'].get(n['id'], [])
    label = n['label'] or texts['status'][n['status']]
    out = ['<g class="el node" data-el="%s:%s">' % (ctx['diagram']['id'], n['id']),
           tooltip(n, label, ctx['report'], found, texts, [n['detail']] if n['detail'] else []), draw_shape(n, x, y)]
    if n['lines']:
        top = y - node_text_height(n) / 2 + (3.5 if n['shape'] == 'store' else 0)
        out.append(text_lines(x, top, n['lines'], LABEL_SIZE, LABEL_LH, TEXT_DARK, weight='500'))
        top += len(n['lines']) * LABEL_LH
        if n['dlines']:
            out.append(text_lines(x, top + 4, n['dlines'], DETAIL_SIZE, DETAIL_LH, TEXT_MID))
            top += 4 + len(n['dlines']) * DETAIL_LH
        if n['rlines']:
            out.append(text_lines(x, top + 6, n['rlines'], REF_SIZE, REF_LH, TEXT_SOFT))
    badge = [n['id']] if ctx['diagram']['show_ids'] and n['shape'] not in ('initial', 'final') else []
    if n['status'] != default:
        badge.append(texts['status'][n['status']])
    if n['shape'] == 'decision':
        # 菱形的徽标和发现标记分列顶点两侧
        top = y - n['h'] / 2
        if badge:
            out.append(status_badge(x - 8, top + 10, badge, n['status'], default, 'end'))
        if found:
            out.append(finding_markers(x + 8, top + 10, found, 1))
    else:
        out.append(top_marks(x - n['w'] / 2, y - n['h'] / 2, n['w'], badge, n['status'], default, found))
    out.append('</g>')
    return ''.join(out)


def status_badge(x, y, badge, status, default, anchor='start'):
    color = STATUS_COLORS[status] if status != default else TEXT_MID
    stroke = color if status != default else '#cbd5e1'
    return pill(x, y, ' · '.join(badge), '#ffffff', stroke, color, anchor=anchor)[1]


def top_marks(left, top, width, badge, status, default, found):
    """矩形类元素：左上角放编号徽标，右上角放发现标记；两者重叠时标记顺延到徽标右侧。"""
    out, badge_end = [], left
    if badge:
        bx = left + min(12, width / 6)
        out.append(status_badge(bx, top, badge, status, default))
        badge_end = bx + text_width(' · '.join(badge), 10.5) + 10
    if found:
        start = max(left + width - 10 - markers_width(found) + 4, badge_end + 4)
        out.append(finding_markers(start, top, found, 1))
    return ''.join(out)


def draw_edge_path(e, route, ctx):
    color, line_w, dash, _ = EDGE_TYPES[e['type']]
    found = ctx['findings'].get(e['id'], [])
    return '<g class="el edge" data-el="%s:%s">%s<path d="%s" fill="none" stroke="transparent" stroke-width="10"/>' \
           '<path d="%s" fill="none" stroke="%s" stroke-width="%s"%s stroke-linecap="round" marker-end="%s"/></g>' % (
               ctx['diagram']['id'], e['id'], tooltip(e, e['label'] or texts_edge(ctx, e), ctx['report'], found, ctx['texts']),
               route['d'], route['d'], color, line_w, ' stroke-dasharray="%s"' % dash if dash else '', marker_url(ctx['prefix'], e['type']))


def texts_edge(ctx, e):
    return ctx['texts']['edge'][e['type']]


def draw_edge_label(e, route, ctx):
    found = ctx['findings'].get(e['id'], [])
    at = route['label'] or route['mid']
    if not at:
        return ''
    x, y = at
    out = ['<g class="el elabel" data-el="%s:%s">' % (ctx['diagram']['id'], e['id'])]
    if e['lw']:
        color = EDGE_TYPES[e['type']][0]
        text_color = '#1f2937' if e['type'] in ('normal', 'depends') else color
        left, top = x - e['bw'] / 2, y - e['lh'] / 2
        out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="#ffffff" stroke="%s" stroke-opacity="0.55"/>' % (
            fmt(left), fmt(top), e['lw'], e['lh'], color))
        center = left + e['lw'] / 2
        out.append(text_lines(center, top + 4, e['lines'], EDGE_SIZE, EDGE_LH, text_color))
        if e['meta']:
            out.append(text_lines(center, top + 4 + len(e['lines']) * EDGE_LH, [e['meta']], META_SIZE, META_LH, TEXT_SOFT))
        x = left + e['lw'] + 4
    if found:
        out.append(finding_markers(x, y, found, 1))
    out.append('</g>')
    return ''.join(out)


GROUP_STYLE = {'default': ('#f8fafc', '#cbd5e1', '', '#334155'),
               'trust': ('#fff8f8', '#f08080', '8 5', '#b91c1c'),
               'external': ('#f7f7f8', '#a3adbb', '4 4', '#475569')}


def draw_group_box(gr, box, ctx):
    x, y, w, h = box
    fill, stroke, dash, _ = GROUP_STYLE[gr['style']]
    return '<g class="el group" data-el="%s:%s"><rect x="%s" y="%s" width="%s" height="%s" rx="12" fill="%s" fill-opacity="0.75" stroke="%s" stroke-width="1.3"%s/></g>' % (
        ctx['diagram']['id'], gr['id'], fmt(x), fmt(y), fmt(w), fmt(h), fill, stroke, ' stroke-dasharray="%s"' % dash if dash else '')


def draw_group_label(gr, box, ctx):
    """分组标题画在连线之上并带底色，连线穿过时仍可读。"""
    x, y, w, _ = box
    fill, _, _, color = GROUP_STYLE[gr['style']]
    label = group_label(gr, ctx['texts'])
    found = ctx['findings'].get(gr['id'], [])
    out = ['<g class="el group" data-el="%s:%s">' % (ctx['diagram']['id'], gr['id']),
           '<rect x="%s" y="%s" width="%s" height="18" rx="5" fill="%s" fill-opacity="0.92"/>' % (
               fmt(x + 7), fmt(y + 4), fmt(text_width(label, GROUP_SIZE) + 10), fill),
           text_lines(x + 12, y + 3, [label], GROUP_SIZE, GROUP_HEAD - 2, color, 'start', '600')]
    if found:
        out.append(finding_markers(x + w - 10 - markers_width(found) + 4, y, found, 1))
    out.append('</g>')
    return ''.join(out)


def graph_svg(d, lay, ctx):
    texts, prefix = ctx['texts'], ctx['prefix']
    width = max(lay['width'] + 2 * MARGIN, 680)
    head, head_h = header(d, texts, width)
    used_edges = {e['type'] for e in d['edges']}
    statuses = {x['status'] for x in d['nodes'] + d['edges'] if x['status'] != d['status']}
    levels = {f['level'] for fs in ctx['findings'].values() for f in fs}
    foot, foot_h = legend(prefix, texts, width, used_edges, (), statuses, levels)
    ox, oy = (width - lay['width']) / 2, head_h + 18
    height = oy + lay['height'] + 22 + foot_h + 10
    body = []
    groups = {gr['id']: gr for gr in d['groups']}
    depth = {gid: 0 for gid in groups}
    for gid in groups:
        cur = groups[gid]['parent']
        while cur:
            depth[gid] += 1
            cur = groups[cur]['parent']
    ordered = sorted(lay['groups'], key=lambda gid: depth.get(gid, 0))
    for gid in ordered:
        body.append(draw_group_box(groups[gid], lay['groups'][gid], ctx))
    for e in d['edges']:
        if e['id'] in lay['edges']:
            body.append(draw_edge_path(e, lay['edges'][e['id']], ctx))
    for gid in ordered:
        body.append(draw_group_label(groups[gid], lay['groups'][gid], ctx))
    for n in d['nodes']:
        body.append(draw_node(n, *lay['pos'][n['id']], ctx))
    for e in d['edges']:
        if e['id'] in lay['edges']:
            body.append(draw_edge_label(e, lay['edges'][e['id']], ctx))
    return assemble_svg(d, ctx, width, height, head, foot, oy + lay['height'] + 12,
                        '<g transform="translate(%s %s)">%s</g>' % (fmt(ox), fmt(oy), ''.join(body)),
                        {(k, EDGE_TYPES[k][3]) for k in used_edges})


def assemble_svg(d, ctx, width, height, head, foot, foot_y, body, arrows):
    prefix = ctx['prefix']
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s" role="img" '
            'aria-labelledby="%s-title %s-desc" font-family="%s">'
            '<title id="%s-title">%s</title><desc id="%s-desc">%s</desc>%s'
            '<rect width="100%%" height="100%%" fill="#ffffff"/>%s%s'
            '<g transform="translate(0 %s)">%s</g></svg>') % (
        fmt(width), fmt(height), fmt(width), fmt(height), prefix, prefix, esc(FONT), prefix, esc(d['title']), prefix,
        esc(d['basis']), arrow_defs(prefix, set(arrows) | {('normal', 'solid'), ('normal', 'open')}), head, body, fmt(foot_y), foot)


# ---------- 时序图 ----------

PART_TONE = {'service': 'info', 'actor': 'neutral', 'store': 'data', 'external': 'neutral'}


def span_of(over, index):
    """注释覆盖的参与者序号范围；只覆盖一个参与者时首尾相同。"""
    positions = sorted(index[o] for o in over)
    return positions[0], positions[-1]


def message_style(m):
    """时序消息：线型由 type 决定，颜色由 tone 决定。"""
    color, line_w, dash, _ = EDGE_TYPES[m['tone']]
    if m['type'] == 'return':
        dash = '5 4'
    elif m['tone'] in ('normal', 'main'):
        dash = ''
    return color, line_w, dash, 'solid' if m['type'] == 'call' else 'open'


def sequence_svg(d, ctx):
    texts, prefix, default = ctx['texts'], ctx['prefix'], d['status']
    parts, items, frames = d['participants'], d['items'], d['frames']
    index = {p['id']: i for i, p in enumerate(parts)}
    for p in parts:
        p['lines'] = wrap(p['label'], 13.5, 170)
        p['dlines'] = wrap(p['detail'], 11.5, 170) if p['detail'] else []
        p['rlines'] = wrap(' '.join(p['refs']), 11, 170) if p['refs'] else []
        tw = max(block_width(p['lines'], 13.5), block_width(p['dlines'], 11.5), block_width(p['rlines'], 11))
        p['th'] = len(p['lines']) * 19 + len(p['dlines']) * 15 + (len(p['rlines']) * 15 + 3 if p['rlines'] else 0)
        p['w'] = max(118, tw + 30 + (16 if p['shape'] == 'actor' else 0))
        p['h'] = max(46, p['th'] + 22 + (8 if p['shape'] == 'store' else 0))
    card_h = max(p['h'] for p in parts)
    for m in items:
        meta = [m['id']] if d['show_ids'] else []
        if m['status'] != default:
            meta.append(texts['status'][m['status']])
        m['meta'] = ' · '.join(meta + m['refs'])
        m['lines'] = wrap(m['label'], 12.5, 240)
        m['tw'] = max(block_width(m['lines'], 12.5), text_width(m['meta'], META_SIZE))
        m['th'] = len(m['lines']) * EDGE_LH + (META_LH if m['meta'] else 0)

    # 横向：参与者间距要放得下消息标签、自调用回环和注释
    gaps = [(parts[i]['w'] + parts[i + 1]['w']) / 2 + 40 for i in range(len(parts) - 1)]
    needs, right_extra = [], 0
    for m in items:
        if m['kind'] == 'note':
            a, b = span_of(m['over'], index)
            if a == b:
                if a > 0:
                    needs.append((a - 1, a, m['tw'] / 2 + 34 + parts[a - 1]['w'] / 2))
                if a < len(parts) - 1:
                    needs.append((a, a + 1, m['tw'] / 2 + 34 + parts[a + 1]['w'] / 2))
            else:
                needs.append((a, b, m['tw'] - 20))
        elif m['from'] == m['to']:
            i = index[m['from']]
            if i < len(parts) - 1:
                needs.append((i, i + 1, 56 + m['tw'] + 24))
            else:
                right_extra = max(right_extra, 56 + m['tw'] + 10 - parts[i]['w'] / 2)
        else:
            a, b = sorted((index[m['from']], index[m['to']]))
            needs.append((a, b, m['tw'] + 44))
    for a, b, need in sorted(needs, key=lambda n: n[1] - n[0]):
        have = sum(gaps[a:b])
        if have < need:
            for i in range(a, b):
                gaps[i] += (need - have) / (b - a)
    xs = [MARGIN + 14 + parts[0]['w'] / 2]
    for gap in gaps:
        xs.append(xs[-1] + gap)
    width = max(xs[-1] + parts[-1]['w'] / 2 + max(right_extra, 0) + MARGIN + 14, 680)

    # 纵向：逐行排布，frame 开头留标题、分支处留分隔线
    head, head_h = header(d, texts, width)
    top = head_h + 18
    y = top + card_h + 26
    starts = {}
    for f in frames:
        starts.setdefault(f['start'], []).append(f)
    for i, m in enumerate(items, 1):
        for f in sorted(starts.get(i, []), key=lambda f: -f['end']):
            f['y1'] = y
            y += 30
        for f in frames:
            for br in f['branches']:
                if br['before'] == i:
                    br['y'] = y
                    y += 26
        m['top'] = y
        if m['kind'] == 'note':
            m['h'] = m['th'] + 14
            y += m['h'] + 14
        else:
            m['line_y'] = y + m['th'] + 6
            y = m['line_y'] + (28 if m['from'] == m['to'] else 0) + 16
        for f in sorted([f for f in frames if f['end'] == i], key=lambda f: -f['start']):
            y += 8
            f['y2'] = y
    bottom = y + 10

    # frame 横向范围：包住其中的消息，并包住内层 frame
    def item_span(m):
        if m['kind'] == 'note':
            a, b = (xs[i] for i in span_of(m['over'], index))
            half = max((b - a) / 2 + 24, m['tw'] / 2 + 10)
            return (a + b) / 2 - half, (a + b) / 2 + half
        a, b = sorted((xs[index[m['from']]], xs[index[m['to']]]))
        return a, (b + 56 + m['tw'] if m['from'] == m['to'] else b)

    for f in sorted(frames, key=lambda f: f['end'] - f['start']):
        spans = [item_span(items[i - 1]) for i in range(f['start'], f['end'] + 1)]
        x1, x2 = min(s[0] for s in spans) - 26, max(s[1] for s in spans) + 26
        for g in frames:
            if g is not f and 'x1' in g and f['start'] <= g['start'] and g['end'] <= f['end']:
                x1, x2 = min(x1, g['x1'] - 10), max(x2, g['x2'] + 10)
        tab = text_width(f['type'], 11.5) + 18 + text_width('[%s]' % f['label'], 12) + 20
        f['x1'], f['x2'] = x1, max(x2, x1 + tab)
    width = max(width, max([f['x2'] + MARGIN for f in frames] or [0]))

    body = []
    for f in sorted(frames, key=lambda f: f['start'] - f['end']):
        x1, x2, y1, y2 = f['x1'], f['x2'], f['y1'], f['y2']
        tab_w = text_width(f['type'], 11.5) + 18
        body.append('<g class="frame"><rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="#f8fafc" fill-opacity="0.45" stroke="#94a3b8" stroke-width="1.2"/>' % (
            fmt(x1), fmt(y1), fmt(x2 - x1), fmt(y2 - y1)))
        body.append('<path d="M %s %s H %s V %s L %s %s H %s Z" fill="#e2e8f0" stroke="#94a3b8" stroke-width="1.2"/>' % (
            fmt(x1), fmt(y1), fmt(x1 + tab_w), fmt(y1 + 14), fmt(x1 + tab_w - 6), fmt(y1 + 21), fmt(x1)))
        body.append(text_lines(x1 + 8, y1 + 1, [f['type']], 11.5, 20, '#1e293b', 'start', '700'))
        body.append(text_lines(x1 + tab_w + 8, y1 + 1, ['[%s]' % f['label']], 12, 20, '#334155', 'start'))
        for br in f['branches']:
            body.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#94a3b8" stroke-dasharray="6 4"/>' % (
                fmt(x1), fmt(br['y'] + 2), fmt(x2), fmt(br['y'] + 2)))
            body.append(text_lines(x1 + 8, br['y'] + 4, ['[%s]' % br['label']], 12, 18, '#334155', 'start'))
        body.append('</g>')
    for p, x in zip(parts, xs):
        body.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#cbd5e1" stroke-width="1.4" stroke-dasharray="5 5"/>' % (
            fmt(x), fmt(top + card_h), fmt(x), fmt(bottom)))
    for p, x in zip(parts, xs):
        body.append(draw_participant(p, x, top + card_h - p['h'] / 2, ctx))
    tones, kinds = set(), set()
    for m in items:
        found = ctx['findings'].get(m['id'], [])
        if m['kind'] == 'note':
            a, b = (xs[i] for i in span_of(m['over'], index))
            half = max((b - a) / 2 + 24, m['tw'] / 2 + 10)
            left = (a + b) / 2 - half
            body.append('<g class="el note" data-el="%s:%s">%s<rect x="%s" y="%s" width="%s" height="%s" rx="4" fill="#fefce8" stroke="#d6b23a"/>' % (
                d['id'], m['id'], tooltip(m, m['label'], ctx['report'], found, texts), fmt(left), fmt(m['top']), fmt(2 * half), fmt(m['h'])))
            body.append(text_lines((a + b) / 2, m['top'] + 7, m['lines'], 12.5, EDGE_LH, '#3f3a1e'))
            if m['meta']:
                body.append(text_lines((a + b) / 2, m['top'] + 7 + len(m['lines']) * EDGE_LH, [m['meta']], META_SIZE, META_LH, TEXT_SOFT))
            if found:
                body.append(finding_markers(left + 2 * half + 4, m['top'] + 8, found, 1))
            body.append('</g>')
            continue
        color, line_w, dash, arrow_head = message_style(m)
        tones.add((m['tone'], arrow_head))
        kinds.add(m['type'])
        x1, x2, ly = xs[index[m['from']]], xs[index[m['to']]], m['line_y']
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ''
        body.append('<g class="el message" data-el="%s:%s">%s' % (d['id'], m['id'], tooltip(m, m['label'], ctx['report'], found, texts)))
        if x1 == x2:
            path = 'M %s %s H %s V %s H %s' % (fmt(x1), fmt(ly), fmt(x1 + 40), fmt(ly + 28), fmt(x1 + 2))
            tx, anchor = x1 + 48, 'start'
        else:
            step = 1 if x2 > x1 else -1
            path = 'M %s %s H %s' % (fmt(x1 + step), fmt(ly), fmt(x2 - step))
            tx, anchor = (x1 + x2) / 2, 'middle'
        body.append('<path d="%s" fill="none" stroke="transparent" stroke-width="10"/><path d="%s" fill="none" stroke="%s" stroke-width="%s"%s stroke-linejoin="round" marker-end="%s"/>' % (
            path, path, color, line_w, dash_attr, marker_url(prefix, m['tone'], arrow_head)))
        text_color = '#1f2937' if m['tone'] == 'normal' else color
        body.append(text_lines(tx, m['top'], m['lines'], 12.5, EDGE_LH, text_color, anchor))
        if m['meta']:
            body.append(text_lines(tx, m['top'] + len(m['lines']) * EDGE_LH, [m['meta']], META_SIZE, META_LH, TEXT_SOFT, anchor))
        if found:
            right = tx + (m['tw'] if anchor == 'start' else m['tw'] / 2)
            body.append(finding_markers(right + 6, m['top'] + 9, found, 1))
        body.append('</g>')
    statuses = {x['status'] for x in parts + items if x['status'] != default}
    levels = {f['level'] for fs in ctx['findings'].values() for f in fs}
    tone_kinds = {t for t, _ in tones if t != 'normal'}
    foot, foot_h = legend(prefix, texts, width, tone_kinds, kinds, statuses, levels)
    height = bottom + 16 + foot_h + 10
    return assemble_svg(d, ctx, width, height, head, foot, bottom + 6, ''.join(body),
                        tones | {(k, EDGE_TYPES[k][3]) for k in tone_kinds})


def draw_participant(p, x, y, ctx):
    texts, default = ctx['texts'], ctx['diagram']['status']
    found = ctx['findings'].get(p['id'], [])
    fill, stroke, _ = TONES[PART_TONE[p['shape']]]
    node = {'w': p['w'], 'h': p['h'], 'tone': PART_TONE[p['shape']], 'status': p['status'],
            'shape': {'store': 'store', 'external': 'external'}.get(p['shape'], 'process')}
    out = ['<g class="el participant" data-el="%s:%s">' % (ctx['diagram']['id'], p['id']),
           tooltip(p, p['label'], ctx['report'], found, texts, [p['detail']] if p['detail'] else []), draw_shape(node, x, y)]
    shift = 8 if p['shape'] == 'actor' else 0
    if p['shape'] == 'actor':
        gx, gy = x - p['w'] / 2 + 14, y
        out.append('<g stroke="%s" stroke-width="1.5" fill="none" stroke-linecap="round"><circle cx="%s" cy="%s" r="4"/>'
                   '<path d="M %s %s V %s M %s %s H %s M %s %s L %s %s M %s %s L %s %s"/></g>' % (
                       stroke, fmt(gx), fmt(gy - 9), fmt(gx), fmt(gy - 5), fmt(gy + 5), fmt(gx - 5), fmt(gy - 1), fmt(gx + 5),
                       fmt(gx), fmt(gy + 5), fmt(gx - 4), fmt(gy + 11), fmt(gx), fmt(gy + 5), fmt(gx + 4), fmt(gy + 11)))
    cur = y - p['th'] / 2 + (4 if p['shape'] == 'store' else 0)
    out.append(text_lines(x + shift, cur, p['lines'], 13.5, 19, TEXT_DARK, weight='600'))
    cur += len(p['lines']) * 19
    out.append(text_lines(x + shift, cur, p['dlines'], 11.5, 15, TEXT_MID))
    cur += len(p['dlines']) * 15 + 3
    out.append(text_lines(x + shift, cur, p['rlines'], 11, 15, TEXT_SOFT))
    badge = [p['id']] if ctx['diagram']['show_ids'] else []
    if p['status'] != default:
        badge.append(texts['status'][p['status']])
    out.append(top_marks(x - p['w'] / 2, y - p['h'] / 2, p['w'], badge, p['status'], default, found))
    out.append('</g>')
    return ''.join(out)


# ---------- Mermaid ----------

def mm(value, sequence=False):
    text = str(value)
    if sequence:
        # 时序图里分号会断行，必须先于其他实体转义处理
        text = text.replace(';', '#59;').replace('\n', ' ')
    text = text.replace('"', '#quot;').replace('<', '#lt;').replace('>', '#gt;')
    return text.replace('\n', '<br/>')


def mm_meta(item, texts):
    refs = (' · ' + ' '.join(item['refs'])) if item.get('refs') else ''
    return '    %%%% %s · %s%s' % (item['id'], texts['status'][item['status']], refs)


def mermaid_graph(d, texts):
    lines = ['%% ' + d['title'], '%% ' + texts['basis'] + d['basis']]
    state = d['kind'] == 'state'
    children, members = {}, {}
    for gr in d['groups']:
        children.setdefault(gr['parent'], []).append(gr)
    for n in d['nodes']:
        members.setdefault(n['group'], []).append(n)
    nodes = {n['id']: n for n in d['nodes']}
    group_of = {n['id']: n['group'] for n in d['nodes']}
    if state:
        lines.append('stateDiagram-v2')
        if d['direction'] == 'LR':
            lines.append('    direction LR')
    else:
        lines.append('flowchart ' + d['direction'])
    shapes = {'process': '["%s"]', 'decision': '{"%s"}', 'start': '(["%s"])', 'end': '(["%s"])', 'store': '[("%s")]',
              'external': '[["%s"]]', 'state': '("%s")', 'final': '((("%s")))'}

    def node_text(n):
        return mm(n['label'] + ('\n' + n['detail'] if n['detail'] else ''))

    def state_edge(e, indent):
        a = '[*]' if nodes[e['from']]['shape'] == 'initial' else e['from']
        b = '[*]' if nodes[e['to']]['shape'] == 'final' else e['to']
        return '%s%s --> %s%s' % (indent, a, b, (': ' + mm(e['label']).replace('<br/>', ' ')) if e['label'] else '')

    placed = set()

    def emit(gid, indent):
        for n in members.get(gid, []):
            lines.append(mm_meta(n, texts).replace('    ', indent, 1))
            if state:
                if n['shape'] not in ('initial', 'final'):
                    lines.append('%sstate "%s" as %s' % (indent, node_text(n), n['id']))
            elif n['shape'] == 'initial':
                lines.append('%s%s((" "))' % (indent, n['id']))
            else:
                lines.append(indent + n['id'] + shapes[n['shape']] % node_text(n))
        for gr in children.get(gid, []):
            if state:
                lines.append('%sstate "%s" as %s' % (indent, mm(gr['label']), gr['id']))
                lines.append('%sstate %s {' % (indent, gr['id']))
            else:
                lines.append('%ssubgraph %s["%s"]' % (indent, gr['id'], mm(group_label(gr, texts))))
            emit(gr['id'], indent + '    ')
            if state:
                for e in d['edges']:
                    if group_of[e['from']] == group_of[e['to']] == gr['id'] and e['id'] not in placed:
                        placed.add(e['id'])
                        lines.append(mm_meta(e, texts).replace('    ', indent + '    ', 1) + ' · ' + texts['edge'][e['type']])
                        lines.append(state_edge(e, indent + '    '))
            lines.append(indent + ('}' if state else 'end'))

    emit(None, '    ')
    link_styles = []
    for e in d['edges']:
        if e['id'] in placed:
            continue
        lines.append(mm_meta(e, texts) + ' · ' + texts['edge'][e['type']])
        if state:
            lines.append(state_edge(e, '    '))
        else:
            arrow = {'main': '==>', 'normal': '-->', 'data': '-->'}.get(e['type'], '-.->')
            label = '|"%s"|' % mm(e['label']) if e['label'] else ''
            lines.append('    %s %s%s %s' % (e['from'], arrow, label, e['to']))
            color, line_w, dash, _ = EDGE_TYPES[e['type']]
            link_styles.append('    linkStyle %d stroke:%s,stroke-width:%spx%s' % (
                len(link_styles), color, fmt(line_w), (',stroke-dasharray:' + dash) if dash else ''))
    lines += link_styles
    for tone, (fill, stroke, _) in TONES.items():
        ids = [n['id'] for n in d['nodes'] if n['tone'] == tone and n['shape'] not in ('initial', 'final')]
        if ids:
            lines.append('    classDef tone_%s fill:%s,stroke:%s' % (tone, fill, stroke))
            lines.append('    class %s tone_%s' % (','.join(ids), tone))
    for status, dash in (('assumption', '6 4'), ('unknown', '2 3')):
        ids = [n['id'] for n in d['nodes'] if n['status'] == status and n['shape'] not in ('initial', 'final')]
        if ids:
            lines.append('    classDef st_%s stroke-dasharray:%s' % (status, dash))
            lines.append('    class %s st_%s' % (','.join(ids), status))
    return '\n'.join(lines) + '\n'


def mermaid_sequence(d, texts):
    lines = ['%% ' + d['title'], '%% ' + texts['basis'] + d['basis'], 'sequenceDiagram']
    for p in d['participants']:
        lines.append(mm_meta(p, texts))
        lines.append('    %s %s as %s' % ('actor' if p['shape'] == 'actor' else 'participant', p['id'], mm(p['label'], True)))
    depth = 0
    for i, m in enumerate(d['items'], 1):
        for f in d['frames']:
            for br in f['branches']:
                if br['before'] == i:
                    word = {'alt': 'else', 'par': 'and', 'critical': 'option'}[f['type']]
                    lines.append('    ' * depth + word + ' ' + mm(br['label'], True))
        for f in sorted([f for f in d['frames'] if f['start'] == i], key=lambda f: -f['end']):
            lines.append('    ' * (depth + 1) + f['type'] + ' ' + mm(f['label'], True))
            depth += 1
        indent = '    ' * (depth + 1)
        lines.append(mm_meta(m, texts).replace('    ', indent, 1))
        if m['kind'] == 'note':
            lines.append('%sNote over %s: %s' % (indent, ','.join(m['over']), mm(m['label'], True)))
        else:
            arrow = {'call': '->>', 'async': '-)', 'return': '-->>'}[m['type']]
            lines.append('%s%s%s%s: %s' % (indent, m['from'], arrow, m['to'], mm(m['label'], True)))
        for f in [f for f in d['frames'] if f['end'] == i]:
            depth -= 1
            lines.append('    ' * (depth + 1) + 'end')
    return '\n'.join(lines) + '\n'


# ---------- 结构检查 ----------

LIMIT_RE = re.compile(r'\d|上限|最多|至多|不超过|直到|超时|预算|max|limit|until|timeout|budget|at most', re.I)
LINT = {
    'zh': {
        'no_entry': '找不到入口：没有 start/initial 节点，也没有无前驱的节点',
        'unreachable': '节点 {0} 从入口不可达',
        'dead_end': '节点 {0} 没有出边，也不是终点；流程在这里中断了吗？',
        'decision_few': '判断 {0} 只有 {1} 条出边；分支是否画全？',
        'decision_unlabeled': '判断 {0} 的出边 {1} 没有写条件',
        'decision_dup': '判断 {0} 有多条出边写了相同条件“{1}”',
        'no_exit': '回环 {0} 在图中没有出口边；补上退出条件或说明由外部停止',
        'retry_limit': '{0} 没有写上限、预算或终止条件',
        'into_initial': '连线 {0} 指向初始状态 {1}',
        'from_final': '连线 {0} 从终止状态 {1} 出发',
        'fact_no_ref': '{1} 个元素标为事实但没有证据编号（{0}）；关键节点和连线应补上证据',
        'long_label': '{0} 的标签有 {1} 个字符，建议精简并把细节放进 detail',
        'dfd_unlabeled': '数据流 {0} 没有写数据名称',
        'self_return': '消息 {0} 是发给自己的返回消息',
        'confirmed_no_ref': '发现 {0} 标为已证实问题，但没有证据编号',
    },
    'en': {
        'no_entry': 'No entry: no start/initial node and no node without predecessors',
        'unreachable': 'Node {0} is unreachable from the entry',
        'dead_end': 'Node {0} has no outgoing edge and is not an end; does the flow stop here?',
        'decision_few': 'Decision {0} has only {1} outgoing edge(s); are all branches drawn?',
        'decision_unlabeled': 'Edge {1} out of decision {0} has no condition label',
        'decision_dup': 'Decision {0} has several outgoing edges labelled “{1}”',
        'no_exit': 'Loop {0} has no exit edge in the diagram; add the exit condition or the external stop',
        'retry_limit': '{0} states no limit, budget or stop condition',
        'into_initial': 'Edge {0} points into initial state {1}',
        'from_final': 'Edge {0} leaves final state {1}',
        'fact_no_ref': '{1} element(s) marked as fact have no evidence reference ({0}); add evidence to key nodes and edges',
        'long_label': 'Label of {0} has {1} characters; shorten it and move detail into detail',
        'dfd_unlabeled': 'Data flow {0} does not name the data',
        'self_return': 'Message {0} is a return sent to itself',
        'confirmed_no_ref': 'Finding {0} is marked confirmed but has no evidence reference',
    },
}


def strongly_connected(nodes, succs):
    index, low, stack, on, result, counter = {}, {}, [], set(), [], [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in succs[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            result.append(comp)

    for v in nodes:
        if v not in index:
            visit(v)
    return result


def lint(d, report):
    msg = LINT[report['lang']]
    out = []
    if d['kind'] == 'sequence':
        items = d['participants'] + d['items']
    else:
        pseudo = {n['id'] for n in d['nodes'] if n['shape'] in ('initial', 'final')}
        cited = {n['id'] for n in d['nodes'] if n['refs']}
        items = [n for n in d['nodes'] if n['id'] not in pseudo]
        # 连线的证据可以由起点节点承担：分支条件就写在起点节点引用的那段源码里
        items += [e for e in d['edges'] if e['from'] not in pseudo and e['to'] not in pseudo and e['from'] not in cited]
    missing = [x['id'] for x in items if x['status'] == 'fact' and not x['refs']]
    if missing:
        shown = '、'.join(missing[:8]) + (' …' if len(missing) > 8 else '')
        out.append(msg['fact_no_ref'].format(shown, len(missing)))
    for x in items:
        if len(x.get('label', '')) > 40:
            out.append(msg['long_label'].format(x['id'], len(x['label'])))
    if d['kind'] == 'sequence':
        for m in d['items']:
            if m['kind'] == 'message' and m['type'] == 'return' and m['from'] == m['to']:
                out.append(msg['self_return'].format(m['id']))
        for f in d['frames']:
            if f['type'] == 'loop' and not LIMIT_RE.search(f['label']):
                out.append(msg['retry_limit'].format('loop [%s]' % f['label']))
        return out
    nodes = {n['id']: n for n in d['nodes']}
    succs = {nid: [] for nid in nodes}
    out_edges = {nid: [] for nid in nodes}
    for e in d['edges']:
        succs[e['from']].append(e['to'])
        out_edges[e['from']].append(e)
        if e['type'] == 'retry' and not LIMIT_RE.search(e['label'] + nodes[e['from']]['label'] + nodes[e['to']]['label']):
            out.append(msg['retry_limit'].format(e['id']))
        if d['kind'] == 'dfd' and not e['label']:
            out.append(msg['dfd_unlabeled'].format(e['id']))
        if nodes[e['to']]['shape'] == 'initial':
            out.append(msg['into_initial'].format(e['id'], e['to']))
        if nodes[e['from']]['shape'] == 'final':
            out.append(msg['from_final'].format(e['id'], e['from']))
    for n in d['nodes']:
        if n['shape'] != 'decision':
            continue
        edges = out_edges[n['id']]
        if len(edges) < 2:
            out.append(msg['decision_few'].format(n['id'], len(edges)))
        labels = [e['label'] for e in edges if e['label']]
        for e in edges:
            if not e['label']:
                out.append(msg['decision_unlabeled'].format(n['id'], e['id']))
        for label in sorted({x for x in labels if labels.count(x) > 1}):
            out.append(msg['decision_dup'].format(n['id'], label))
    if d['kind'] not in ('flowchart', 'state'):
        return out
    has_in = {e['to'] for e in d['edges'] if e['from'] != e['to']}
    entries = [nid for nid, n in nodes.items() if n['shape'] in ('start', 'initial')] or \
              [nid for nid in nodes if nid not in has_in]
    if not entries:
        out.append(msg['no_entry'])
    seen, stack = set(entries), list(entries)
    while stack:
        for nxt in succs[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    terminal = ('end', 'final', 'store', 'external')
    for nid, n in nodes.items():
        if entries and nid not in seen:
            out.append(msg['unreachable'].format(nid))
        if not succs[nid] and n['shape'] not in terminal:
            out.append(msg['dead_end'].format(nid))
    for comp in strongly_connected(list(nodes), succs):
        members = set(comp)
        looping = len(comp) > 1 or comp[0] in succs[comp[0]]
        if not looping or any(nodes[c]['shape'] in terminal for c in comp):
            continue
        if not any(t not in members for c in comp for t in succs[c]):
            out.append(msg['no_exit'].format('→'.join(sorted(comp, key=list(nodes).index))))
    return out


# ---------- HTML 报告 ----------

CSS = """
:root{--ink:#0f172a;--mid:#475569;--soft:#64748b;--line:#e2e8f0;--bg:#f4f6f9;--card:#fff;--blue:#1d4ed8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 %s}
main{max-width:1320px;margin:0 auto;padding:28px 24px 64px}
header.top,section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 26px;margin:0 0 20px}
h1{font-size:26px;line-height:1.3;margin:0 0 8px}
h2{font-size:19px;margin:0 0 14px}
h3{font-size:16px;margin:0}
p{margin:6px 0}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.summary{font-size:16px;color:#1e293b;max-width:900px}
.stats,.nav{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:13px;color:var(--mid);background:#f8fafc}
.chip b{color:var(--ink)}
.dot{width:9px;height:9px;border-radius:50%%;display:inline-block}
.diagram{overflow:auto;border-radius:10px}
.diagram svg{display:block;max-width:100%%;height:auto;margin:0 auto}
.under{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px;font-size:13px;color:var(--soft)}
details{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
summary{cursor:pointer;color:var(--mid);font-size:14px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;border-radius:8px;padding:14px;font-size:13px;line-height:1.5}
table{border-collapse:collapse;width:100%%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{color:var(--mid);font-weight:600;background:#f8fafc}
.findings{display:grid;gap:14px}
.finding{border:1px solid var(--line);border-left:5px solid var(--lv);border-radius:10px;padding:14px 18px;scroll-margin-top:20px}
.finding:target{box-shadow:0 0 0 3px #fde68a}
.fhead{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:6px}
.fid{background:var(--lv);color:#fff;border-radius:999px;padding:0 9px;font-weight:700;font-size:13px}
.lv{color:var(--lv);font-weight:600;font-size:13px}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:8px 0 0}
.kv dt{color:var(--soft);font-size:13.5px}
.kv dd{margin:0}
.anchor{display:inline-block;border:1px solid #bfdbfe;background:#eff6ff;border-radius:6px;padding:0 7px;margin:0 4px 4px 0;font-size:13px}
.ref{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 6px;margin:0 4px 2px 0;font-size:12.5px;color:var(--mid);background:#f8fafc}
.st{font-size:12.5px;font-weight:600;border-radius:999px;padding:1px 9px;white-space:nowrap}
.st-pass{background:#dcfce7;color:#166534}.st-fail{background:#fee2e2;color:#991b1b}
.st-todo{background:#f1f5f9;color:#475569}.st-blocked{background:#fef3c7;color:#92400e}
ul.plain{margin:0;padding-left:20px}
ul.plain li{margin:4px 0}
.warn li{color:#92400e}
.foot{color:var(--soft);font-size:13px;text-align:center}
.el:hover{filter:drop-shadow(0 0 3px rgba(37,99,235,.45))}
.hl{animation:pulse 1.2s ease-in-out 2}
@keyframes pulse{0%%,100%%{filter:none}50%%{filter:drop-shadow(0 0 6px #f59e0b) drop-shadow(0 0 3px #f59e0b)}}
@media print{body{background:#fff}main{padding:0}section,header.top{break-inside:avoid;border:none;padding:8px 0}details{display:none}}
""" % FONT

JS = """
document.addEventListener('click', function (ev) {
  var a = ev.target.closest('a.anchor');
  if (!a) return;
  ev.preventDefault();
  var key = a.getAttribute('data-el');
  var hits = document.querySelectorAll('svg [data-el="' + key + '"]');
  if (!hits.length) {
    var s = document.getElementById(key.split(':')[0]);
    if (s) s.scrollIntoView({behavior: 'smooth', block: 'start'});
    return;
  }
  hits[0].scrollIntoView({behavior: 'smooth', block: 'center'});
  hits.forEach(function (h) { h.classList.remove('hl'); void h.getBoundingClientRect(); h.classList.add('hl'); });
});
"""


def anchor_html(anchor, report):
    did, eid = anchor
    if eid is None:
        title = next(d['title'] for d in report['diagrams'] if d['id'] == did)
        return '<a class="anchor" href="#%s" data-el="%s">%s</a>' % (did, did, esc(title))
    kind, item = report['elements'][did][eid]
    label = item.get('label') or report['texts']['element_kinds'][kind]
    short = label if len(label) <= 18 else label[:17] + '…'
    return '<a class="anchor" href="#%s" data-el="%s:%s" title="%s">%s · %s</a>' % (
        did, did, eid, esc(did + ':' + eid + ' ' + label), esc(eid), esc(short))


def refs_html(refs, report):
    return ''.join('<span class="ref" title="%s">%s</span>' % (esc(report['sources'].get(r, '')), esc(r)) for r in refs)


def finding_html(f, report):
    texts = report['texts']
    if f['plain']:
        return '<article class="finding" id="f-%s" style="--lv:#94a3b8"><div class="fhead"><span class="fid">%s</span><h3>%s</h3></div></article>' % (
            esc(f['id']), esc(f['id']), esc(f['title']))
    rows = []
    if f['anchors']:
        rows.append((texts['col_where'], ''.join(anchor_html(a, report) for a in f['anchors'])))
    for key in ('trigger', 'impact', 'suggestion', 'tradeoff'):
        if f[key]:
            rows.append((texts[key], esc(f[key])))
    if f['refs']:
        rows.append((texts['col_refs'], refs_html(f['refs'], report)))
    return ('<article class="finding" id="f-%s" style="--lv:%s"><div class="fhead"><span class="fid">%s</span>'
            '<span class="lv">%s</span><h3>%s</h3></div><dl class="kv">%s</dl></article>') % (
        esc(f['id']), LEVEL_COLORS[f['level']], esc(f['id']), esc(texts['level'][f['level']]), esc(f['title']),
        ''.join('<dt>%s</dt><dd>%s</dd>' % row for row in rows))


def elements_table(d, report, findings_by_el):
    texts = report['texts']
    rows = []
    for eid, (kind, item) in report['elements'][d['id']].items():
        if kind == 'group':
            continue
        label = item.get('label') or texts['element_kinds'][kind]
        if kind == 'edge':
            label = '%s → %s%s' % (item['from'], item['to'], ('：' + item['label']) if item['label'] else '')
        elif kind == 'message':
            label = '%s → %s：%s' % (item['from'], item['to'], item['label'])
        found = ' '.join('<a href="#f-%s">%s</a>' % (esc(f['id']), esc(f['id'])) for f in findings_by_el.get((d['id'], eid), []))
        rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(eid), esc(texts['element_kinds'][kind]), esc(label), esc(texts['status'][item['status']]),
            refs_html(item['refs'], report), found))
    return '<table><thead><tr><th>%s</th><th></th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>' % (
        texts['col_id'], texts['col_label'], texts['col_status'], texts['col_refs'], texts['findings'], ''.join(rows))


def render_html(report, rendered, warnings, findings_by_el):
    texts = report['texts']
    findings, tests = report['findings'], report['tests']
    stats = ['<span class="chip"><b>%s</b></span>' % esc(texts['diagrams'].format(len(rendered)))]
    if findings:
        stats.append('<span class="chip"><b>%s</b></span>' % esc(texts['n_findings'].format(len(findings))))
        for level, color in LEVEL_COLORS.items():
            count = sum(1 for f in findings if not f['plain'] and f['level'] == level)
            if count:
                stats.append('<span class="chip"><span class="dot" style="background:%s"></span>%s %d</span>' % (color, esc(texts['level'][level]), count))
    if tests:
        stats.append('<span class="chip"><b>%s</b></span>' % esc(texts['n_tests'].format(len(tests))))
        for state in TEST_STATES:
            count = sum(1 for t in tests if not t['plain'] and t['status'] == state)
            if count:
                stats.append('<span class="chip">%s %d</span>' % (esc(texts['test'][state]), count))
    nav = ''.join('<a class="chip" href="#%s">%s</a>' % (d['id'], esc(d['title'])) for d, _, _, _ in rendered)
    parts = ['<header class="top"><h1>%s</h1>%s<div class="stats">%s</div><div class="nav">%s</div></header>' % (
        esc(report['title']), '<p class="summary">%s</p>' % esc(report['summary']) if report['summary'] else '',
        ''.join(stats), nav)]
    for d, svg, mermaid, engine in rendered:
        own = sorted({f['id']: f for (did, _), fs in findings_by_el.items() if did == d['id'] for f in fs}.values(),
                     key=lambda f: [x['id'] for x in findings].index(f['id']))
        own += [f for f in findings if (d['id'], None) in f['anchors'] and f not in own]
        links = ''.join('<a class="chip" href="#f-%s"><span class="dot" style="background:%s"></span>%s %s</a>' % (
            esc(f['id']), LEVEL_COLORS[f['level']], esc(f['id']), esc(f['title'])) for f in own)
        parts.append(('<section id="%s"><div class="diagram">%s</div><div class="under">%s</div>'
                      '<details><summary>%s</summary>%s</details><details><summary>%s</summary><pre>%s</pre></details>'
                      '<div class="under">%s<a href="%s.svg">%s.svg</a><a href="%s.mmd">%s.mmd</a>%s</div></section>') % (
            d['id'], svg, links, texts['elements'], elements_table(d, report, findings_by_el), texts['mermaid'], esc(mermaid),
            texts['files'], d['id'], d['id'], d['id'], d['id'], (' · ' + esc(texts['engine'].format(engine))) if engine else ''))
    if findings:
        parts.append('<section id="findings"><h2>%s</h2><div class="findings">%s</div></section>' % (
            texts['findings'], ''.join(finding_html(f, report) for f in findings)))
    if tests:
        rows = []
        for t in tests:
            if t['plain']:
                rows.append('<tr><td>%s</td><td colspan="4">%s</td></tr>' % (esc(t['id']), esc(t['title'])))
                continue
            command = '<div><code>%s</code></div>' % esc(t['command']) if t['command'] else ''
            rows.append('<tr id="t-%s"><td>%s</td><td>%s%s</td><td>%s</td><td>%s</td><td><span class="st st-%s">%s</span></td></tr>' % (
                esc(t['id']), esc(t['id']), esc(t['title']), command, ''.join(anchor_html(a, report) for a in t['anchors']),
                esc(t['expect']), t['status'], esc(texts['test'][t['status']])))
        parts.append('<section id="tests"><h2>%s</h2><table><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table></section>' % (
            texts['tests'], texts['col_id'], texts['col_scenario'], texts['col_covers'], texts['col_expect'], texts['col_state'], ''.join(rows)))
    for key in ('decisions', 'limitations'):
        if report[key]:
            parts.append('<section><h2>%s</h2><ul class="plain">%s</ul></section>' % (
                texts[key], ''.join('<li>%s</li>' % esc(v) for v in report[key])))
    parts.append('<section id="lint"><h2>%s</h2>%s</section>' % (
        texts['lint'], '<ul class="plain warn">%s</ul>' % ''.join('<li>%s</li>' % esc(w) for w in warnings) if warnings else '<p>%s</p>' % texts['lint_ok']))
    if report['sources']:
        parts.append('<section id="evidence"><h2>%s</h2><table><tbody>%s</tbody></table></section>' % (
            texts['evidence'], ''.join('<tr><td><b>%s</b></td><td>%s</td></tr>' % (esc(k), esc(v)) for k, v in report['sources'].items())))
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><main>%s<p class="foot">render_review.py</p></main><script>%s</script></body></html>') % (
        'en' if report['lang'] == 'en' else 'zh-CN', esc(report['title']), CSS, ''.join(parts), JS)


# ---------- 入口 ----------

def render(raw, out, engine='auto', strict=False):
    problems = Problems()
    report = normalize_report(raw, problems)
    problems.raise_if_any()
    skill_dir = Path(__file__).resolve().parent.parent
    target = out.resolve()
    if target == skill_dir or skill_dir in target.parents:
        raise InputError('输出目录在技能目录内；请改用任务自己的输出目录')
    dot = shutil.which('dot')
    if engine == 'dot' and not dot:
        raise InputError('找不到 Graphviz 的 dot 命令；改用 --engine builtin，或在用户同意后安装 Graphviz')
    texts = report['texts']
    findings_by_el, referenced = {}, set()
    for f in report['findings']:
        for did, eid in f['anchors']:
            if eid:
                findings_by_el.setdefault((did, eid), []).append(f)
                referenced.add((did, eid))
    for t in report['tests']:
        referenced.update((did, eid) for did, eid in t['anchors'] if eid)
    rendered = []
    for d in report['diagrams']:
        ctx = {'report': report, 'texts': texts, 'diagram': d, 'prefix': 'v-' + d['id'],
               'findings': {eid: fs for (did, eid), fs in findings_by_el.items() if did == d['id']}}
        engine_name = ''
        if d['kind'] == 'sequence':
            svg = sequence_svg(d, ctx)
            mermaid = mermaid_sequence(d, texts)
        else:
            for n in d['nodes']:
                measure_node(n)
            for e in d['edges']:
                measure_edge(e, d['status'], texts, (d['id'], e['id']) in referenced, ctx['findings'].get(e['id'], []))
            lay = None
            if dot and engine != 'builtin':
                try:
                    lay = layout_dot(d, texts)
                except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                    problems.warn(d['id'], 'Graphviz 排版失败，改用内置排版：%s' % exc)
            if lay is None:
                lay = layout_builtin(d, texts)
                for message in lay['overlaps']:
                    problems.warn(d['id'], message)
            engine_name = lay['engine']
            svg = graph_svg(d, lay, ctx)
            mermaid = mermaid_graph(d, texts)
        for message in lint(d, report):
            problems.warn(d['id'], message)
        rendered.append((d, svg, mermaid, engine_name))
    msg = LINT[report['lang']]['confirmed_no_ref']
    for f in report['findings']:
        if not f['plain'] and f['level'] == 'confirmed' and not f['refs']:
            problems.warn('findings', msg.format(f['id']))
    if strict and problems.warnings:
        raise InputError('--strict：存在结构警告\n' + '\n'.join(problems.warnings))
    out.mkdir(parents=True, exist_ok=True)
    for d, svg, mermaid, _ in rendered:
        (out / (d['id'] + '.svg')).write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + svg + '\n', encoding='utf-8')
        (out / (d['id'] + '.mmd')).write_text(mermaid, encoding='utf-8')
    (out / 'index.html').write_text(render_html(report, rendered, problems.warnings, findings_by_el), encoding='utf-8')
    (out / 'review.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return out / 'index.html', problems.warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('input', type=Path, help='审查输入 JSON')
    parser.add_argument('--out', type=Path, required=True, help='输出目录（不要放在技能目录或业务源码目录）')
    parser.add_argument('--engine', choices=('auto', 'dot', 'builtin'), default='auto',
                        help='排版引擎：auto 有 Graphviz 时用 dot，否则用内置排版')
    parser.add_argument('--strict', action='store_true', help='有结构警告时视为失败')
    args = parser.parse_args()
    try:
        raw = json.loads(args.input.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        print('render_review: JSON 格式错误（第 %d 行第 %d 列）：%s' % (exc.lineno, exc.colno, exc.msg), file=sys.stderr)
        return 2
    except OSError as exc:
        print('render_review: 无法读取输入：%s' % exc, file=sys.stderr)
        return 2
    try:
        index, warnings = render(raw, args.out, args.engine, args.strict)
    except InputError as exc:
        print('render_review: 输入有误，请一次改完：\n' + str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print('render_review: 写出失败：%s' % exc, file=sys.stderr)
        return 2
    for warning in warnings:
        print('警告 ' + warning, file=sys.stderr)
    print(str(index.resolve()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
