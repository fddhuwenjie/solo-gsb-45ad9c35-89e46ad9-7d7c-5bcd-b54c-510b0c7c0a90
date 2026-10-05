# -*- coding: utf-8 -*-
"""
针织行式编译器（纯函数模块，不依赖 Flask）

针法表（消耗 / 产出）：
    k      1 / 1   下针
    p      1 / 1   上针
    yo     0 / 1   挂线（空加针）
    k2tog  2 / 1   两针并一针

语法：
    一行 = 多个针法项
    项   = k | p | yo | k2tog
         | "(" 行 ")" xN      嵌套重复
         | "*" 行 "*"         行末重复（必须位于整行末尾，不允许嵌套）
         | "@" 名称           引用片段（在定义区以「名称 = 针法序列」声明）
    空白、逗号（, ，）、顿号（、）仅作分隔。

片段定义区（可选）：
    每行一条：名称 = 针法序列。片段体内可写分组与 @引用（允许相互引用、
    前向引用），但不能写行末 *…*。只有被行源码（直接或间接）引用的片段
    才会被检查：未被引用的片段即使写错也不影响任何行。
    缺失引用 / 循环引用 / 片段语法错误都定位到「使用它的行与引用位置」，
    并附带所涉定义在定义区中的行列位置；后续行按原规则阻断。

行末重复的语义：
    若 "*…*" 内部完整执行一次的消耗量 C <= 0，报错（无法确定次数）；
    令 R = 当前剩余未编织针数，若 R % C != 0，报错并给出余针 R；
    否则恰好重复 R / C 次。整行消耗必须精确等于上一行产出的针数，
    既不能截断也不能补针。片段按展开后的实际消耗计入 C 与整行针数。
"""
import unicodedata

# ---- 限制（防止恶意/手滑展开出超大结果）-----------------------------------
MAX_CAST_ON = 2000
MAX_ROWS = 500
MAX_LINE_LEN = 2000
MAX_GROUP_REPEAT = 999
MAX_STITCHES_PER_ROW = 20000
MAX_DEF_LINES = 200      # 定义区最多行数
MAX_DEF_NAME_LEN = 40    # 片段名最长字符数

# ---- 针法定义 -------------------------------------------------------------
STITCHES = {
    'k':     {'consume': 1, 'produce': 1, 'name': 'k（下针）'},
    'p':     {'consume': 1, 'produce': 1, 'name': 'p（上针）'},
    'yo':    {'consume': 0, 'produce': 1, 'name': 'yo（挂线）'},
    'k2tog': {'consume': 2, 'produce': 1, 'name': 'k2tog（二并一）'},
}

# 全角符号按 1:1 归一化，字符位置保持不变
_NORMALIZE = {'（': '(', '）': ')', '＊': '*', '，': ',', '、': ','}


def _norm_char(c):
    """单字符归一化：全角→半角（仅当结果为 1 个字符，保证位置不错位）。"""
    c = _NORMALIZE.get(c, c)
    if not c.isascii():
        n = unicodedata.normalize('NFKC', c)
        if len(n) == 1:
            c = n
    return c


def _norm_window(src, i, size):
    """从 i 起取 size 个源字符并逐字归一化（每个源字符仍占 1 个位置）。"""
    return ''.join(_norm_char(c) for c in src[i:i + size])


def _is_name_char(c):
    """片段名可用字符：汉字 / 字母 / 数字以及 '_'、'-'。"""
    return c.isalnum() or c in '_-'


class KnitError(Exception):
    """编译错误：行号 row（1 基）、列号 col（1 基）、余针 remaining（可为 None）。

    与片段相关的错误额外携带：
        def_line / def_col  所涉定义在定义区中的行号 / 列号（1 基，可为 None）
        def_name            所涉片段名
        chain               从行内引用到出错位置的片段名链
    """

    def __init__(self, row, col, message, remaining=None,
                 def_line=None, def_col=None, def_name=None, chain=None):
        super().__init__(message)
        self.row = row
        self.col = col
        self.message = message
        self.remaining = remaining
        self.def_line = def_line
        self.def_col = def_col
        self.def_name = def_name
        self.chain = chain

    def to_dict(self):
        d = {
            'row': self.row,
            'col': self.col,
            'message': self.message,
            'remaining': self.remaining,
        }
        if self.def_line is not None:
            d['def_line'] = self.def_line
            d['def_col'] = self.def_col
        if self.def_name is not None:
            d['def_name'] = self.def_name
        if self.chain:
            d['chain'] = list(self.chain)
        return d


# ---- 词法分析 -------------------------------------------------------------
# token: (type, value, start, end)，start/end 为 0 基半开区间
# type: ST 针法 | LP ( | RP ) | STAR * | X xN | REF @片段名

def tokenize(src, row, col_off=0):
    """把一行源码切成记号流。

    col_off：src 若是某行的子串（如定义体），给出其在整行中的 0 基偏移，
    使记号位置始终相对整行（定义体 → 相对定义行）。
    """
    toks = []
    i, n = 0, len(src)
    while i < n:
        ch = _norm_char(src[i])
        low = ch.lower()

        if ch.isspace() or ch == ',':
            i += 1
            continue

        # 关键字优先（k2tog 必须在 k 之前匹配）
        if _norm_window(src, i, 5).lower() == 'k2tog':
            toks.append(('ST', 'k2tog', i + col_off, i + 5 + col_off))
            i += 5
            continue
        if _norm_window(src, i, 2).lower() == 'yo':
            toks.append(('ST', 'yo', i + col_off, i + 2 + col_off))
            i += 2
            continue
        if low in ('k', 'p'):
            toks.append(('ST', low, i + col_off, i + 1 + col_off))
            i += 1
            continue

        if ch == '@':
            j = i + 1
            while j < n and _is_name_char(_norm_char(src[j])):
                j += 1
            if j == i + 1:
                raise KnitError(row, i + 1 + col_off,
                                "'@' 后需要片段名称，例如 @罗纹")
            name = ''.join(_norm_char(c) for c in src[i + 1:j])
            toks.append(('REF', name, i + col_off, j + col_off))
            i = j
            continue

        if ch == '(':
            toks.append(('LP', '(', i + col_off, i + 1 + col_off))
            i += 1
            continue
        if ch == ')':
            toks.append(('RP', ')', i + col_off, i + 1 + col_off))
            i += 1
            continue
        if ch == '*':
            toks.append(('STAR', '*', i + col_off, i + 1 + col_off))
            i += 1
            continue
        if low == 'x':
            j = i + 1
            while j < n and src[j].isdigit():
                j += 1
            if j == i + 1:
                raise KnitError(row, i + 1 + col_off,
                                "'x' 后需要重复次数，例如 (k,p)x3")
            toks.append(('X', int(src[i + 1:j]), i + col_off, j + col_off))
            i = j
            continue

        if ch.isdigit():
            raise KnitError(row, i + 1 + col_off,
                            "不支持 'k3' 这类数字后缀；重复请写 (k)x3，"
                            "整行重复用 *…*")
        raise KnitError(row, i + 1 + col_off, "无法识别的字符 %r" % src[i])

    return toks


# ---- 语法分析（递归下降，支持嵌套括号）--------------------------------------
# AST 节点：
#   {'kind':'st',  'sym':..., 's':i, 'e':j}
#   {'kind':'ref', 'name':..., 's':i, 'e':j}   @片段引用
#   {'kind':'grp', 'items':[...], 'repeat':N, 's','e'}
#   {'kind':'star', 'items':[...], 's','e'}    仅允许出现在行末顶层

def parse_row(src, row):
    toks = tokenize(src, row)
    items, i = _parse_seq(toks, 0, row, stops=(), top=True, allow_star=True)
    if i < len(toks):  # 理论上顶层没有终止符，不会走到这里
        raise KnitError(row, toks[i][2] + 1, "意外的记号")

    # “末尾 *…*”：星号重复必须是最后一个顶层项
    for idx, it in enumerate(items):
        if it['kind'] == 'star' and idx != len(items) - 1:
            raise KnitError(row, it['s'] + 1,
                            "'*…*' 只能位于行末，其后不能再出现其他针法")
    return items


def parse_def_body(src, line_no, col_off=0):
    """解析片段体（定义行中 '=' 之后的部分）。片段内不允许行末 *…*。"""
    toks = tokenize(src, line_no, col_off=col_off)
    items, i = _parse_seq(toks, 0, line_no, stops=(), top=True,
                          allow_star=False)
    if i < len(toks):  # 顶层无终止符，防御性分支
        raise KnitError(line_no, toks[i][2] + 1, "意外的记号")
    if not items:
        raise KnitError(line_no, col_off + 1,
                        "片段内容为空，至少需要一项针法")
    return items


def _parse_seq(toks, i, row, stops, top, allow_star):
    items = []
    while i < len(toks) and toks[i][0] not in stops:
        typ, val, s, e = toks[i]

        if typ == 'ST':
            items.append({'kind': 'st', 'sym': val, 's': s, 'e': e})
            i += 1

        elif typ == 'REF':
            items.append({'kind': 'ref', 'name': val, 's': s, 'e': e})
            i += 1

        elif typ == 'LP':
            inner, j = _parse_seq(toks, i + 1, row, stops=('RP',),
                                  top=False, allow_star=allow_star)
            if j >= len(toks):
                raise KnitError(row, s + 1, "括号 '(' 缺少匹配的 ')'")
            rp_s = toks[j][2]
            j += 1  # 跳过 ')'
            repeat = 1
            if j < len(toks) and toks[j][0] == 'X':
                repeat = toks[j][1]
                if repeat > MAX_GROUP_REPEAT:
                    raise KnitError(
                        row, toks[j][2] + 1,
                        "重复次数过大（上限 %d）" % MAX_GROUP_REPEAT)
                j += 1
            items.append({'kind': 'grp', 'items': inner,
                          'repeat': repeat, 's': s, 'e': rp_s + 1})
            i = j

        elif typ == 'STAR':
            if not allow_star:
                raise KnitError(
                    row, s + 1,
                    "片段内不允许使用行末重复 '*…*'，"
                    "请把 '*…*' 写在引用行的末尾")
            if not top:
                raise KnitError(row, s + 1,
                                "'*…*' 只能写在行末，不能嵌套在括号里")
            inner, j = _parse_seq(toks, i + 1, row, stops=('STAR',),
                                  top=False, allow_star=allow_star)
            if j >= len(toks):
                raise KnitError(row, s + 1,
                                "'*' 缺少配对的 '*'，行末重复应写成 *…*")
            star_end = toks[j][3]
            j += 1
            items.append({'kind': 'star', 'items': inner,
                          's': s, 'e': star_end})
            i = j

        elif typ == 'RP':
            raise KnitError(row, s + 1, "多余的右括号 ')'")
        elif typ == 'X':
            raise KnitError(row, s + 1, "'xN' 只能紧跟在 ')' 之后使用")
        else:  # pragma: no cover - 防御性分支
            raise KnitError(row, s + 1, "意外的记号")

    return items, i


# ---- 片段定义区 -------------------------------------------------------------

class Definitions:
    """定义区解析结果：结构诊断 + 片段体的惰性解析 + 静态针数缓存。

    片段体只在被引用时才解析（未引用的片段写错也不影响正常行）；
    解析错误以 (列, 消息) 形式缓存，由引用处统一包装成定位到使用行的
    KnitError。
    """

    def __init__(self, lines):
        self.lines = list(lines)
        self.entries = {}      # name -> {'name','line','body','body_off','name_col'}
        self.line_diags = []   # 每行结构诊断（与 self.lines 对齐）
        self.counts = {}       # name -> (consume, produce)，仅缓存成功结果
        self._asts = {}        # name -> (items, None) | (None, (col, message))
        self._parse_structure()

    # -- 结构解析：名称 = 针法序列 --
    def _parse_structure(self):
        for idx, raw in enumerate(self.lines):
            line_no = idx + 1
            diag = {'line': line_no, 'status': 'blank'}
            self.line_diags.append(diag)
            if not raw.strip():
                continue

            eq = next((i for i, c in enumerate(raw)
                       if _norm_char(c) == '='), -1)
            if eq < 0:
                col = next((i + 1 for i, c in enumerate(raw)
                            if not c.isspace()), 1)
                diag.update(status='error', error={
                    'col': col,
                    'message': "缺少 '='：片段定义格式为  名称 = 针法序列"})
                continue

            name_raw = raw[:eq].strip()
            name = ''.join(_norm_char(c) for c in name_raw)
            name_col = raw.find(name_raw) + 1 if name_raw else eq + 1
            if not name:
                diag.update(status='error', error={
                    'col': eq + 1, 'message': "'=' 前缺少片段名称"})
                continue
            bad = next((c for c in name if not _is_name_char(c)), None)
            if bad is not None:
                i = name.index(bad)
                diag.update(status='error', error={
                    'col': name_col + i,
                    'message': "片段名称含非法字符 %r"
                               "（可用汉字、字母、数字、'_'、'-'）"
                               % name_raw[i]})
                continue
            if len(name) > MAX_DEF_NAME_LEN:
                diag.update(status='error', error={
                    'col': name_col,
                    'message': "片段名称过长（上限 %d 字符）"
                               % MAX_DEF_NAME_LEN})
                continue
            if name in self.entries:
                diag.update(status='error', error={
                    'col': name_col,
                    'message': "片段 '%s' 重复定义，以第 %d 行为准"
                               % (name, self.entries[name]['line'])})
                continue

            self.entries[name] = {
                'name': name,
                'line': line_no,
                'body': raw[eq + 1:],
                'body_off': eq + 1,
                'name_col': name_col,
            }
            diag.update(status='ok', name=name)

    # -- 片段体（惰性解析，结果缓存）--
    def get_ast(self, name):
        """返回 (items, None) 或 (None, (列, 消息))；列相对定义行（1 基）。"""
        if name not in self._asts:
            entry = self.entries[name]
            try:
                items = parse_def_body(entry['body'], entry['line'],
                                       entry['body_off'])
                self._asts[name] = (items, None)
            except KnitError as err:
                self._asts[name] = (None, (err.col, err.message))
        return self._asts[name]

    # -- 诊断汇总：供前端定义区行号栏着色 --
    def report(self, sources):
        used = self._collect_used(sources)

        # 对被使用的片段做完整校验（语法 + 引用解析 + 循环检测），
        # 问题按「实际出错定义所在行」归集
        problems = {}
        for name in sorted(used):
            if name not in self.entries:
                continue
            try:
                self._validate(name)
            except KnitError as err:
                line = err.def_line or self.entries[name]['line']
                problems.setdefault(line, {
                    'col': err.def_col or 1, 'message': err.message})

        lines = []
        for diag in self.line_diags:
            d = dict(diag)
            if d['status'] == 'ok':
                d['used'] = d['name'] in used
                if not d['used']:
                    d['status'] = 'unused'
                elif d['line'] in problems:
                    d['status'] = 'error'
                    d['error'] = problems[d['line']]
            lines.append(d)
        return {'lines': lines}

    def _validate(self, name):
        """对片段做完整静态解析（语法 + 引用 + 循环），有错抛 KnitError。"""
        ctx = _Ctx(self, 0)
        ctx.count_ref({'kind': 'ref', 'name': name, 's': 0, 'e': 0})

    def _collect_used(self, sources):
        """从全部行源码出发，静态收集被（直接或间接）引用的片段名。"""
        used = set()
        queue = []

        def feed(names):
            for nm in names:
                if nm not in used:
                    used.add(nm)
                    queue.append(nm)

        for src in sources:
            try:
                toks = tokenize(src, 0)
            except KnitError:
                continue  # 行本身的词法错误由行编译报告
            feed(val for typ, val, _s, _e in toks if typ == 'REF')
        while queue:
            nm = queue.pop()
            if nm not in self.entries:
                continue
            items, _perr = self.get_ast(nm)
            if items is None:
                continue
            feed(_ref_names(items))
        return used


def _ref_names(items):
    out = []

    def walk(seq):
        for it in seq:
            if it['kind'] == 'ref':
                out.append(it['name'])
            elif it['kind'] in ('grp', 'star'):
                walk(it['items'])

    walk(items)
    return out


# ---- 展开上下文（片段解析 + 行内引用锚点）------------------------------------

class _Ctx:
    """单次行展开的上下文：片段表、展开栈与行内最外层引用位置。"""

    def __init__(self, defs, row):
        self.defs = defs
        self.row = row
        self.stack = []        # 正在展开的片段名（外层在前）
        self.anchor = None     # 行内最外层 '@名称' 的 0 基区间 (s, e)
        self.cur_entry = None  # 当前展开到的片段定义（None = 行源码顶层）

    def error(self, message, node=None, remaining=None,
              def_line=None, def_col=None, def_name=None):
        """构造定位到「使用它的行与引用位置」的错误，并附带所涉定义位置。"""
        entry = self.cur_entry
        if entry is not None:
            if def_line is None:
                def_line = entry['line']
            if def_name is None:
                def_name = entry['name']
            if def_col is None and node is not None:
                def_col = node['s'] + 1
        if self.anchor is not None:
            col = self.anchor[0] + 1
        elif node is not None:
            col = node['s'] + 1
        else:
            col = 1
        return KnitError(self.row, col, message, remaining=remaining,
                         def_line=def_line, def_col=def_col,
                         def_name=def_name,
                         chain=list(self.stack) if self.stack else None)

    def resolve_ref(self, node):
        """解析 @引用 → (片段AST, 定义条目)；失败抛定位到引用处的错误。"""
        name = node['name']
        defs = self.defs
        if defs is None or name not in defs.entries:
            if self.cur_entry is not None:
                raise self.error(
                    "在片段 '%s' 中引用了未定义的片段 '@%s'"
                    % (self.cur_entry['name'], name), node)
            raise self.error(
                "未定义的片段 '@%s'（可在定义区添加「%s = 针法序列」）"
                % (name, name), node)
        if name in self.stack:
            cyc = self.stack[self.stack.index(name):] + [name]
            raise self.error('循环引用：' + ' → '.join(cyc), node)
        entry = defs.entries[name]
        items, perr = defs.get_ast(name)
        if perr is not None:
            raise self.error(
                "片段 '%s' 语法错误：%s" % (name, perr[1]), node,
                def_line=entry['line'], def_col=perr[0], def_name=name)
        return items, entry

    def enter(self, node, entry):
        if not self.stack:  # 从行源码跨入片段：记录行内引用锚点
            self.anchor = (node['s'], node['e'])
        self.stack.append(node['name'])
        self.cur_entry = entry

    def exit(self):
        self.stack.pop()
        if self.stack:
            self.cur_entry = self.defs.entries[self.stack[-1]]
        else:
            self.cur_entry = None
            self.anchor = None

    def walk_ref(self, node, walk_fn):
        items, entry = self.resolve_ref(node)
        self.enter(node, entry)
        try:
            walk_fn(items)
        finally:
            self.exit()

    def count_ref(self, node):
        """片段的静态（消耗, 产出）；成功结果按名缓存（与上下文无关）。"""
        items, entry = self.resolve_ref(node)
        name = node['name']
        cached = self.defs.counts.get(name)
        if cached is not None:
            return cached
        self.enter(node, entry)
        try:
            c, p = _static_count(items, self)
        finally:
            self.exit()
        self.defs.counts[name] = (c, p)
        return c, p


# ---- 静态针数（不展开也能算出一段的消耗/产出）-------------------------------

def _static_count(items, ctx=None):
    consume = produce = 0
    for it in items:
        if it['kind'] == 'st':
            d = STITCHES[it['sym']]
            consume += d['consume']
            produce += d['produce']
        elif it['kind'] == 'grp':
            c, p = _static_count(it['items'], ctx)
            consume += c * it['repeat']
            produce += p * it['repeat']
        elif it['kind'] == 'ref':
            if ctx is None:
                raise KnitError(0, it['s'] + 1,
                                "未定义的片段 '@%s'" % it['name'])
            c, p = ctx.count_ref(it)
            consume += c
            produce += p
        # star 的次数依赖运行时余针，不计入
    return consume, produce


# ---- 行展开 ---------------------------------------------------------------

def expand(items, available, row, defs=None):
    """把 AST 展开为针法列表；available = 本行可消耗针数（上一行产出）。

    返回 (stitches, consumed, produced)。stitches 中每一项：
        {'sym':..., 's': 源码起始列(0基), 'e': 源码结束列(0基, 开区间),
         'pre': [输入针位...]}   1 基针位，相对本行的输入针列
    'pre' 是逐针前驱关系，按针法的实际消耗记录：
        k / p     → 1 个前驱；k2tog → 2 个前驱（两针合并为一针）；
        yo        → 无前驱（挂线凭空产生的新针）。
    第 1 行的前驱针位指向「起针」针位；其后每行指向上一行的产出针位。
    分组重复、行末重复与片段的多次引用都按每次实际展开消耗针位，
    即使源码位置相同，各次展开的 'pre' 也互不相同（不按源码位置合并）。
    来自片段的针法额外携带：
        'def':   {'line','s','e','name'} 实际针法在定义区中的位置
        'chain': [片段名...]              从行内引用到该定义的链
        此时 's'/'e' 是本次行内 '@名称' 引用的位置：同一片段的多次引用
        各自映射回本引用处，互不混淆。
    消耗必须恰好等于 available，否则抛 KnitError（余针 / 针数不足）。
    """
    out = []
    consumed = 0
    ctx = _Ctx(defs, row)

    def emit(st):
        nonlocal consumed
        d = STITCHES[st['sym']]
        c, p = d['consume'], d['produce']
        remaining = available - consumed
        if c > remaining:
            msg = ("针数不足：%s 需消耗 %d 针，但本行只剩 %d 针"
                   % (d['name'], c, remaining))
            if ctx.cur_entry is not None:
                msg += "（展开片段 '%s' 时）" % ctx.cur_entry['name']
            raise ctx.error(msg, st, remaining=remaining)
        # 逐针前驱：本次实际消耗的输入针位（1 基）。重复展开同一源码段时
        # 每次都消耗新的针位，因此不按源码位置合并针目。
        pre = list(range(consumed + 1, consumed + c + 1))
        consumed += c
        base = {'sym': st['sym'], 's': st['s'], 'e': st['e'], 'pre': pre}
        if ctx.cur_entry is None:
            out.append(base)
        else:
            out.append(dict(base, **{
                's': ctx.anchor[0],
                'e': ctx.anchor[1],
                'def': {'line': ctx.cur_entry['line'],
                        's': st['s'], 'e': st['e'],
                        'name': ctx.cur_entry['name']},
                'chain': list(ctx.stack),
            }))
        if len(out) > MAX_STITCHES_PER_ROW:
            raise ctx.error(
                "展开针数超过上限 %d（检查 xN 是否过大）"
                % MAX_STITCHES_PER_ROW, st)
        return p

    def walk(seq):
        for it in seq:
            kind = it['kind']
            if kind == 'st':
                emit(it)
            elif kind == 'grp':
                for _ in range(it['repeat']):
                    walk(it['items'])
            elif kind == 'ref':
                ctx.walk_ref(it, walk)
            else:  # star：只允许在顶层且位于末尾
                once_c, _once_p = _static_count(it['items'], ctx)
                if once_c <= 0:
                    raise KnitError(
                        row, it['s'] + 1,
                        "'*…*' 内单次消耗必须为正针数（当前为 %d），"
                        "否则无法确定重复次数" % once_c,
                        remaining=available - consumed)
                remain = available - consumed
                if remain % once_c != 0:
                    raise KnitError(
                        row, it['s'] + 1,
                        "'*…*' 单次消耗 %d 针，不能整除余针 %d（%d %% %d = %d）"
                        "；不得截断或补针"
                        % (once_c, remain, remain, once_c, remain % once_c),
                        remaining=remain)
                for _ in range(remain // once_c):
                    walk(it['items'])

    walk(items)

    if consumed != available:
        remain = available - consumed
        # 余针定位到行尾（空行则定位到第 1 列）
        raise KnitError(
            row, max(1, items[-1]['e']) if items else 1,
            "行末仍余 %d 针未编织（不得截断或补针）" % remain,
            remaining=remain)

    produced = len(out)  # 四种针法均产出 1 针
    return out, consumed, produced


# ---- 整段编译：逐行推导 ----------------------------------------------------

def compile_rows(cast_on, sources, defs=None):
    """编译全部行。defs 为可选的片段定义区（多行字符串或字符串列表）。

    sources: 每行源码字符串列表。返回可直接 JSON 序列化的结果；
    提供 defs 时结果附带 'defs' 诊断（逐行状态），未提供时不含该键
    （旧请求的行为与返回格式保持不变）。
    """
    if not isinstance(cast_on, int) or isinstance(cast_on, bool):
        raise ValueError('castOn 必须是整数')
    if not (1 <= cast_on <= MAX_CAST_ON):
        raise ValueError('起针数必须在 1..%d 之间' % MAX_CAST_ON)
    if not isinstance(sources, (list, tuple)) or not all(
            isinstance(s, str) for s in sources):
        raise ValueError('rows 必须是字符串列表')
    if len(sources) > MAX_ROWS:
        raise ValueError('行数超过上限 %d' % MAX_ROWS)

    defs_obj = None
    if defs is not None:
        if isinstance(defs, str):
            def_lines = defs.split('\n')
        elif isinstance(defs, (list, tuple)) and all(
                isinstance(s, str) for s in defs):
            def_lines = list(defs)
        else:
            raise ValueError('defs 必须是多行字符串或字符串列表')
        if len(def_lines) > MAX_DEF_LINES:
            raise ValueError('定义区行数超过上限 %d' % MAX_DEF_LINES)
        for i, ln in enumerate(def_lines):
            if len(ln) > MAX_LINE_LEN:
                raise ValueError('定义区第 %d 行超过 %d 字符上限'
                                 % (i + 1, MAX_LINE_LEN))
        defs_obj = Definitions(def_lines)

    result_rows = []
    available = cast_on
    blocked = False  # 一旦某行出错，后续行无法推导针数

    for idx, src in enumerate(sources):
        row_no = idx + 1
        if len(src) > MAX_LINE_LEN:
            raise ValueError('第 %d 行超过 %d 字符上限' % (row_no, MAX_LINE_LEN))

        if blocked:
            result_rows.append({
                'row': row_no, 'source': src, 'status': 'blocked',
            })
            continue

        try:
            ast = parse_row(src, row_no)
            stitches, consumed, produced = expand(ast, available, row_no,
                                                  defs_obj)
            result_rows.append({
                'row': row_no,
                'source': src,
                'status': 'ok',
                'in': available,
                'out': produced,
                'consumed': consumed,
                'stitches': stitches,
            })
            available = produced
        except KnitError as err:
            result_rows.append({
                'row': row_no,
                'source': src,
                'status': 'error',
                'in': available,
                'error': err.to_dict(),
            })
            blocked = True

    result = {'castOn': cast_on, 'rows': result_rows}
    if defs_obj is not None:
        result['defs'] = defs_obj.report(sources)
    return result
