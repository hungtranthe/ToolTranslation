# -*- coding: utf-8 -*-
import json, re, sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\8man\moe_girl_dataset\moegirl-dataset\moegirl\extra_info.json")
title = sys.argv[2] if len(sys.argv) > 2 else "比企谷八幡"
if not p.is_file():
    sys.exit(f"Không thấy {p}")
print(f"{p.name}: {p.stat().st_size / 1e6:.0f} MB")
data = json.loads(p.read_text(encoding="utf-8-sig"))
if not isinstance(data, dict):
    sys.exit(f"File là {type(data).__name__}, không phải dict {{tên trang: nội dung}}")
print(f"{len(data):,} mục")
rec = data.get(title)
if rec is None:
    sys.exit(f"Không có mục {title}")
blob = json.dumps(rec, ensure_ascii=False)
print(f"Kiểu mục: {type(rec).__name__}, dài {len(blob):,} ký tự")
if isinstance(rec, dict):
    print("Khóa:", list(rec)[:30])
for kw in ("Hikigaya", "ひきがや", "基本资料", "姓名"):
    i = blob.find(kw)
    print(f"{kw!r}:", "KHÔNG có" if i < 0 else blob[max(0, i - 60): i + 160])
pat = re.compile(r"[ぁ-んァ-ヶー ]{2,}[，,、]\s*[A-Z][A-Za-z.' -]{2,}")
n = sum(1 for v in data.values() if pat.search(json.dumps(v, ensure_ascii=False)))
print(f"Số mục có mẫu 'kana，Latin': {n:,}/{len(data):,}")