# -*- coding: utf-8 -*-
"""Tải wikitext của nhân vật Moegirl và trích dòng '姓名：<Hán>（<kana>，<Latin>）'.

Thử một trang trước:
    python fetch_wikitext_names.py --titles 比企谷八幡
Chạy theo tác phẩm đã duyệt:
    python fetch_wikitext_names.py --dataset-dir <moegirl> --approved moegirl_works_approved.txt
Cookie (nếu site yêu cầu) đặt trong biến môi trường MOEGIRL_COOKIES, định dạng như README của dataset.
Kết quả lưu ở --out (mặc định moegirl_wikitext_names.json); chạy lại sẽ bỏ qua trang đã có.
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kana_romaji import kana_to_romaji

API = "https://zh.moegirl.org.cn/api.php"
NAME_LINE = re.compile(r"姓名|名字|本名|日文名")
PAIR = re.compile(r"[（(]\s*([ぁ-んァ-ヶー・　 ]+?)\s*[，,、;；]\s*([A-Za-z][A-Za-z.'’\- ]*?)\s*[）)]")
_TPL = re.compile(r"\{\{[^{}|]*\|([^{}]*)\}\}")
_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")


def clean_markup(s):
    s = re.sub(r"'{2,}", "", s)
    s = _LINK.sub(r"\1", s)
    for _ in range(3):  # {{lang|ja|ひきがや}} -> ひきがや (lấy tham số cuối)
        s = _TPL.sub(lambda m: m.group(1).split("|")[-1], s)
    return re.sub(r"<[^>]+>", "", s)


ANCHORED = re.compile(r"(?:姓名|名字|本名|日文名)[^（(\n]{0,40}?" + PAIR.pattern)


KANA_ONLY = re.compile(r"(?:姓名|名字|本名|日文名)[^（(\n]{0,40}?[（(]\s*([ぁ-んァ-ヶー・　 ]+?)\s*[）)]")


def extract(wikitext):
    """Tìm '姓名：<Hán>（<kana>，<Latin>）'. Cặp kana,Latin phải nằm ngay sau từ khóa (kể cả khi trang
    bị mất xuống dòng), để không nhặt nhầm cặp ngoặc ở chỗ khác trong bài.
    Nếu chỉ có kana (vd '本名=比企谷 八幡\\n（ひきがや はちまん）') thì đổi kana -> romaji bằng kana_romaji.
    Kết quả có thêm khóa 'source': 'page' (Latin có sẵn trên trang) hoặc 'romaji' (tự chuyển từ kana)."""
    lines = wikitext.splitlines()
    for i, line in enumerate(lines):
        if not NAME_LINE.search(line):
            continue
        # nối với dòng kế: kana có thể nằm ở dòng dưới (infobox xuống dòng sau tên Hán)
        clean = clean_markup(line + " " + (lines[i + 1] if i + 1 < len(lines) else ""))
        m = ANCHORED.search(clean)
        if m:
            return {"kana": m.group(1).strip(), "latin": m.group(2).strip(),
                    "source": "page", "line": m.group(0).strip()[:200]}
        m = KANA_ONLY.search(clean)
        if m:
            latin = kana_to_romaji(m.group(1))
            if latin:
                return {"kana": m.group(1).strip(), "latin": latin,
                        "source": "romaji", "line": m.group(0).strip()[:200]}
    return None


def fetch(titles, cookie):
    qs = urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
        "redirects": 1, "format": "json", "formatversion": 2, "titles": "|".join(titles)})
    headers = {"User-Agent": "novel-glossary-helper/0.1 (personal use)"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(API + "?" + qs, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if "error" in data:
        raise RuntimeError(f"API trả lỗi: {data['error']}")
    return data


def parse_batch(data, titles):
    q = data.get("query", {})
    mapping = {}
    for key in ("normalized", "redirects"):
        for m in q.get(key, []):
            mapping[m["from"]] = m["to"]
    pages = {p.get("title"): p for p in q.get("pages", [])}
    out = {}
    for t in titles:
        cur = t
        for _ in range(3):
            cur = mapping.get(cur, cur)
        p = pages.get(cur)
        revs = (p or {}).get("revisions") or []
        text = revs[0].get("slots", {}).get("main", {}).get("content", "") if revs else ""
        out[t] = (extract(text) if text else None, text or None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--titles", nargs="*", default=[])
    ap.add_argument("--dataset-dir", default="")
    ap.add_argument("--approved", default="")
    ap.add_argument("--out", default="moegirl_wikitext_names.json")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=0, help="chỉ tải tối đa N trang (để thử)")
    a = ap.parse_args()

    titles = list(a.titles)
    if not titles:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from moegirl_index import MoegirlIndex, _read_tsv
        idx = MoegirlIndex(a.dataset_dir, a.approved)
        seen = set()
        for row in _read_tsv(a.approved):
            for key in (k.strip() for k in row.get("subject_keys", "").split("|") if k.strip()):
                for ch in idx.characters_of(key):
                    if ch.page_title not in seen:
                        seen.add(ch.page_title); titles.append(ch.page_title)
    out_path = Path(a.out)
    cache = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() else {}
    todo = titles if a.titles else [t for t in titles if t not in cache]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(titles):,} trang, {len(todo):,} cần tải.")
    raws = {}
    cookie = os.environ.get("MOEGIRL_COOKIES", "").strip().strip('"')
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        try:
            parsed = parse_batch(fetch(chunk, cookie), chunk)
            for t, (res, text) in parsed.items():
                raws[t] = text
                if text:  # chỉ ghi cache khi đã tải được trang; không tải được thì để lần sau thử lại
                    cache[t] = res
        except Exception as e:  # dừng ngay, không thử dồn dập; chạy lại sẽ tiếp tục
            print(f"Dừng ở lô {i // a.batch + 1}: {e}")
            break
        out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        got = sum(1 for t in chunk if cache.get(t))
        loaded = sum(1 for t in chunk if raws.get(t))
        print(f"  {min(i + a.batch, len(todo))}/{len(todo)}  (tải được: {loaded}/{len(chunk)}, có 姓名: {got}/{len(chunk)})")
        time.sleep(a.delay)
    for t in a.titles:
        text = raws.get(t)
        print(f"\n=== {t} ===")
        if not text:
            print("Không tải được wikitext (trang không tồn tại hoặc API trả rỗng).")
            continue
        Path("wikitext_debug.txt").write_text(text, encoding="utf-8")
        print(f"Wikitext: {len(text):,} ký tự (đã lưu wikitext_debug.txt). Kết quả: {cache.get(t)}")
        shown = 0
        for line in text.splitlines():
            if re.search(r"姓名|名字|本名|日文名|Hikigaya|ひきがや|基本资料", line):
                print("  |", line.strip()[:220]); shown += 1
                if shown >= 10:
                    break
        if not shown:
            print("  (không dòng nào chứa 姓名/名字/本名/日文名/Hikigaya/ひきがや; mở wikitext_debug.txt để xem)")


if __name__ == "__main__":
    main()