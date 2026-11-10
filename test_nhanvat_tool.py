import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

import moegirl_index
import nhanvat_tool as tool


class NhanvatToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.out = self.root / "out"
        self.out.mkdir()
        records = {
            "比企谷八幡": {"本名": "Hikigaya Hachiman"},
            "比企谷小町": {"本名": "Hikigaya Komachi"},
            "雪之下雪乃": {"英文名": "Yukinoshita Yukino"},
            "雪之下阳乃": {"本名": "Yukinoshita Haruno"},
            "毛利兰": {"本名": "Mouri Ran"},
        }
        (self.dataset / "extra_processed.json").write_text(json.dumps(records), encoding="utf-8")
        (self.dataset / "char2subject.json").write_text(json.dumps({
            "比企谷八幡": ["Oregairu"], "比企谷小町": ["Oregairu"],
            "雪之下雪乃": ["Oregairu"], "雪之下阳乃": ["Oregairu"],
            "毛利兰": ["Detective Conan"],
        }), encoding="utf-8")
        (self.dataset / "char2gender.json").write_text(json.dumps({
            "比企谷八幡": "male", "比企谷小町": "female",
        }), encoding="utf-8")
        self.works = self.root / "works.tsv"
        self.works.write_text(
            "work_id\twork_title\tsubject_keys\noregairu\tMy Teen Romantic Comedy\tOregairu\n",
            encoding="utf-8",
        )
        self.glossary = self.root / "thuatngu.txt"
        self.glossary.write_text(
            "比企谷八幡 => Hikigaya Hachiman # nhân vật; gender=male; id=hachiman\n"
            "比企谷小町 => Hikigaya Komachi # nhân vật; gender=female; id=komachi\n",
            encoding="utf-8",
        )
        self.reader = self.root / "report.md"
        self.reader.write_text(
            "# Báo cáo\n"
            "## 第 1 章 第007章\n"
            "### 👥 人物\n"
            "- **比企谷八幡** — nhân vật A\n"
            "- **比企谷小町** — em gái\n"
            "- **雪之下雪乃**\n"
            "- **雪之下阳乃**\n"
            "- **小町**\n"
            "- **雪之下**\n"
            "- **高杉步**\n"
            "- **毛利**\n"
            "### 🤝 关系\n"
            "- 比企谷八幡 —（同学）→ 比企谷小町：câu một\n"
            "## 第 2 章 第009章\n"
            "### 👥 人物\n"
            "- **比企谷八幡**\n"
            "- **比企谷小町**\n"
            "### 🤝 关系\n"
            "- 比企谷八幡 —（朋友）→ 比企谷小町：câu hai\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_and_cast_only_resolution_never_uses_substring_search(self):
        args = Namespace(
            reader=str(self.reader), out=str(self.out), glossary=str(self.glossary),
            aliases=str(self.root / "aliases.txt"), overrides=str(self.root / "overrides.tsv"),
            moegirl_dir=str(self.dataset), works=str(self.works),
        )
        with patch.object(moegirl_index.MoegirlIndex, "find_pages", side_effect=AssertionError):
            data = tool.run_entities(args)
        resolutions = data["resolutions"]
        self.assertEqual(resolutions["比企谷八幡"]["level"], "A")
        self.assertEqual(resolutions["小町"]["level"], "B")
        self.assertEqual(resolutions["雪之下"]["level"], "B")
        self.assertIsNone(resolutions["雪之下"]["key"])
        self.assertEqual(resolutions["毛利"]["level"], "D")
        self.assertEqual(resolutions["高杉步"]["level"], "D")
        self.assertEqual(len(data["ambiguous"]), 1)
        workbook = load_workbook(self.out / "entities.xlsx")
        workbook["Mơ hồ"]["E2"] = "雪之下雪乃"
        workbook.save(self.out / "entities.xlsx")
        workbook.close()
        rerun = tool.run_entities(args)
        self.assertEqual(rerun["resolutions"]["雪之下"]["level"], "0")
        self.assertEqual(rerun["resolutions"]["雪之下"]["page"], "雪之下雪乃")

    def test_glossary_and_xungho_are_gated_by_manual_approval(self):
        tool.run_entities(Namespace(
            reader=str(self.reader), out=str(self.out), glossary=str(self.glossary),
            aliases=str(self.root / "aliases.txt"), overrides=str(self.root / "overrides.tsv"),
            moegirl_dir=str(self.dataset), works=str(self.works),
        ))
        args = Namespace(out=str(self.out), glossary=str(self.glossary))
        count, _ = tool.run_glossary(args)
        self.assertGreater(count, 0)
        self.assertTrue((self.out / "thuatngu.candidates.txt").is_file())
        self.assertIn(
            "# CHƯA CÓ ĐÍCH: 雪之下雪乃",
            (self.out / "thuatngu.candidates.txt").read_text(encoding="utf-8-sig"),
        )
        self.assertEqual((self.root / "thuatngu.txt").read_text(encoding="utf-8"), self.glossary.read_text(encoding="utf-8"))

        xungho_args = Namespace(
            out=str(self.out), glossary=str(self.glossary),
            rel_types=str(self.root / "rel_types.txt"), min_run=2,
        )
        rows, _ = tool.run_xungho(xungho_args)
        self.assertEqual(rows, 2)
        workbook = load_workbook(self.out / "xungho.xlsx", read_only=True, data_only=True)
        sheet = workbook["Xưng hô"]
        names = [row[1] for row in sheet.iter_rows(min_row=2, values_only=True)]
        workbook.close()
        self.assertTrue(names)
        self.assertTrue(all("Hikigaya" in name for name in names))
        workbook = load_workbook(self.out / "xungho.xlsx")
        workbook["Xưng hô"]["K2"] = "tớ"
        workbook["Xưng hô"]["L2"] = "cậu"
        workbook.save(self.out / "xungho.xlsx")
        workbook.close()
        tool.run_xungho(xungho_args)
        self.assertIn('tự xưng "tớ"; gọi "cậu"', (self.out / "xungho.txt").read_text(encoding="utf-8-sig"))
        rules = json.loads((self.out / "xungho.rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules["rules"]), 1)
        self.assertEqual(rules["rules"][0]["self"], "tớ")
        self.assertEqual(rules["rules"][0]["address"], "cậu")
        self.assertEqual(rules["rules"][0]["start"], 1)
        self.assertEqual(rules["rules"][0]["end"], 2)

    def test_short_relationship_runs_merge_without_losing_later_runs(self):
        chapters = [{"number": number, "source_number": number, "title": str(number)}
                    for number in range(1, 9)]
        relations = []
        for chapter, kind in (
            [(1, "朋友"), (2, "朋友"), (3, "朋友"), (4, "冲突"),
             (5, "合作"), (6, "债务"), (7, "债务"), (8, "债务")]
        ):
            relations.append({
                "chapter": chapter, "source": "a", "target": "b",
                "type": kind, "count": 1, "quotes": [],
            })
        data = {
            "chapters": chapters,
            "characters": [
                {"key": "a", "canonical_zh": "甲", "chapter_count": 8},
                {"key": "b", "canonical_zh": "乙", "chapter_count": 8},
            ],
            "relations": relations,
        }
        rows, _ = tool.build_pairs(
            data, {"a", "b"},
            {"朋友": "Bạn", "冲突": "Xung đột", "合作": "Hợp tác", "债务": "Nợ"},
            min_run=2,
        )
        self.assertEqual(len(rows), 4)
        phases = {(row["Nhóm quan hệ"], row["Từ chương (file)"], row["Đến chương"])
                  for row in rows}
        self.assertEqual(phases, {("Bạn", 1, 5), ("Nợ", 6, "hết")})

    def test_user_entries_are_preserved_and_orphaned(self):
        path = self.out / "xungho.xlsx"
        sheets = {"Xưng hô": (["_key", "Tự xưng", "Gọi người nghe là", "Ghi chú của tôi"], [
            {"_key": "pair:a>b:1-2", "Tự xưng": "", "Gọi người nghe là": "", "Ghi chú của tôi": ""},
        ]), "Mồ côi": (["_key", "Khóa cũ", "Tự xưng", "Gọi người nghe là", "Ghi chú của tôi"], [])}
        tool.write_workbook(path, sheets, orphan_sheet="Mồ côi")
        workbook = load_workbook(path)
        workbook["Xưng hô"]["B2"] = "tớ"
        workbook.save(path)
        workbook.close()
        changed = {"Xưng hô": (["_key", "Tự xưng", "Gọi người nghe là", "Ghi chú của tôi"], [])}
        tool.write_workbook(path, changed, orphan_sheet="Mồ côi")
        workbook = load_workbook(path, read_only=True, data_only=True)
        orphan_values = list(workbook["Mồ côi"].values)
        workbook.close()
        self.assertTrue(any("tớ" in row for row in orphan_values))

    def test_parse_reader_keeps_file_and_source_chapter_numbers(self):
        parsed = tool.parse_reader(self.reader)
        self.assertEqual([chapter["number"] for chapter in parsed["chapters"]], [1, 2])
        self.assertEqual([chapter["source_number"] for chapter in parsed["chapters"]], [7, 9])
        self.assertEqual(parsed["chapters"][0]["relations"][0]["quote"], "câu một")

    def test_reader_aliases_are_evidence_not_automatic_merges(self):
        self.reader.write_text(
            "## 第 1 章 第001章\n### 👥 人物\n"
            "- **甲**（别名：乙、丙） — 描述\n", encoding="utf-8"
        )
        person = tool.parse_reader(self.reader)["chapters"][0]["characters"][0]
        self.assertEqual(person["name"], "甲")
        self.assertEqual(person["aliases"], ["乙", "丙"])
        self.assertEqual(person["description"], "描述")

    def test_pair_key_stays_stable_when_phase_end_changes(self):
        path = self.out / "xungho.xlsx"
        headers = ["_key", "Tự xưng"]
        tool.write_workbook(path, {"Xưng hô": (headers, [{"_key": "pair:a>b:1-2", "Tự xưng": ""}])})
        workbook = load_workbook(path)
        workbook["Xưng hô"]["B2"] = "tớ"
        workbook.save(path)
        workbook.close()
        result = tool.write_workbook(path, {"Xưng hô": (headers, [{"_key": "pair:a>b:1", "Tự xưng": ""}])}, orphan_sheet="Mồ côi")
        self.assertEqual(result["Xưng hô"][1][0]["Tự xưng"], "tớ")
        self.assertEqual(result["Mồ côi"][1], [])

    def test_approved_work_id_schema_respects_status(self):
        path = self.root / "approved.tsv"
        path.write_text(
            "work_id\twork_title\tstatus\n"
            "Oregairu\tMy Teen Romantic Comedy\tapproved\n"
            "Not approved\tOther\tpending\n",
            encoding="utf-8",
        )
        self.assertEqual(tool._approved_subjects(path), {"Oregairu"})

    def test_blank_destination_does_not_approve_character(self):
        self.glossary.write_text("甲 =>  # nhân vật; id=a\n", encoding="utf-8")
        groups, _ = tool.load_glossary_characters(self.glossary)
        self.assertEqual(groups, [])

    def test_project_ui_saves_approved_rows_and_rejects_stale_data(self):
        from novel_backend import NovelBackend

        source = self.root / "source.txt"
        source.write_text("## 第007章\n甲\n", encoding="utf-8")
        tool.run_entities(Namespace(
            reader=str(self.reader), out=str(self.root), glossary=str(self.glossary),
            aliases=str(self.root / "aliases.txt"), overrides=str(self.root / "overrides.tsv"),
            moegirl_dir=str(self.dataset), works=str(self.works),
        ))
        backend = NovelBackend(app_dir=self.root)
        self.assertTrue(backend.dispatch("project.open", {"source": str(source)})["ok"])
        generated = backend.dispatch("xungho.generate", {"source": str(source)})
        self.assertTrue(generated["ok"], generated)
        data = generated["data"]
        self.assertEqual(len(data["rows"]), 2)
        friends = next(item for item in data["summary"] if item["type"] == "朋友")
        reverse = next(r for r in data["rows"] if r["speaker"] == friends["pairs"][0]["target"])
        forward = next(r for r in data["rows"] if r["speaker"] == friends["pairs"][0]["source"])
        self.assertIn(forward["key"], friends["row_keys"])
        self.assertNotIn(reverse["key"], friends["row_keys"])
        row = data["rows"][0]
        saved = backend.dispatch("xungho.save", {
            "source": str(source), "revision": data["revision"], "updates": [
                {"key": row["key"], "self": "em", "you": "anh", "name": "gọi tên"},
            ],
        })
        self.assertTrue(saved["ok"], saved)
        rules = json.loads((self.root / "xungho.rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules["rules"]), 1)
        self.assertEqual(rules["rules"][0]["self"], "em")
        self.assertEqual(rules["rules"][0]["note"], "gọi tên")
        stale = backend.dispatch("xungho.save", {"source": str(source), "revision": data["revision"], "updates": []})
        self.assertEqual(stale["error"]["code"], "STALE_DATA")
        switched = backend.dispatch("xungho.generate", {"source": str(self.root / "other.txt")})
        self.assertEqual(switched["error"]["code"], "STALE_PROJECT")
        self.assertEqual(saved["data"]["rows"][0]["self"], "em")

    def test_partial_save_preserves_other_phases_and_exports_rules(self):
        from novel_backend import NovelBackend

        source = self.root / "source.txt"
        source.write_text("test", encoding="utf-8")
        self.reader.write_text(
            "# Report\n" + "".join(
                f"## 第 {n} 章 第00{n}章\n### 👥 人物\n"
                "- **比企谷八幡**\n- **比企谷小町**\n### 🤝 关系\n"
                f"- 比企谷八幡 —（{kind}）→ 比企谷小町：evidence\n"
                for n, kind in enumerate(("同学", "同学", "朋友", "朋友"), 1)
            ), encoding="utf-8",
        )
        tool.run_entities(Namespace(
            reader=str(self.reader), out=str(self.root), glossary=str(self.glossary),
            aliases=str(self.root / "aliases.txt"), overrides=str(self.root / "overrides.tsv"),
            moegirl_dir=str(self.dataset), works=str(self.works),
        ))
        backend = NovelBackend(app_dir=self.root)
        self.assertTrue(backend.dispatch("project.open", {"source": str(source)})["ok"])
        generated = backend.dispatch("xungho.generate", {"source": str(source)})
        self.assertTrue(generated["ok"], generated)
        rows = generated["data"]["rows"]
        phases = sorted((r for r in rows if r["speaker"] == rows[0]["speaker"]), key=lambda r: r["from"])
        self.assertEqual(len(phases), 2)
        initial = backend.dispatch("xungho.save", {
            "source": str(source), "revision": generated["data"]["revision"],
            "updates": [{"key": phases[0]["key"], "self": "tôi", "you": "cậu", "name": "trước"},
                        {"key": phases[1]["key"], "self": "ta", "you": "ngươi", "name": "giữ"}],
        })
        self.assertTrue(initial["ok"], initial)
        saved = backend.dispatch("xungho.save", {
            "source": str(source), "revision": initial["data"]["revision"],
            "updates": [{"key": phases[1]["key"], "self": "anh", "you": "em", "name": "giữ"}],
        })
        self.assertTrue(saved["ok"], saved)
        by_key = {r["key"]: r for r in saved["data"]["rows"]}
        self.assertEqual((by_key[phases[0]["key"]]["self"], by_key[phases[0]["key"]]["you"]), ("tôi", "cậu"))
        self.assertEqual((by_key[phases[1]["key"]]["self"], by_key[phases[1]["key"]]["you"],
                          by_key[phases[1]["key"]]["name"]), ("anh", "em", "giữ"))
        exported = json.loads((self.root / "xungho.rules.json").read_text(encoding="utf-8"))
        rule = next(r for r in exported["rules"] if r["speaker"] == phases[1]["speaker"] and r["start"] == 3)
        self.assertEqual((rule["self"], rule["address"], rule["note"]), ("anh", "em", "giữ"))

    def test_ui_analysis_and_character_review(self):
        from novel_backend import NovelBackend

        source = self.root / "source.txt"
        source.write_text("## 第007章\n甲\n", encoding="utf-8")
        self.glossary.write_text("比企谷八幡 => Hikigaya Hachiman # keep-note\n", encoding="utf-8")
        backend = NovelBackend(app_dir=self.root)
        self.assertTrue(backend.dispatch("project.open", {"source": str(source)})["ok"])
        p = backend.project.paths
        import shutil
        shutil.copyfile(self.works, p.approved)
        shutil.copytree(self.dataset, self.root / "moegirl")
        result = backend.dispatch("xungho.analyze", {"source": str(source), "report": str(self.reader)})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["chapters"], 2)
        review = backend.dispatch("xungho.review", {"source": str(source)})
        self.assertTrue(review["ok"], review)
        c = next(c for c in review["characters"] if c["name"] == "比企谷八幡")
        self.assertTrue(c["approved"])
        self.assertEqual(c["dest"], "Hikigaya Hachiman")
        result = backend.dispatch("xungho.approve_character", {
            "source": str(source), "revision": review["revision"], "key": c["key"],
            "destination": "Wrong", "gender": "male", "sources": ["比企谷八幡"],
        })
        self.assertFalse(result["ok"])
        self.assertNotIn("nhân vật", self.glossary.read_text(encoding="utf-8"))
        result = backend.dispatch("xungho.approve_character", {
            "source": str(source), "revision": review["revision"], "key": c["key"],
            "destination": "Hikigaya Hachiman", "gender": "male", "sources": ["比企谷八幡"],
        })
        self.assertTrue(result["ok"], result)
        self.assertIn("keep-note; nhân vật", self.glossary.read_text(encoding="utf-8-sig"))
        self.assertTrue(result["saved"]["backup"])
        ambiguous = result["review"]["ambiguous"][0]
        result = backend.dispatch("xungho.decision", {
            "source": str(source), "revision": result["review"]["revision"],
            "name": ambiguous["Cách gọi"], "choice": "not a candidate",
        })
        self.assertFalse(result["ok"])
        review = backend.dispatch("xungho.review", {"source": str(source)})
        ambiguous = review["ambiguous"][0]
        candidate = ambiguous["Ứng viên"].split(" | ")[0]
        result = backend.dispatch("xungho.decision", {
            "source": str(source), "revision": review["revision"],
            "name": ambiguous["Cách gọi"], "choice": candidate,
        })
        self.assertTrue(result["ok"], result)
        book = load_workbook(self.root / "entities.xlsx", read_only=True, data_only=True)
        self.assertEqual(book["Mơ hồ"]["E2"].value, candidate)
        book.close()
        result = backend.dispatch("xungho.analyze", {"source": str(source), "report": str(self.reader)})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ambiguous"], 0)

    def test_existing_glossary_auto_approval_in_both_directions(self):
        import xungho_ui

        tool.run_entities(Namespace(
            reader=str(self.reader), out=str(self.out), glossary=str(self.glossary),
            aliases=str(self.root / "aliases.txt"), overrides=str(self.root / "overrides.tsv"),
            moegirl_dir=str(self.dataset), works=str(self.works),
        ))
        self.glossary.write_text(
            "Hikigaya Hachiman = 比企谷八幡\n"
            "比企谷小町 => Hikigaya Komachi\n"
            "雪之下雪乃 => Yukino\n雪之下雪乃 => Other\n",
            encoding="utf-8",
        )
        original = self.glossary.read_bytes()
        review = xungho_ui.review(self.out, self.glossary)
        chars = {c["name"]: c for c in review["characters"]}
        self.assertTrue(chars["比企谷八幡"]["approved"])
        self.assertTrue(chars["比企谷小町"]["approved"])
        self.assertFalse(chars["雪之下雪乃"]["approved"])
        self.assertTrue(chars["雪之下雪乃"]["conflict"])
        self.assertFalse(chars["高杉步"]["approved"])
        count, _ = tool.run_xungho(Namespace(
            out=str(self.out), glossary=str(self.glossary),
            rel_types=str(self.root / "rel_types.txt"), min_run=2,
        ))
        self.assertEqual(count, 2)
        workbook = load_workbook(self.out / "xungho.xlsx", read_only=True, data_only=True)
        self.assertEqual(workbook["Nhân vật"].max_row, 3)
        workbook.close()
        self.assertEqual(self.glossary.read_bytes(), original)

    def test_missing_openpyxl_reports_interpreter_and_review_requires_project_source(self):
        import builtins
        import sys
        from novel_backend import NovelBackend

        source = self.root / "source.txt"
        source.write_text("## 第001章\n甲\n", encoding="utf-8")
        backend = NovelBackend(app_dir=self.root)
        self.assertTrue(backend.dispatch("project.open", {"source": str(source)})["ok"])
        original_import = builtins.__import__

        def missing_dependency(name, *args, **kwargs):
            if name == "xungho_ui":
                raise ModuleNotFoundError("No module named 'openpyxl'", name="openpyxl")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_dependency):
            result = backend.dispatch("xungho.read")
        self.assertEqual(result["error"]["code"], "MISSING_DEPENDENCY")
        self.assertIn(sys.executable, result["error"]["message"])
        self.assertIn("-m pip install openpyxl", result["error"]["message"])
        result = backend.dispatch("xungho.review")
        self.assertEqual(result["error"]["code"], "STALE_PROJECT")

    def test_relationship_summary_preserves_report_labels_direction_and_evidence(self):
        import xungho_ui

        data = {
            "characters": [
                {"key": "a", "canonical_zh": "甲", "gender": "male", "calls": [{"name": "甲"}]},
                {"key": "b", "canonical_zh": "乙", "gender": "female", "calls": [{"name": "乙"}]},
                {"key": "c", "canonical_zh": "丙", "gender": "", "calls": [{"name": "丙"}]},
            ],
            "relations": [
                {"source": "a", "target": "b", "chapter": 1, "type": "朋友", "quotes": ["evidence"]},
                {"source": "a", "target": "b", "chapter": 1, "type": "朋友", "quotes": ["evidence"]},
                {"source": "b", "target": "a", "chapter": 1, "type": "朋友"},
                {"source": "b", "target": "c", "chapter": 2, "type": "对话"},
                {"source": "a", "target": "b", "chapter": 3, "type": "兄弟"},
                {"source": "a", "target": "b", "chapter": 4, "type": "恋人"},
                {"source": "a", "target": "b", "chapter": 4, "type": "情侣"},
            ],
        }
        summary = xungho_ui.relationship_summary(data, self.root / "missing-rel.txt", self.glossary)
        counts = {(r["type"], r["speaker_gender"], r["listener_gender"]): r for r in summary}
        self.assertEqual(counts[("朋友", "male", "female")]["observations"], 1)
        self.assertEqual(counts[("朋友", "female", "male")]["observations"], 1)
        self.assertEqual(counts[("对话", "female", "")]["observations"], 1)
        self.assertNotIn(("对话", "", "female"), counts)
        self.assertEqual(counts[("兄弟", "male", "female")]["group"], "Anh em trai")
        pair = counts[("朋友", "male", "female")]["pairs"][0]
        self.assertEqual((pair["source_name"], pair["target_name"], pair["chapters"], pair["quotes"]),
                         ("甲", "乙", [1], ["evidence"]))
        self.assertIn(("恋人", "male", "female"), counts)
        self.assertIn(("情侣", "male", "female"), counts)

    def test_sibling_roles_require_evidence_and_support_manual_review(self):
        import xungho_ui
        from novel_backend import NovelBackend

        source = self.root / "source.txt"
        source.write_text("test", encoding="utf-8")
        chars = [
            {"key": "a", "canonical_zh": "甲", "gender": "male", "calls": [{"name": "甲"}]},
            {"key": "b", "canonical_zh": "乙", "gender": "female", "calls": [{"name": "乙"}]},
            {"key": "c", "canonical_zh": "丙", "gender": "male", "calls": [{"name": "丙"}]},
            {"key": "d", "canonical_zh": "丁", "gender": "", "calls": [{"name": "丁"}]},
        ]
        relations = [
            {"source": "a", "target": "b", "chapter": 1, "type": "兄妹"},
            {"source": "b", "target": "a", "chapter": 2, "type": "兄妹"},
            {"source": "a", "target": "b", "chapter": 3, "type": "姐弟"},
            {"source": "a", "target": "c", "chapter": 4, "type": "兄弟"},
            {"source": "a", "target": "d", "chapter": 5, "type": "兄妹"},
        ]
        (self.root / "entities.json").write_text(json.dumps({
            "characters": chars, "relations": relations, "chapters": [],
        }, ensure_ascii=False), encoding="utf-8")
        summary = xungho_ui.read(self.root, self.glossary)["summary"]
        get = lambda kind, a, b: next(g for g in summary if g["type"] == kind and
                                     any(p["source"] == a and p["target"] == b for p in g["pairs"]))
        self.assertEqual(get("兄妹", "a", "b")["role_label"], "Anh trai – Em gái")
        self.assertEqual(get("兄妹", "b", "a")["role_label"], "Em gái – Anh trai")
        self.assertEqual(get("姐弟", "a", "b")["role_label"], "Em trai – Chị gái")
        self.assertEqual(get("兄弟", "a", "c")["role_origin"], "pending")
        self.assertEqual(get("兄妹", "a", "d")["role_origin"], "pending")

        backend = NovelBackend(app_dir=self.root)
        self.assertTrue(backend.dispatch("project.open", {"source": str(source)})["ok"])
        initial = backend.dispatch("xungho.read")
        params = {"source": str(source), "revision": initial["revision"], "kind": "兄妹",
                  "a": "a", "b": "b", "a_role": "younger_male", "b_role": "older_female"}
        saved = backend.dispatch("xungho.roles", params)
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(backend.dispatch("xungho.roles", params)["error"]["code"], "STALE_DATA")
        summary = backend.dispatch("xungho.read")["summary"]
        self.assertEqual(get("兄妹", "a", "b")["role_label"], "Em trai – Chị gái")
        self.assertEqual(get("兄妹", "b", "a")["role_label"], "Chị gái – Em trai")
        self.assertEqual(get("兄妹", "a", "b")["role_origin"], "manual")
        params.update(revision=saved["revision"], a_role="older_male", b_role="older_female")
        self.assertFalse(backend.dispatch("xungho.roles", params)["ok"])
        params.update(a_role="", b_role="")
        self.assertTrue(backend.dispatch("xungho.roles", params)["ok"])
        summary = backend.dispatch("xungho.read")["summary"]
        self.assertEqual(get("兄妹", "a", "b")["role_origin"], "report")


if __name__ == "__main__":
    unittest.main()
