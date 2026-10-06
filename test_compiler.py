# -*- coding: utf-8 -*-
"""compiler 模块单元测试：python -m unittest -v"""
import unittest

from compiler import KnitError, compile_rows, expand, parse_row


def run(cast_on, *lines):
    return compile_rows(cast_on, list(lines))


def ok_rows(result):
    return [r for r in result['rows'] if r['status'] == 'ok']


class TestBasicStitches(unittest.TestCase):
    def test_kp_passthrough(self):
        r = run(4, 'k k p k')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual((row['in'], row['out']), (4, 4))
        self.assertEqual([s['sym'] for s in row['stitches']],
                         ['k', 'k', 'p', 'k'])

    def test_yo_consumes_nothing(self):
        r = run(2, 'k yo k')
        row = r['rows'][0]
        self.assertEqual((row['in'], row['out']), (2, 3))

    def test_k2tog_decreases(self):
        r = run(4, 'k2tog k2tog')
        row = r['rows'][0]
        self.assertEqual((row['in'], row['out']), (4, 2))

    def test_row_chain_derives_next_input(self):
        # 第 1 行 4→3，第 2 行必须以 3 针为输入
        r = run(4, 'k k k2tog', 'k yo k k')
        rows = r['rows']
        self.assertEqual(rows[0]['out'], 3)
        self.assertEqual(rows[1]['in'], 3)
        self.assertEqual(rows[1]['out'], 4)

    def test_case_and_fullwidth(self):
        r = run(2, '（K，Ｐ）x1')
        self.assertEqual(r['rows'][0]['status'], 'ok')

    def test_digit_suffix_rejected(self):
        r = run(9, 'k3')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 2)
        self.assertIn('(k)x3', err['message'])


class TestGroups(unittest.TestCase):
    def test_simple_group(self):
        r = run(6, '(k p)x3')
        row = r['rows'][0]
        self.assertEqual(row['out'], 6)
        self.assertEqual([s['sym'] for s in row['stitches']],
                         ['k', 'p'] * 3)

    def test_nested_group(self):
        # (k (p yo)x2)x2：内层 耗1产2，x2 → 耗2产4；外层 耗3产5，x2 → 耗6产10
        r = run(6, '(k (p yo)x2)x2')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['out'], 10)

    def test_group_repeat_maps_to_same_source(self):
        r = run(4, '(k2tog)x2')
        spans = [(s['s'], s['e']) for s in r['rows'][0]['stitches']]
        self.assertEqual(spans, [(1, 6), (1, 6)])  # 两次展开都映射回同一源码段

    def test_unclosed_paren(self):
        r = run(4, '(k p')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 1)
        self.assertIn("')'", err['message'])

    def test_stray_rparen(self):
        r = run(4, 'k p)')
        self.assertEqual(r['rows'][0]['error']['col'], 4)

    def test_stray_x(self):
        r = run(4, 'k x3 p')
        self.assertEqual(r['rows'][0]['error']['col'], 3)


class TestStarRepeat(unittest.TestCase):
    def test_star_fills_to_end(self):
        # 10 针：k4 后余 6，*yo k2tog* 单次耗 2 → 重复 3 次
        r = run(10, 'k k k k *yo k2tog*')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['out'], 4 + 3 * 2)

    def test_star_whole_row(self):
        r = run(12, '*k k p p*')
        row = r['rows'][0]
        self.assertEqual(row['out'], 12)

    def test_star_not_divisible_reports_remaining(self):
        # 10 针，单次耗 3 → 10 % 3 = 1，余针 10
        r = run(10, '*k2tog k*')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 10)
        self.assertIn('不能整除', err['message'])
        self.assertEqual(err['col'], 1)

    def test_star_zero_consumption_rejected(self):
        r = run(6, '*yo*')
        err = r['rows'][0]['error']
        self.assertIn('单次消耗必须为正', err['message'])
        self.assertEqual(err['remaining'], 6)

    def test_star_must_be_at_end(self):
        r = run(6, '*k2tog* k')
        self.assertIn('行末', r['rows'][0]['error']['message'])

    def test_star_cannot_nest_in_group(self):
        r = run(6, '(*k2tog*)x2')
        self.assertIn('行末', r['rows'][0]['error']['message'])

    def test_unclosed_star(self):
        r = run(6, '*k2tog')
        self.assertIn("'*'", r['rows'][0]['error']['message'])


class TestStitchAccounting(unittest.TestCase):
    def test_leftover_stitches_error(self):
        # 5 针只织了 3 针 → 余 2，定位行尾
        r = run(5, 'k k k')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 2)
        self.assertIn('余 2 针', err['message'])
        self.assertEqual(err['col'], 5)

    def test_insufficient_stitches_error(self):
        # 只剩 1 针时遇到 k2tog（需 2 针）
        r = run(3, 'k k k2tog')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 1)
        self.assertEqual(err['col'], 5)
        self.assertIn('针数不足', err['message'])

    def test_empty_line_errors_when_stitches_remain(self):
        r = run(2, '')
        self.assertEqual(r['rows'][0]['error']['remaining'], 2)

    def test_error_blocks_following_rows(self):
        r = run(4, 'k k k k', 'k k', 'k k k k')
        rows = r['rows']
        self.assertEqual(rows[0]['status'], 'ok')
        self.assertEqual(rows[1]['status'], 'error')
        self.assertEqual(rows[2]['status'], 'blocked')

    def test_recompute_after_fix(self):
        # 修正中间行后，后续行按新针数继续推导
        r = run(4, 'k k k k', 'k k k k', 'k2tog k2tog')
        self.assertTrue(all(row['status'] == 'ok' for row in r['rows']))
        self.assertEqual(r['rows'][2]['out'], 2)


class TestValidation(unittest.TestCase):
    def test_bad_cast_on(self):
        with self.assertRaises(ValueError):
            compile_rows(0, ['k'])
        with self.assertRaises(ValueError):
            compile_rows('24', ['k'])

    def test_expand_position_is_one_based(self):
        # 第 2 个记号处出错，列号应为该记号起始列（1 基）
        ast = parse_row('k k2tog', 1)
        with self.assertRaises(KnitError) as ctx:
            expand(ast, 1, 1)
        self.assertEqual(ctx.exception.col, 3)


class TestSnippets(unittest.TestCase):
    """片段定义区：名称 = 针法序列，行内 @名称 引用。"""

    def run_defs(self, cast_on, defs, *lines):
        return compile_rows(cast_on, list(lines), defs)

    def test_basic_reference(self):
        r = self.run_defs(4, '罗纹 = k k, p p', '@罗纹')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual((row['in'], row['out']), (4, 4))
        self.assertEqual([s['sym'] for s in row['stitches']],
                         ['k', 'k', 'p', 'p'])
        # 行内引用位置 + 实际针法所在定义位置
        st = row['stitches'][0]
        self.assertEqual((st['s'], st['e']), (0, 3))   # 行内 @罗纹
        self.assertEqual(st['def']['line'], 1)
        self.assertEqual(st['def']['name'], '罗纹')
        self.assertEqual(st['chain'], ['罗纹'])

    def test_reference_in_group(self):
        r = self.run_defs(6, 'a = k p', '(@a)x3')
        row = r['rows'][0]
        self.assertEqual(row['out'], 6)
        self.assertEqual([s['sym'] for s in row['stitches']], ['k', 'p'] * 3)
        # 分组展开的所有针都映射回同一引用处
        self.assertEqual([(s['s'], s['e']) for s in row['stitches']],
                         [(1, 3)] * 6)

    def test_reference_inside_star_counts_consumption(self):
        # 镂空 单次耗 2：k4 后余 6 → 重复 3 次
        r = self.run_defs(10, '镂空 = yo, k2tog', 'k k k k *@镂空*')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['out'], 4 + 3 * 2)

    def test_star_whole_row_is_reference(self):
        r = self.run_defs(8, '罗纹 = k k, p p', '*@罗纹*')
        self.assertEqual(r['rows'][0]['out'], 8)

    def test_star_not_divisible_with_reference(self):
        # a 单次耗 3，10 % 3 = 1 → 报错并给出余针
        r = self.run_defs(10, 'a = k2tog k', '*@a*')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 10)
        self.assertIn('不能整除', err['message'])

    def test_star_zero_consumption_via_def(self):
        r = self.run_defs(6, 'a = yo', '*@a*')
        err = r['rows'][0]['error']
        self.assertIn('单次消耗必须为正', err['message'])
        self.assertEqual(err['remaining'], 6)

    def test_forward_reference(self):
        r = self.run_defs(2, 'b = @a k\na = p', '@b')
        self.assertEqual([s['sym'] for s in r['rows'][0]['stitches']],
                         ['p', 'k'])

    def test_mutual_noncyclic_reference(self):
        r = self.run_defs(2, 'a = k @b\nb = p', '@a')
        self.assertEqual([s['sym'] for s in r['rows'][0]['stitches']],
                         ['k', 'p'])

    def test_multiple_references_not_confused(self):
        r = self.run_defs(4, 'a = k k', '@a @a')
        sts = r['rows'][0]['stitches']
        # 同一片段的两次引用：各自映射回本引用处
        self.assertEqual([(s['s'], s['e']) for s in sts],
                         [(0, 2), (0, 2), (3, 5), (3, 5)])
        self.assertTrue(all(s['def']['line'] == 1 for s in sts))

    def test_stitch_maps_to_definition_position(self):
        defs = 'a = k k\nb = @a p'
        r = self.run_defs(6, defs, '@b @b')
        sts = r['rows'][0]['stitches']
        # 第 1 针来自 a（经 b），第 3 针来自 b 自身
        self.assertEqual(sts[0]['def'], {'line': 1, 's': 4, 'e': 5,
                                         'name': 'a'})
        self.assertEqual(sts[0]['chain'], ['b', 'a'])
        self.assertEqual(sts[2]['def'], {'line': 2, 's': 7, 'e': 8,
                                         'name': 'b'})
        self.assertEqual(sts[2]['chain'], ['b'])
        # 第二次引用：行内位置不同，定义位置相同
        self.assertEqual((sts[3]['s'], sts[3]['e']), (3, 5))
        self.assertEqual(sts[3]['def'], sts[0]['def'])

    def test_missing_reference(self):
        r = self.run_defs(2, '', 'k @nope')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 3)
        self.assertIn('未定义', err['message'])
        self.assertIn('@nope', err['message'])
        self.assertNotIn('def_line', err)

    def test_missing_reference_inside_definition(self):
        r = self.run_defs(3, 'a = k @nope', 'k @a k')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 3)        # 定位到行内 @a
        self.assertEqual(err['def_line'], 1)   # 附带所涉定义位置
        self.assertEqual(err['def_col'], 7)
        self.assertIn('@nope', err['message'])
        self.assertIn("'a'", err['message'])

    def test_circular_reference(self):
        r = self.run_defs(2, 'a = @b\nb = @a k', '@a')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 1)
        self.assertIn('循环引用', err['message'])
        self.assertIn('a → b → a', err['message'])
        self.assertEqual(err['def_line'], 2)   # 闭环引用写在 b 的定义里
        self.assertEqual(err['def_col'], 5)

    def test_self_cycle(self):
        r = self.run_defs(2, 'a = k @a', '@a')
        self.assertIn('循环引用', r['rows'][0]['error']['message'])

    def test_definition_syntax_error_at_use_site(self):
        r = self.run_defs(2, 'a = k ) k', '@a @a')
        err = r['rows'][0]['error']
        self.assertEqual((err['row'], err['col']), (1, 1))
        self.assertEqual(err['def_line'], 1)
        self.assertEqual(err['def_col'], 7)
        self.assertIn("片段 'a' 语法错误", err['message'])

    def test_star_forbidden_inside_definition(self):
        r = self.run_defs(2, 'a = *k k*', '@a')
        self.assertIn('片段内不允许', r['rows'][0]['error']['message'])

    def test_empty_definition_body(self):
        r = self.run_defs(2, 'a =', '@a')
        err = r['rows'][0]['error']
        self.assertIn('内容为空', err['message'])
        self.assertEqual(err['def_line'], 1)

    def test_insufficient_stitches_inside_definition(self):
        r = self.run_defs(2, 'a = k k2tog', '@a')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 1)        # 行内 @a 的位置
        self.assertEqual(err['remaining'], 1)
        self.assertIn('针数不足', err['message'])
        self.assertEqual(err['def_line'], 1)   # 可定位到定义里的 k2tog
        self.assertEqual(err['def_col'], 7)

    def test_unused_broken_definition_ignored(self):
        r = self.run_defs(2, '坏 = k ) k\n好 = k k', '@好')
        self.assertEqual(r['rows'][0]['status'], 'ok')
        lines = {d['line']: d for d in r['defs']['lines']}
        self.assertEqual(lines[1]['status'], 'unused')  # 写错但未引用：不影响
        self.assertNotIn('error', lines[1])
        self.assertEqual(lines[2]['status'], 'ok')
        self.assertTrue(lines[2]['used'])

    def test_structural_errors_do_not_block_rows(self):
        defs = '这不是定义\na = k k\na = p p'
        r = self.run_defs(2, defs, '@a')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual([s['sym'] for s in row['stitches']], ['k', 'k'])
        lines = {d['line']: d for d in r['defs']['lines']}
        self.assertEqual(lines[1]['status'], 'error')   # 缺少 '='
        self.assertEqual(lines[3]['status'], 'error')   # 重复定义
        self.assertEqual(lines[2]['status'], 'ok')

    def test_error_blocks_following_rows(self):
        r = self.run_defs(4, 'a = k k', '@missing', 'k k k k')
        rows = r['rows']
        self.assertEqual(rows[0]['status'], 'error')
        self.assertEqual(rows[1]['status'], 'blocked')

    def test_definition_edit_recomputes(self):
        rows = ['@a']
        r1 = compile_rows(2, rows, 'a = k k')
        r2 = compile_rows(2, rows, 'a = k p')
        self.assertEqual([s['sym'] for s in r1['rows'][0]['stitches']],
                         ['k', 'k'])
        self.assertEqual([s['sym'] for s in r2['rows'][0]['stitches']],
                         ['k', 'p'])

    def test_group_repeat_with_reference(self):
        # a 耗 3 产 2：(@a)x4 耗 12 产 8，下一行以 8 针为输入
        r = self.run_defs(12, 'a = k k2tog', '(@a)x4', '*k k*')
        rows = r['rows']
        self.assertTrue(all(row['status'] == 'ok' for row in rows))
        self.assertEqual((rows[0]['out'], rows[1]['in']), (8, 8))

    def test_def_report_used_flags(self):
        defs = 'a = k\nb = @a\nc = p'
        r = self.run_defs(1, defs, '@b')
        lines = {d['line']: d for d in r['defs']['lines']}
        self.assertTrue(lines[1]['used'])    # 间接引用也算被使用
        self.assertTrue(lines[2]['used'])
        self.assertEqual(lines[3]['status'], 'unused')

    def test_def_error_visible_even_if_row_blocked(self):
        # 第 1 行出错 → 第 2 行未计算，但被引用片段的问题仍标在定义区
        r = self.run_defs(2, 'a = k ) k', 'k k k', '@a @a')
        rows = r['rows']
        self.assertEqual(rows[0]['status'], 'error')
        self.assertEqual(rows[1]['status'], 'blocked')
        line1 = r['defs']['lines'][0]
        self.assertEqual(line1['status'], 'error')
        self.assertIn('语法错误', line1['error']['message'])

    def test_defs_as_list(self):
        r = compile_rows(2, ['@a'], ['a = k k'])
        self.assertEqual(r['rows'][0]['status'], 'ok')
        self.assertIn('defs', r)

    def test_no_defs_key_for_old_requests(self):
        r = compile_rows(2, ['k k'])
        self.assertNotIn('defs', r)
        self.assertNotIn('def', r['rows'][0]['stitches'][0])
        # 无定义区时行内 @引用 → 未定义错误
        r2 = compile_rows(2, ['@a'])
        self.assertEqual(r2['rows'][0]['status'], 'error')

    def test_fullwidth_at(self):
        r = self.run_defs(2, '罗纹 = k k', '＠罗纹')
        self.assertEqual(r['rows'][0]['status'], 'ok')

    def test_at_without_name(self):
        r = self.run_defs(2, '', 'k @ k')
        self.assertIn("'@' 后需要片段名称", r['rows'][0]['error']['message'])


class TestPredecessors(unittest.TestCase):
    """逐针前驱 'pre'：每行按实际消耗记录前驱针位（1 基）。"""

    def test_kp_take_one_each(self):
        r = run(4, 'k p k k')
        sts = r['rows'][0]['stitches']
        self.assertEqual([s['pre'] for s in sts],
                         [[1], [2], [3], [4]])

    def test_k2tog_takes_two(self):
        r = run(4, 'k2tog k2tog')
        sts = r['rows'][0]['stitches']
        self.assertEqual([s['pre'] for s in sts], [[1, 2], [3, 4]])

    def test_yo_has_no_predecessor(self):
        r = run(2, 'k yo k')
        sts = r['rows'][0]['stitches']
        self.assertEqual([s['pre'] for s in sts], [[1], [], [2]])

    def test_row_chain_predecessors_point_to_prior_outputs(self):
        # 行1: k yo k（2→3，行2 的第 2 针由 yo 凭空产生）
        # 行2: k2tog k：并入行1 的 1、2 针，再承接第 3 针
        r = run(2, 'k yo k', 'k2tog k')
        s1, s2 = (r['rows'][0]['stitches'], r['rows'][1]['stitches'])
        self.assertEqual([s['pre'] for s in s1], [[1], [], [2]])
        self.assertEqual([s['pre'] for s in s2], [[1, 2], [3]])

    def test_group_repeat_consumes_fresh_positions(self):
        # (k2tog)x2：两次展开同一源码段，各并 1,2 与 3,4，不合并针目
        r = run(4, '(k2tog)x2')
        sts = r['rows'][0]['stitches']
        self.assertEqual([s['pre'] for s in sts], [[1, 2], [3, 4]])

    def test_nested_group_and_yo(self):
        # (k (p yo)x2)x2：耗 6 产 10
        r = run(6, '(k (p yo)x2)x2')
        sts = r['rows'][0]['stitches']
        pres = [s['pre'] for s in sts]
        # k p yo p yo | k p yo p yo
        self.assertEqual(pres, [[1], [2], [], [3], [],
                                [4], [5], [], [6], []])

    def test_star_repeat_each_iteration_distinct(self):
        # 10 针：k4 后 *yo k2tog* 重复 3 次，每次并不同的针位
        r = run(10, 'k k k k *yo k2tog*')
        sts = r['rows'][0]['stitches']
        self.assertEqual(len(sts), 10)
        # 前 4 个 k，然后 yo/k2tog 三组
        self.assertEqual([s['pre'] for s in sts[:4]],
                         [[1], [2], [3], [4]])
        self.assertEqual([s['pre'] for s in sts[4:]],
                         [[], [5, 6], [], [7, 8], [], [9, 10]])

    def test_predecessors_partition_input_row(self):
        # 任意合法行：每个输入针位恰好被消耗一次（k/p 或 k2tog）
        r = run(12, '(k (p yo)x2)x2 *k2tog*')
        used = [p for s in r['rows'][0]['stitches'] for p in s['pre']]
        self.assertEqual(sorted(used), list(range(1, 13)))

    def test_snippet_references_not_merged(self):
        # 同一 @a 引用两次：pre 按每次实际消耗记录
        r = compile_rows(4, ['@a @a'], 'a = k2tog')
        sts = r['rows'][0]['stitches']
        self.assertEqual([s['pre'] for s in sts], [[1, 2], [3, 4]])

    def test_snippet_with_yo(self):
        # 镂空 = yo k2tog，10 针：k4 后重复 3 次
        r = compile_rows(10, ['k k k k *@镂空*'], '镂空 = yo, k2tog')
        sts = r['rows'][0]['stitches']
        self.assertEqual([s['pre'] for s in sts],
                         [[1], [2], [3], [4],
                          [], [5, 6], [], [7, 8], [], [9, 10]])

    def test_trace_back_to_cast_on_and_yo(self):
        # 端到端：行2 产出针的源头 = 起针针位 + 行1 的 yo
        r = run(2, 'k2tog yo', 'k k')
        row2 = r['rows'][1]['stitches']
        # 行2 第1针 → 行1 第1针（= 起针 1、2）；第2针 → 行1 第2针（yo 新针）
        self.assertEqual([s['pre'] for s in row2], [[1], [2]])
        row1 = r['rows'][0]['stitches']
        self.assertEqual(row1[0]['pre'], [1, 2])
        self.assertEqual(row1[1]['pre'], [])

    def test_legal_large_trace_back_to_every_cast_or_yo(self):
        # 两轮「1 针扩到 2048 针，再逐行并回 1 针」。前端分段展开该追溯时，
        # 覆盖节点会超过 6000；后端返回的每个针位仍必须带有完整针法和 pre。
        expand_row = 'k (yo)x999 (yo)x524 (yo)x524'
        sources = [expand_row]
        target = 1024
        while target >= 1:
            if target == 1024:
                sources.append('((k2tog)x512)x2')
            else:
                sources.append('(k2tog)x%d' % target)
            target //= 2
        sources += [expand_row]
        target = 1024
        while target >= 1:
            if target == 1024:
                sources.append('((k2tog)x512)x2')
            else:
                sources.append('(k2tog)x%d' % target)
            target //= 2

        r = compile_rows(1, sources)
        self.assertEqual(len(r['rows']), 24)
        self.assertTrue(all(row['status'] == 'ok' for row in r['rows']))
        self.assertEqual([row['out'] for row in r['rows'][:12]],
                         [2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1])
        self.assertEqual([row['out'] for row in r['rows'][12:]],
                         [2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1])

        def collect_sources(row_no, pos, seen=None):
            if seen is None:
                seen = {'cast': set(), 'yo': set(), 'nodes': 0}
            row = r['rows'][row_no - 1]
            st = row['stitches'][pos - 1]
            self.assertIn(st['sym'], ('k', 'yo', 'k2tog'))
            self.assertIn('pre', st)
            seen['nodes'] += 1
            if st['sym'] == 'yo':
                seen['yo'].add((row_no, pos))
                return seen
            if row_no == 1:
                seen['cast'].update(st['pre'])
                return seen
            for pre in st['pre']:
                collect_sources(row_no - 1, pre, seen)
            return seen

        sources_for_last = collect_sources(24, 1)
        self.assertEqual(sources_for_last['cast'], {1})
        first_round_yos = {(1, pos) for pos in range(2, 2049)}
        second_round_yos = {(13, pos) for pos in range(2, 2049)}
        self.assertEqual(sources_for_last['yo'],
                         first_round_yos | second_round_yos)
        self.assertEqual(sources_for_last['nodes'], 8190)

        first_round_target = collect_sources(12, 1)
        self.assertEqual(first_round_target['cast'], {1})
        self.assertEqual(first_round_target['yo'], first_round_yos)
        self.assertEqual(first_round_target['nodes'], 4095)

    def test_error_and_blocked_rows_have_no_pre(self):
        r = run(4, 'k k', 'k k k k')
        self.assertEqual(r['rows'][0]['status'], 'error')
        self.assertNotIn('stitches', r['rows'][0])
        self.assertEqual(r['rows'][1]['status'], 'blocked')
        self.assertNotIn('stitches', r['rows'][1])


if __name__ == '__main__':
    unittest.main()
