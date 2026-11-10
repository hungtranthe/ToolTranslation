# -*- coding: utf-8 -*-
"""Quét dump Moegirl (HF: mrzjy/chinese_moegirl_wiki_corpus_raw) tìm dòng 姓名：…（kana，Latin）.

Thử một trang:
    python extract_names_from_dump.py --titles 比企谷八幡
Theo tác phẩm đã duyệt:
    python extract_names_from_dump.py --dataset-dir <thư mục moegirl> --approved <moegirl_works_approved.txt>
Nguồn dữ liệu: mặc định tải streaming từ Hugging Face (cần: pip install datasets).
    --local <thư mục>   dùng bản đã tải về (datasets.load_from_disk)
    --jsonl <file>      dùng file jsonl
Nếu không đoán được tên cột, truyền --title-key / --text-key.
"""
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_wikitext_names import extract

HF_NAME = "mrzjy/chinese_moegirl_wiki_corpus_raw"
TITLE_KEYS = ("title", "page_title", "name")
TEXT_KEYS = ("text", "content", "wikitext", "raw")


def iter_rows(a):
    if a.jsonl:
        with open(a.jsonl, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
        return
    import datasets
    if a.local:
        ds = datasets.load_from_disk(a.local)
        ds = ds["train"] if isinstance(ds, datasets.DatasetDict) else ds
    else:
        ds = datasets.load_dataset(HF_NAME, split="train", streaming=True)
    yield from ds


def scan(rows, wanted, title_key="", text_key="", progress=0):
    """Trả về {tiêu đề: (kết quả hoặc None, wikitext)} cho các tiêu đề có trong dump."""
    found, tk, xk = {}, title_key, text_key
    for n, row in enumerate(rows, 1):
        if n == 1:
            print("Các cột:", list(row))
            tk = tk or next((k for k in TITLE_KEYS if k in row), "")
            xk = xk or next((k for k in TEXT_KEYS if k in row), "")
            if not tk or not xk:
                for k, v in row.items():
                    print(f"  {k}: {str(v)[:100]!r}")
                raise SystemExit("Không đoán được tên cột; hãy truyền --title-key và --text-key.")
            print(f"Dùng cột tiêu đề={tk!r}, nội dung={xk!r}")
        title = str(row.get(tk) or "").strip()
        if title in wanted and title not in found:
            text = str(row.get(xk) or "")
            found[title] = (extract(text), text)
            if len(found) == len(wanted):
                break
        if progress and n % progress == 0:
            print(f"  đã quét {n:,} dòng, khớp {len(found)}/{len(wanted)}")
    return found


def _row_keys(row):
    """Các subject key của một dòng file duyệt. Có cột subject_keys (cách nhau bằng '|') thì dùng nó;
    nếu không (file kiểu work_id/work_title/status) thì dùng work_id của dòng có status approved."""
    keys = [k.strip() for k in (row.get("subject_keys") or "").split("|") if k.strip()]
    if keys:
        return keys
    status = (row.get("status") or "approved").strip().lower()
    wid = (row.get("work_id") or row.get("work_title") or "").strip()
    return [wid] if wid and status == "approved" else []


def wanted_titles(a):
    if a.titles:
        return list(a.titles)
    from moegirl_index import MoegirlIndex, _read_tsv
    idx = MoegirlIndex(a.dataset_dir, a.approved)
    out, seen = [], set()
    rows = list(_read_tsv(a.approved))
    keys_total = keys_empty = 0
    empty_samples = []
    for row in rows:
        for key in _row_keys(row):
            keys_total += 1
            chars = list(idx.characters_of(key))
            if not chars:
                keys_empty += 1
                if len(empty_samples) < 5:
                    empty_samples.append(key)
            for ch in chars:
                if ch.page_title not in seen:
                    seen.add(ch.page_title); out.append(ch.page_title)
    print(f"File duyệt: {len(rows)} dòng, {keys_total} key, {keys_empty} key không ra nhân vật nào.")
    if empty_samples:
        print("  Key không ra nhân vật (tối đa 5):", empty_samples)
    if rows:
        print("  Các cột của file duyệt:", list(rows[0]))
        print("  Dòng đầu:", {k: str(v)[:60] for k, v in rows[0].items()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--titles", nargs="*", default=[])
    ap.add_argument("--dataset-dir", default="")
    ap.add_argument("--approved", default="")
    ap.add_argument("--out", default="moegirl_wikitext_names.json")
    ap.add_argument("--local", default="")
    ap.add_argument("--jsonl", default="")
    ap.add_argument("--title-key", default="")
    ap.add_argument("--text-key", default="")
    a = ap.parse_args()

    titles = wanted_titles(a)
    print(f"Cần tìm {len(titles):,} trang.")
    if not titles:
        raise SystemExit("Danh sách trang cần tìm đang rỗng nên không quét dump. Xem dòng 'File duyệt' ở trên: "
                         "file --approved có thể rỗng, thiếu cột subject_keys, hoặc key không khớp char2subject.json.")
    found = scan(iter_rows(a), set(titles), a.title_key, a.text_key, progress=20000)

    out_path = Path(a.out)
    cache = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() else {}
    for t, (res, _) in found.items():
        cache[t] = res
    out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    hit = sum(1 for t in found if cache.get(t))
    print(f"\nTìm thấy trang: {len(found)}/{len(titles)}; trích được 姓名: {hit}/{len(found)}. Đã ghi {out_path}")
    missing = [t for t in titles if t not in found]
    if missing:
        print(f"Không có trong dump ({len(missing)}): {missing[:10]}{' ...' if len(missing) > 10 else ''}")

    if a.titles:
        for t in a.titles:
            if t not in found:
                continue
            res, text = found[t]
            Path("wikitext_debug.txt").write_text(text, encoding="utf-8")
            print(f"\n=== {t} === {len(text):,} ký tự (đã lưu wikitext_debug.txt). Kết quả: {res}")
            shown = 0
            for line in text.splitlines():
                if re.search(r"姓名|名字|本名|日文名|Hikigaya|ひきがや|基本资料", line):
                    print("  |", line.strip()[:220]); shown += 1
                    if shown >= 10:
                        break


if __name__ == "__main__":
    main()