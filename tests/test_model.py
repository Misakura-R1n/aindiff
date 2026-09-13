import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff.model import (
    EntryConflict,
    align_dumps,
    build_edit_text,
    count_changed,
    display_text,
    escape_text,
    normalize_search_text,
    parse_dump,
    parse_edit_text,
    unescape_text,
)


class EditSyntaxTests(unittest.TestCase):
    def test_comments_duplicates_and_escaped_separators(self):
        text = ';s[9] = "ignored"\ns[1] = "first"\ns[1] = "last\\n\\t\u2028" ; comment\n'
        self.assertEqual(parse_edit_text(text), {("s", 1): "last\n\t\u2028"})

    def test_invalid_assignment_is_not_silently_skipped(self):
        with self.assertRaisesRegex(ValueError, "第 2 行"):
            parse_edit_text('s[1] = "valid"\ns[bad] = "invalid"\n')

# Raw string keeps the dump-format backslash escapes literally.
SAMPLE = r"""; main
;s[9] = "hello\nworld"
;s[7] = "a\\b\"c"
;m[1] = "你好"

; sub
;s[7] = "again"
;m[1] = "duplicate message"
"""


class ParseTests(unittest.TestCase):
    def test_parse_sections_and_entries(self):
        dump = parse_dump(SAMPLE, path="sample.ain")
        self.assertEqual(dump.sections, ["main", "sub"])
        self.assertEqual(len(dump.entries), 5)
        self.assertEqual(dump.entries[0].kind, "s")
        self.assertEqual(dump.entries[0].index, 9)
        self.assertEqual(dump.entries[0].section, "main")
        self.assertEqual(dump.entries[0].section_no, 0)
        self.assertEqual(dump.entries[0].text, "hello\nworld")

    def test_unescape_rules(self):
        self.assertEqual(unescape_text(r'a\\b\"c\n\r\t\b\f\q'), 'a\\b"c\n\r\t\b\fq')

    def test_occurrence_counts_restart_per_section(self):
        dump = parse_dump(SAMPLE)
        self.assertEqual(dump.entries[0].key, ("s", 9, 1))
        self.assertEqual(dump.entries[1].key, ("s", 7, 1))
        self.assertEqual(dump.entries[2].key, ("m", 1, 1))
        self.assertEqual(dump.entries[3].key, ("s", 7, 1))
        self.assertEqual(dump.entries[4].key, ("m", 1, 1))

    def test_escape_roundtrip(self):
        text = 'quote" slash\\ nl\n cr\r tab\t'
        self.assertEqual(unescape_text(escape_text(text)), text)

    def test_parse_preserves_control_and_unicode_separators_in_text(self):
        text = "a\b\f\v\x1c\x1d\x1e\x85\u2028\u2029b"
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=repr(newline)):
                source = f'; main{newline};s[1] = "{text}"{newline};m[2] = "last"{newline}'
                dump = parse_dump(source)
                self.assertEqual([entry.text for entry in dump.entries], [text, "last"])
                self.assertEqual([entry.line_no for entry in dump.entries], [2, 3])
                edit = build_edit_text(dump.entries)
                self.assertEqual([entry.text for entry in parse_dump(edit).entries], [text, "last"])

    def test_display_text_is_one_line(self):
        self.assertEqual(display_text("a\nb"), r"a\nb")


class EditTextTests(unittest.TestCase):
    def test_build_edit_text_groups_by_section(self):
        dump = parse_dump(SAMPLE)
        edit = build_edit_text(dump.entries[:2])
        self.assertIn("; main", edit)
        self.assertIn(r's[9] = "hello\nworld"', edit)
        self.assertIn(r's[7] = "a\\b\"c"', edit)

    def test_build_edit_text_rejects_conflicting_duplicate_ids(self):
        dump = parse_dump('; a\n;s[1] = "one"\n;s[1] = "two"\n')
        first, second = dump.entries
        second.text = "changed"
        with self.assertRaises(EntryConflict):
            build_edit_text([first, second])

    def test_build_edit_text_rejects_nul_before_it_can_truncate_a_string(self):
        dump = parse_dump('; a\n;s[1] = "one"\n')
        dump.entries[0].text = "before\0after"
        with self.assertRaisesRegex(ValueError, r"NUL.*s\[1\]"):
            build_edit_text(dump.entries)


class AlignTests(unittest.TestCase):
    def test_align_same_structure(self):
        left = parse_dump(SAMPLE)
        right = parse_dump(SAMPLE.replace("hello", "HELLO"))
        rows = align_dumps(left, right)
        self.assertEqual(rows[0].is_section, True)
        entry_rows = [r for r in rows if not r.is_section]
        self.assertEqual(len(entry_rows), 5)
        self.assertEqual(count_changed(rows), 1)

    def test_align_insertion_and_deletion(self):
        left = parse_dump('; a\n;s[1] = "one"\n;s[2] = "two"\n')
        right = parse_dump('; a\n;s[1] = "ONE"\n;s[3] = "three"\n')
        rows = [r for r in align_dumps(left, right) if not r.is_section]
        keys = [(r.left.key if r.left else None, r.right.key if r.right else None) for r in rows]
        self.assertEqual(
            keys,
            [
                (("s", 1, 1), ("s", 1, 1)),
                (("s", 2, 1), None),
                (None, ("s", 3, 1)),
            ],
        )

    def test_align_multiple_sections_by_ordinal(self):
        left = parse_dump('; A\n;s[1] = "a"\n; B\n;s[2] = "b"\n')
        right = parse_dump('; C\n;s[1] = "c"\n; D\n;s[2] = "d"\n')
        rows = align_dumps(left, right)
        self.assertEqual(rows[0].left_section, "A")
        self.assertEqual(rows[0].right_section, "C")
        self.assertEqual(rows[2].left_section, "B")
        self.assertEqual(rows[2].right_section, "D")


class SearchNormalizeTests(unittest.TestCase):
    def test_fullwidth_to_halfwidth(self):
        self.assertEqual(normalize_search_text("ＡＢＣ１２３"), "abc123")

    def test_casefold(self):
        self.assertEqual(normalize_search_text("Hello"), "hello")

    def test_japanese_kana_halfwidth(self):
        self.assertEqual(normalize_search_text("ﾃｽﾄ"), "テスト")

    def test_distinct_han_codepoints_are_preserved(self):
        # 日文“剣”和中文“剑”是不同字，不应被错误折叠成同一个字。
        self.assertNotEqual(normalize_search_text("剣"), normalize_search_text("剑"))


if __name__ == "__main__":
    unittest.main()
