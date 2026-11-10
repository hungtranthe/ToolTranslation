# -*- coding: utf-8 -*-
"""Bổ sung kana/Latin từ Bangumi cho các nhân vật mà wikitext Moegirl không cho ra tên.

Đầu vào: file JSON kết quả của extract_names_from_dump.py (moegirl_wikitext_names.json).
Với mỗi trang còn null: tên trang Moegirl -> id Bangumi (moegirl2bgm.json) -> API Bangumi
(https://api.bgm.tv/v0/characters/<id>) -> trường 纯假名 / 罗马字 trong 别名.
Kết quả: file JSON mới (--out), file gốc không bị sửa. Chạy lại được: các id đã tải nằm trong --cache.

Thử vài trang trước:
    python bgm_names.py --names moegirl_wikitext_names.json --bgm-dir <thư mục bangumi> --only 工藤新一 灰原哀
Chạy cho tất cả trang null:
    python bgm_names.py --names moegirl_wikitext_names.json --bgm-dir <thư mục bangumi>
Đối chiếu cả những trang đã có tên (không ghi đè, chỉ thêm khóa "bangumi"):
    python bgm_names.py ... --cross-check

Mục được điền có dạng:
    {"kana", "latin", "latin_converted", "source": "bangumi" | "bangumi-kana", "bgm_id", "bgm_name", ["latin_differs"]}
    latin = 罗马字 của Bangumi nếu có; nếu không thì đổi từ kana. latin_differs=true khi hai cái lệch nhau.
"""
import argparse, json, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kana_romaji import kana_to_romaji

API = "https://api.bgm.tv/v0/characters/{}"
UA = "novel-glossary-helper/0.1 (personal use)"
KANA_KEYS = ("纯假名", "假名", "かな")
ROMAJI_KEYS = ("罗马字", "罗马音", "ローマ字", "Romaji")
ENGLISH_KEYS = ("英文名",)
MAX_IDS = 3  # một trang Moegirl có thể ứng với nhiều id; chỉ thử tối đa chừng này id đầu


class NetError(Exception):
    pass


def fetch_character(cid):
    """Trả về dict JSON, hoặc None nếu 404. Lỗi mạng/429/5xx -> NetError."""
    req = urllib.request.Request(API.format(cid), headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise NetError(f"HTTP {e.code} cho id {cid}")
    except Exception as e:
        raise NetError(f"{type(e).__name__}: {e} (id {cid})")


def pairs(infobox):
    """Duỗi infobox thành các cặp (khóa, chuỗi). Giá trị dạng danh sách {k, v} được tách theo k."""
    for item in infobox or []:
        if not isinstance(item, dict):
            continue
        key, val = item.get("key"), item.get("value")
        if isinstance(val, str):
            yield key, val
        elif isinstance(val, list):
            for sub in val:
                if isinstance(sub, dict):
                    yield sub.get("k") or key, sub.get("v")
                elif isinstance(sub, str):
                    yield key, sub


def summarize(rec):
    kana, romaji, english = [], [], []
    for k, v in pairs(rec.get("infobox")):
        if not isinstance(v, str) or not v.strip():
            continue
        v = v.strip()
        if k in KANA_KEYS:
            kana.append(v)
        elif k in ROMAJI_KEYS:
            romaji.append(v)
        elif k in ENGLISH_KEYS:
            english.append(v)
    return {"name": rec.get("name"), "kana": kana, "romaji": romaji, "english": english}


def ids_for(title, m2b):
    v = m2b.get(title)
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    return [str(x) for x in v]


def get_info(cid, cache, delay):
    if cid not in cache:
        rec = fetch_character(cid)
        cache[cid] = {"missing": True} if rec is None else summarize(rec)
        time.sleep(delay)
    return cache[cid]


def make_entry(ids, cache, delay):
    for cid in ids[:MAX_IDS]:
        info = get_info(cid, cache, delay)
        if not info.get("kana"):
            continue
        kana = info["kana"][0]
        conv = kana_to_romaji(kana)
        bgm_latin = info["romaji"][0] if info.get("romaji") else None
        entry = {"kana": kana, "latin": bgm_latin or conv, "latin_converted": conv,
                 "source": "bangumi" if bgm_latin else "bangumi-kana",
                 "bgm_id": cid, "bgm_name": info.get("name")}
        if bgm_latin and conv and bgm_latin.replace(" ", "").lower() != conv.replace(" ", "").lower():
            entry["latin_differs"] = True
        if cid != ids[0]:
            entry["id_fallback"] = True  # id đầu không có kana nên dùng id sau: kiểm tra bgm_name có đúng nhân vật không
        return entry
    return None


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True, help="moegirl_wikitext_names.json")
    ap.add_argument("--bgm-dir", required=True, help="thư mục chứa moegirl2bgm.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--cache", default="bgm_cache.json")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--only", nargs="*", default=[], help="chỉ xử lý các tên trang này")
    ap.add_argument("--cross-check", action="store_true", help="cũng tra các trang đã có tên (không ghi đè)")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    names_path = Path(a.names)
    names = json.loads(names_path.read_text(encoding="utf-8"))
    m2b = json.loads((Path(a.bgm_dir) / "moegirl2bgm.json").read_text(encoding="utf-8"))
    out_path = Path(a.out) if a.out else names_path.with_name(names_path.stem + "_bgm.json")
    cache_path = Path(a.cache)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}

    targets = [t for t, v in names.items() if (v is None or a.cross_check)]
    if a.only:
        targets = [t for t in a.only if t in names] or list(a.only)
        for t in a.only:
            names.setdefault(t, None)
    if a.limit:
        targets = targets[:a.limit]

    stats = {"filled": 0, "no_bgm_id": 0, "no_kana": 0, "differs": 0}
    stopped = False
    for n, t in enumerate(targets, 1):
        ids = ids_for(t, m2b)
        if not ids:
            stats["no_bgm_id"] += 1
            continue
        try:
            entry = make_entry(ids, cache, a.delay)
        except NetError as e:
            print(f"Dừng ở '{t}': {e}. Chạy lại lệnh này để tiếp tục (id đã tải được lưu trong cache).")
            stopped = True
            break
        if entry is None:
            stats["no_kana"] += 1
        else:
            stats["filled"] += 1
            stats["differs"] += bool(entry.get("latin_differs"))
            if names.get(t) is None:
                names[t] = entry
            else:
                names[t]["bangumi"] = entry
        if n % 20 == 0:
            save_json(cache_path, cache)
            print(f"  {n}/{len(targets)}")
    save_json(cache_path, cache)
    save_json(out_path, names)

    still_null = sum(1 for v in names.values() if v is None)
    print(f"\nĐã xử lý {len(targets):,} trang{' (bị dừng giữa chừng)' if stopped else ''}: "
          f"điền được {stats['filled']:,}, không có id Bangumi {stats['no_bgm_id']:,}, "
          f"có id nhưng không có kana {stats['no_kana']:,}.")
    print(f"Latin của Bangumi lệch với bản tự đổi từ kana: {stats['differs']:,}. Còn null: {still_null:,}. Đã ghi {out_path}")


if __name__ == "__main__":
    main()
