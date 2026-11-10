import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from xungho_runtime import hint_for_chunk, load_rules


class AddressingRuntimeTests(unittest.TestCase):
    def test_approved_rules_follow_report_titles_not_source_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary = root / "thuatngu.txt"
            glossary.write_text("approved", encoding="utf-8")
            rules = {
                "schema_version": 1,
                "glossary_sha256": hashlib.sha256(glossary.read_bytes()).hexdigest(),
                "chapters": [
                    {"number": 1, "title": "第 1 章 序章"},
                    {"number": 2, "title": "第 2 章 第001章 出院"},
                    {"number": 3, "title": "第 3 章 第002章 上学"},
                ],
                "characters": {
                    "a": {"display": "A", "names": ["甲甲"]},
                    "b": {"display": "B", "names": ["乙乙"]},
                },
                "rules": [
                    {"speaker": "a", "listener": "b", "start": 2, "end": 2,
                     "self": "tôi", "address": "cậu", "note": "ghi chú"},
                ],
            }
            path = root / "xungho.rules.json"
            path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            chunks = [
                {"chapter": 1, "part": 1, "text": "## 第001章 出院\n甲甲 乙乙"},
                {"chapter": 2, "part": 1, "text": "## 第002章 上学\n甲甲 乙乙"},
                {"chapter": 3, "part": 1, "text": "## Không khớp\n甲甲 乙乙"},
            ]
            data, missing = load_rules(path, glossary, chunks)
            self.assertEqual(missing, 1)
            hint = hint_for_chunk(data, 1, "甲甲 nói với 乙乙")
            self.assertIn('A nói với B: tự xưng "tôi"; gọi người nghe "cậu"; ghi chú', hint)
            self.assertEqual(hint_for_chunk(data, 2, "甲甲 乙乙"), "")
            self.assertEqual(hint_for_chunk(data, 3, "甲甲 乙乙"), "")
            self.assertEqual(hint_for_chunk(data, 1, "甲甲"), "")
            glossary.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "đã đổi"):
                load_rules(path, glossary, chunks)

    def test_ambiguous_names_do_not_select_a_character(self):
        data = {
            "_pattern": __import__("re").compile("共同|乙乙"),
            "_names": {"共同": {"a", "c"}, "乙乙": {"b"}},
            "_chapter_map": {1: 1},
            "characters": {"a": {"display": "A"}, "b": {"display": "B"}},
            "rules": [{"speaker": "a", "listener": "b", "start": 1, "end": 1,
                       "self": "tôi", "address": "", "note": ""}],
        }
        self.assertEqual(hint_for_chunk(data, 1, "共同 乙乙"), "")


if __name__ == "__main__":
    unittest.main()
