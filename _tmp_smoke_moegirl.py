# -*- coding: utf-8 -*-
"""Smoke-test các API moegirl.* mà pageD() sử dụng (qua NovelBackend.dispatch)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import novel_backend as nb  # noqa: E402

tmp = tempfile.mkdtemp(prefix="nv_moegirl_")
src = Path(tmp) / "truyen.txt"
src.write_text("第一章 测试\n这是叶修的故事。叶修很强。\n", encoding="utf-8")

backend = nb.NovelBackend()
fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


r = backend.dispatch("project.open", {"source": str(src)})
check("project.open", r.get("ok") is True, str(r.get("error", "")))

# Gieo sẵn store gợi ý (moegirl_suggestions.json)
proj = backend.project
proj.paths.suggestions.write_text(json.dumps({
    "version": 1,
    "candidates": [{
        "id": "CAND_test1", "src": "叶修", "dst": "Ye Xiu", "dst_alt": "",
        "works": ["WP_0001"], "work_titles": ["Toàn Chức Cao Thủ"],
        "aliases": ["君莫笑"], "count": 5, "chapters": 2,
        "source_tag": "moegirl_name", "category": "character",
        "status": "pending", "mode": "force", "note": "", "conflict": None,
        "committed_at": None,
    }],
    "filtered": {},
}, ensure_ascii=False), encoding="utf-8")

r = backend.dispatch("moegirl.list", {})
check("moegirl.list", r.get("ok") is True and r.get("total") == 1
      and "status_counts" in r and "work_counts" in r, f"total={r.get('total')}")

r = backend.dispatch("moegirl.detail", {"cand_id": "CAND_test1"})
check("moegirl.detail", r.get("ok") is True and "contexts" in r and r.get("src") == "叶修",
      f"contexts={len(r.get('contexts', []))}")

r = backend.dispatch("moegirl.set_status", {"cand_ids": ["CAND_test1"], "status": "approved"})
check("moegirl.set_status", r.get("ok") is True and r.get("updated") == 1)

r = backend.dispatch("moegirl.edit", {"cand_id": "CAND_test1", "dst": "Ye Xiu (Dịch)", "mode": "hint"})
check("moegirl.edit", r.get("ok") is True and r.get("updated") == 1)

r = backend.dispatch("moegirl.export", {"fmt": "tsv"})
check("moegirl.export", r.get("ok") is True and "text" in r and "rows" in r, f"rows={r.get('rows')}")

r = backend.dispatch("moegirl.cache_info", {})
check("moegirl.cache_info", r.get("ok") is True and "project_cache" in r and "scope" in r)

r = backend.dispatch("moegirl.commit_preview", {})
check("moegirl.commit_preview", r.get("ok") is True and "token" in r and "add" in r,
      f"add={len(r.get('add', []))}")
token = r.get("token")

r = backend.dispatch("moegirl.commit", {"token": token})
check("moegirl.commit", r.get("ok") is True and "committed" in r,
      f"committed={r.get('committed')} added={r.get('added')}")

r = backend.dispatch("moegirl.build_project_cache", {})
check("moegirl.build_project_cache (chưa có catalog -> báo lỗi rõ)",
      r.get("ok") is False and (r.get("error", {}).get("code") == "NO_CATALOG"),
      str((r.get("error") or {}).get("code")))

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
