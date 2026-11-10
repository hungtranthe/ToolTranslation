# -*- coding: utf-8 -*-
"""Smoke-test các API anchors.* mà pageC() sử dụng (qua NovelBackend.dispatch)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import novel_backend as nb  # noqa: E402

tmp = tempfile.mkdtemp(prefix="nv_anchors_")
src = Path(tmp) / "truyen.txt"
src.write_text("第一章 测试\n这是叶修的故事。\n", encoding="utf-8")

backend = nb.NovelBackend()
fails = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        fails.append(name)


r = backend.dispatch("project.open", {"source": str(src)})
check("project.open", r.get("ok") is True, str(r.get("error", "")))

r = backend.dispatch("anchors.list", {})
check("anchors.list", r.get("ok") is True and "items" in r and "works" in r
      and "kind_counts" in r and "avg_weight" in r and "total_rows" in r and "dirty" in r,
      "keys=" + ",".join(sorted(k for k in r if k != "ok")))

r = backend.dispatch("anchors.add", {"fields": {
    "source_name": "叶修", "work_id": "WP_0001", "work_title": "Toàn Chức Cao Thủ",
    "aliases": "叶秋, 君莫笑", "weight": 1.0, "kind": "main",
    "subject_keys": "quanzhi_ye", "notes": "nhân vật chính"}})
check("anchors.add", r.get("ok") is True and "row" in r and r["row"].get("row_id"),
      f"row_id={r.get('row', {}).get('row_id')}")
row_id = (r.get("row") or {}).get("row_id")

r = backend.dispatch("anchors.update", {"row_id": row_id, "fields": {"weight": 0.9, "kind": "main"}})
check("anchors.update", r.get("ok") is True and r.get("row", {}).get("weight") == 0.9)

r = backend.dispatch("anchors.check", {})
check("anchors.check", r.get("ok") is True and "valid" in r and "errors" in r
      and "warnings" in r and "can_save" in r, f"valid={r.get('valid')} errors={len(r.get('errors', []))}")

r = backend.dispatch("anchors.export_tsv", {})
check("anchors.export_tsv", r.get("ok") is True and "text" in r and "rows" in r,
      f"rows={r.get('rows')}")

r = backend.dispatch("anchors.import_tsv", {
    "text": "source_name\twork_id\twork_title\taliases\tweight\tkind\tsubject_keys\tnotes\n"
            "萧炎\tWP_0192\tĐấu Phá Thương Khung\t炎帝\t1.0\tmain\tdoupo_main\t\n"})
check("anchors.import_tsv", r.get("ok") is True and r.get("added") == 1, f"added={r.get('added')}")

r = backend.dispatch("anchors.save", {})
check("anchors.save", r.get("ok") is True and "rows" in r and "path" in r,
      f"rows={r.get('rows')} backup={bool(r.get('backup'))}")

r = backend.dispatch("anchors.list", {"query": "萧", "kind": "main"})
check("anchors.list (query+kind)", r.get("ok") is True and r.get("total") == 1, f"total={r.get('total')}")

r = backend.dispatch("anchors.delete", {"row_ids": [row_id]})
check("anchors.delete", r.get("ok") is True and r.get("deleted") == 1, f"deleted={r.get('deleted')}")

r = backend.dispatch("anchors.reload", {})
check("anchors.reload", r.get("ok") is True and "dirty" in r)

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
