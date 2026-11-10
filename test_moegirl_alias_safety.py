import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from moegirl_dataset_import import build_catalog
from moegirl_index import MoegirlIndex
from novel_backend import MoegirlService, _automatic_aliases
from nhanvat_tool import build_cast


class MoegirlAliasSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _write_dataset(self, records):
        dataset = self.root / "dataset"
        dataset.mkdir(exist_ok=True)
        (dataset / "extra_processed.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8"
        )
        (dataset / "char2subject.json").write_text(
            json.dumps({title: [] for title in records}, ensure_ascii=False),
            encoding="utf-8",
        )
        return dataset

    def test_short_han_alias_is_manual_only_but_short_real_name_is_strong(self):
        dataset = self._write_dataset({
            "藤原千花": {
                "昵称": "笨蛋",
                "本名": "千花",
                "别名": "藤原千花会长",
            },
        })

        catalog = build_catalog(dataset)
        record = catalog["records"]["藤原千花"]
        self.assertIn("笨蛋", record["aliases"])
        self.assertIn("笨蛋", record["weak_aliases"])
        self.assertIn("千花", record["strong_aliases"])
        self.assertIn("藤原千花会长", record["strong_aliases"])
        self.assertEqual(
            _automatic_aliases({"aliases": ["笨蛋", "藤原千花会长"]}, "藤原千花"),
            ["藤原千花会长"],
        )

        index = MoegirlIndex(dataset)
        self.assertEqual(index.pages_for_name("笨蛋"), [])
        self.assertEqual(index.pages_for_name("千花"), ["藤原千花"])
        self.assertEqual(index.find_pages("笨蛋"), ["藤原千花"])
        self.assertEqual(index.find_pages("笨"), [])

    def test_glossary_generation_excludes_weak_and_ambiguous_aliases(self):
        glossary = self.root / "thuatngu.txt"
        chapters = SimpleNamespace(chapter_of=lambda _position: 1)
        project = SimpleNamespace(
            cfg={"moegirl": {"min_frequency": 1, "min_confidence": 0.0}},
            approvals=SimpleNamespace(approved_ids=lambda: []),
            paths=SimpleNamespace(glossary=glossary, source=self.root / "source.txt"),
            anchors=SimpleNamespace(works=lambda: {}),
            source_text=lambda: ("藤原千花 笨蛋 共同使用的称呼", chapters, None),
        )
        catalog = {"records": {
            "藤原千花": {
                "page_title": "藤原千花", "canonical_zh": "藤原千花",
                "aliases": ["笨蛋", "共同使用的称呼"],
                "strong_aliases": ["共同使用的称呼"], "weak_aliases": ["笨蛋"],
                "latin_entries": [{"name": "Chika Fujiwara", "confidence": 0.9}],
            },
            "另一人物": {
                "page_title": "另一人物", "canonical_zh": "另一人物",
                "aliases": ["共同使用的称呼"],
                "strong_aliases": ["共同使用的称呼"], "weak_aliases": [],
                "latin_entries": [{"name": "Another Person", "confidence": 0.9}],
            },
        }}
        service = MoegirlService(project)
        saved = []
        with (
            patch.object(service, "_load_catalog", return_value=catalog),
            patch.object(service, "_read_names_bgm", return_value={}),
            patch.object(service, "_load_store", return_value={"candidates": []}),
            patch.object(service, "_save_store", side_effect=saved.append),
        ):
            service.generate()

        candidates = saved[0]["candidates"]
        sources = {candidate["src"] for candidate in candidates}
        self.assertIn("藤原千花", sources)
        self.assertNotIn("笨蛋", sources)
        self.assertNotIn("共同使用的称呼", sources)

    def test_entity_resolution_does_not_promote_weak_alias_from_seed_name(self):
        dataset = self._write_dataset({
            "藤原千花": {"昵称": "笨蛋", "本名": "千花"},
        })
        index = MoegirlIndex(dataset)
        catalog = build_catalog(dataset)
        parsed = {"chapters": [{
            "number": 1,
            "characters": [
                {"name": "藤原千花", "description": ""},
                {"name": "笨蛋", "description": ""},
            ],
            "relations": [],
        }]}

        data = build_cast(
            parsed, index, catalog, [], {}, {}, {"藤原千花"},
            set(), {}, {},
        )
        self.assertEqual(data["resolutions"]["藤原千花"]["level"], "A")
        self.assertEqual(data["resolutions"]["笨蛋"]["level"], "D")


if __name__ == "__main__":
    unittest.main()
