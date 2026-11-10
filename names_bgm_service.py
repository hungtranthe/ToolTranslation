# -*- coding: utf-8 -*-
"""Backend cho nút "Tạo lại gợi ý" ở trang D (moegirl).

Gộp 2 bước có sẵn thành một lần gọi:
  1) fetch_wikitext_names: tải wikitext các trang nhân vật, trích 姓名：…（kana，Latin）
  2) bgm_names: trang nào còn null thì bổ sung kana/Latin từ Bangumi

Dùng:
  ensure_names_bgm(...)  -> handler "moegirl.ensure_names_bgm": file đã có thì chỉ đếm, chưa có thì tạo.
  load_names_bgm(path)   -> {page_title: {"kana","latin",...}|None} để moegirl.generate lấy tên Latin.
"""
import json
import os
import time
from pathlib import Path

import fetch_wikitext_names as fw
import bgm_names as bg


def build_names_bgm(titles, bgm_dir=None, work_dir=".", out_dir=None, cookie="", delay_wiki=3.0, delay_bgm=0.5, batch=20):
    cookie = cookie or os.environ.get("MOEGIRL_COOKIES", "").strip().strip('"')
    work = Path(work_dir)
    names_path = work / "moegirl_wikitext_names.json"
    # file kết quả nằm ở out_dir (thư mục file truyện); cache dùng chung nằm ở work_dir
    out_path = Path(out_dir or work_dir) / "moegirl_wikitext_names_bgm.json"
    cache_path = work / "bgm_cache.json"

    names = json.loads(names_path.read_text(encoding="utf-8")) if names_path.is_file() else {}
    stopped = None
    wiki_error = None

    # 1) wikitext Moegirl (bỏ qua trang đã có trong cache)
    todo = [t for t in titles if t not in names]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        try:
            parsed = fw.parse_batch(fw.fetch(chunk, cookie), chunk)
        except Exception as e:  # Moegirl lỗi/chặn: không dừng cả quy trình, vẫn bổ sung từ Bangumi
            wiki_error = f"Không tải được wikitext Moegirl ({type(e).__name__}: {e})"
            break
        for t, (res, text) in parsed.items():
            if text:
                names[t] = res
        names_path.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(delay_wiki)

    # 2) Bangumi cho các trang còn null
    bgm_file = Path(bgm_dir) / "moegirl2bgm.json" if bgm_dir else None
    m2b = json.loads(bgm_file.read_text(encoding="utf-8-sig")) if bgm_file and bgm_file.is_file() else {}
    redirects = {}  # id Bangumi đã gộp -> id đích (API trả 404 cho id cũ)
    rd_file = Path(bgm_dir) / "bgm_redirects_full.json" if bgm_dir else None
    if rd_file and rd_file.is_file():
        redirects = {str(k): str(v) for k, v in json.loads(rd_file.read_text(encoding="utf-8-sig")).items()}
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    result = {t: names.get(t) for t in titles}  # trang chưa tải được wikitext = null, để Bangumi điền
    filled = 0
    for t in [t for t, v in result.items() if v is None]:
        ids = []
        for i in bg.ids_for(t, m2b):
            i = redirects.get(i, i)
            if i not in ids:
                ids.append(i)
        if not ids:
            continue
        try:
            entry = bg.make_entry(ids, cache, delay_bgm)
        except bg.NetError as e:
            stopped = f"Bangumi: {e}"
            break
        if entry:
            result[t] = entry
            filled += 1
    bg.save_json(cache_path, cache)

    # Chỉ ghi file cuối khi chạy trọn vẹn; nếu dừng giữa chừng thì tiến trình nằm trong 2 file cache,
    # lần sau chạy lại sẽ tiếp tục và file cuối chưa xuất hiện (tránh dùng nhầm bản dở dang).
    if not stopped:
        bg.save_json(out_path, result)
    return {"created": not stopped, "path": str(out_path), "filled": filled,
            "total": len(result), "stopped": stopped, "wiki_error": wiki_error}


def ensure_names_bgm(titles, bgm_dir, folder=".", **kw):
    """Handler cho moegirl.ensure_names_bgm. folder = thư mục chứa moegirl_wikitext_names_bgm.json."""
    path = Path(folder) / "moegirl_wikitext_names_bgm.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"created": False, "path": str(path), "total": len(data), "stopped": None}
    return build_names_bgm(titles, bgm_dir, work_dir=folder, **kw)


def load_names_bgm(path):
    """Đọc file; trả về {title: {"kana","latin","source"} hoặc None}. Mục có khóa "bangumi" lồng
    (khi chạy --cross-check) vẫn dùng kana/latin của chính mục đó, không ghi đè."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {t: ({"kana": v.get("kana"), "latin": v.get("latin"), "source": v.get("source")} if v else None)
            for t, v in data.items()}
