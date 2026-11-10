# BACKEND TODO — Novel Translator Desktop GUI

> ⚠️ **Tài liệu này đã lỗi thời (snapshot 2026-10-22).** Danh sách việc còn lại + trạng thái thực tế hiện được
> theo dõi tại **[`TODO.md`](TODO.md)** (audit 2026-10-27). Các số liệu bên dưới (số dòng file, `GuiDich.py` còn tồn tại)
> không còn đúng — `GuiDich.py` đã bị xoá, `novel_backend.py` hiện 3.684 dòng, `nv_bindings.js` 1.985 dòng.

Trạng thái triển khai backend cho bộ giao diện HTML (PyWebView / HTTP / SSE).

## Kết quả khảo sát (2026-10-22)

### Kiến trúc hiện có
- `novel_backend.py` (2687 dòng): backend chính — `NovelBackend.dispatch()`, ~50 API đã đăng ký, service (Glossary/WorkDetector/Moegirl/EngineRunner/Progress/Guard), transport HTTP JSON + SSE + PyWebView `Bridge`.
- `GuiDich.py` (1562 dòng): app desktop (pywebview) + `DesktopBridge` + HTTP server riêng.
- `serve_ui.py` (561 dòng): serve UI qua trình duyệt, `Wiring` nối `NovelBackend` + `DesktopBridge` + `Bridge`; inject shim (`window.pywebview`, token, `/rpc/*`) + `nv_bindings.js`.
- `nv_bindings.js` (648 dòng): JS nối trang HTML với `/api/*` (dùng `NV.call`).
- `dichTrungsangViet.py` (2248 dòng): engine dịch (đã merge `translation_guard`). **Đã sạch**: `import time` có sẵn, không còn hàm giả `apply_glossary_fallback`/`translate_partial`/`translate_chunk`.
- 7 trang HTML (`giao_di_n_<a..g>_*/code.html`).

### Bảng đối chiếu HTML ↔ backend ↔ JS

| Trang | Route | API backend (đã đăng ký) | JS | Ghi chú |
|---|---|---|---|---|
| A Tổng quan & cấu hình | `tong-quan` | `config.*`, `ollama.status`, `system.*`, `project.*`, `files.read_aux` | ✅ `pageA` | xong |
| B Báo cáo tác phẩm | `bao-cao` | `works.*` | ✅ `pageB` | xong |
| C Nhân vật mốc | `nhan-vat` | `anchors.*` | ✅ `pageC` | **HOÀN TẤT** |
| D Gợi ý Moegirl | `moegirl` | `moegirl.*` | ✅ `pageD` | **HOÀN TẤT** |
| E Quản lý glossary | `glossary` | `glossary.*` (+ `moegirl.commit_preview/commit` cho modal nhập) | ✅ `pageE` | **HOÀN TẤT** |
| F Dịch & tiến trình | `dich-tien-trinh` | `run.*` | ✅ `pageF` | xong |
| G Nhật ký & kiểm tra | `nhat-ky` | `logs.*`, `guard.*` | ✅ `pageG` | xong |

## Tiến độ

### Đã hoàn thành
- [x] Trang E (glossary): `pageE()` nối `glossary.*` + modal nhập Moegirl.
- [x] Trang C (nhan-vat): `pageC()` nối `anchors.*` — render bảng động, tìm kiếm (Aa/`\b`/`.*`), lọc theo tác phẩm + loại, modal thêm/sửa, xóa có xác nhận, import/export TSV, lưu/kiểm tra dữ liệu. Đã smoke-test API qua `NovelBackend.dispatch` (ALL PASS).
- [x] Trang D (moegirl): `pageD()` nối `moegirl.*` — bảng ứng viên động, lọc (tác phẩm/trạng thái/tìm Aa+`.*`), panel chi tiết + form sửa (dst/dst_alt/mode/note), duyệt/từ chối/bỏ duyệt, generate + build/rebuild cache (toast tiến trình), export TXT/TSV, modal commit 2 bước. Đã smoke-test API (ALL PASS 10/10).
- [x] Hook `nv_bindings.js` vào `serve_ui.py` (`bindings_script()` + `serve_page`): trước đây file này **bị bỏ quên** (chỉ inject dock `INJECTED_UI`, không ai đọc `nv_bindings.js`; `NV_ROUTE`/`NV_ALIASES` chưa từng được đặt). Giờ trang được nối thật qua `/api/*`.

### Còn lại
- [ ] Desktop `GuiDich.py` (pywebview) chưa inject `nv_bindings.js` (chế độ desktop dùng bridge khác — `pywebview.api` / `nv_api_client.js`; cần shim token riêng nếu muốn dùng chung `nv_bindings.js`).
- [ ] `tests/` — chưa có; cần thêm kiểm thử nhỏ (syntax, API smoke).

## Ghi chú kỹ thuật
- Pattern JS: `rebind(el, fn)`, `NV.call(method, params)`, `NV.toast(msg, type)`, helper `leaf/withOwn/btn`.
- `anchors.list` trả `{items, works[], kind_counts, avg_weight, total_rows, dirty, total, page, pages, page_size}`; row có `{row_id, source_name, work_id, work_title, aliases, weight, kind, subject_keys, notes}`.
- **Định dạng file neo tương thích 2 chiều**: `AnchorStore.parse()` tự nhận diện header cũ (`type`/`anchor_weight`) và migrate sang schema 8 cột (`type=canon`→`kind=main`, `anchor_weight`→`weight∈[0,1]`, `work_id` cũ = tên tác phẩm → dùng làm `work_id` + `work_title`). Engine `dichTrungsangViet.py` `load_work_anchors()` cũng đọc được cả 2 định dạng nên sau khi lưu từ màn hình C engine vẫn chạy.
- `anchors.add({fields})`, `anchors.update({row_id, fields})` → `{row, dirty}`; `anchors.delete({row_ids})` → `{deleted, dirty}`.
- `anchors.check()` → `{valid, errors[], warnings[], total, can_save}`; `anchors.save()` → `{path, rows, backup}` (từ chối nếu có lỗi).
- `anchors.import_tsv({path|text, mode})` → `{added, updated, dirty}`; `anchors.export_tsv({})` → `{text, rows}`; `anchors.reload()` → `{dirty}`.
- `moegirl.list(**kw)` trả `{items, total, page, pages, page_size, work_counts, status_counts, total_candidates, filtered, filtered_total, generated_at}`; ứng viên có `{id, src, dst, dst_alt, works[], work_titles[], aliases[], count, chapters, source_tag, category, status(pending/approved/rejected), mode(force/hint), note, conflict, committed_at}`.
- `moegirl.detail(cand_id)` → `{...cand, contexts[], glossary_line}`; `moegirl.set_status({cand_ids, status})` → `{updated}`; `moegirl.edit({cand_id, dst, dst_alt, mode, note})` → `{updated}`.
- `moegirl.generate()` → `{candidates, filtered, filtered_total, works}` (cần có tác phẩm đã duyệt ở màn B); `moegirl.build_project_cache()` → `{path, records, works}`; `moegirl.rebuild_cache({dataset_dir})` → `{records, path}`.
- `moegirl.cache_info()` → `{exists, path, record_count, dataset_dir, project_cache, scope}`; `moegirl.export({fmt})` → `{text, rows}`.
- **`moegirl.rebuild_cache`**: thư mục dataset Moegirl đã dời vào `stitch_noveltranslator_desktop_gui/moegirl` (chứa `extra_processed.json`, `char2subject.json`, `char2gender.json`). `moegirl_dataset_import.py` có `find_dataset_dir()` để tự dò (mặc định mới + các vị trí cũ); `rebuild_cache` dùng hàm này + kiểm tra `extra_processed.json` trước khi build.
- **Đường dẫn engine + file Moegirl**: engine `dichTrungsangViet.py` nằm ở `stitch_noveltranslator_desktop_gui\` → đã sửa `GuiDich.py` `ENGINE_DEFAULT = APP_DIR / "dichTrungsangViet.py"` (trước đây trỏ nhầm `APP_DIR.parent`). File Moegirl (`moegirl_dataset_import.py`, `moegirl_terms.json`, `moegirl_index.py`) nằm ở `D:\8man\code` → `novel_backend.py` dùng hằng `CODE_ROOT = Path(__file__).resolve().parent.parent` cho `moegirl_terms_path()` và `rebuild_cache()` (không còn phụ thuộc `engine_path().parent`). `MOEGIRL_TERMS_FILE` của engine được `engine_overrides()` ghi đè bằng `moegirl_terms_path()` nên không cần sửa engine.
- **Đích dịch Moegirl chỉ lấy tên thật**: `extract_fields()` (trong `moegirl_dataset_import.py`, và bản sao `_moegirl_extract_fields` trong engine) giờ chỉ đưa vào `latin_names` (→ `dst` ở màn D) các tên Latin từ `本名` + `LATIN_FIELDS` (英文名/外文名...), **không** lấy từ `别名/别号/昵称/绰号/其他名字`. Đã rebuild `moegirl_terms.json` (backup `moegirl_terms.json.bak_*`); số record có `latin_names` giảm ~12.134 → 9.051.
- `moegirl.commit_preview({cand_ids, conflict_policy})` → `{token, target, add[], conflicts[], already_present[], lines_after_estimate}`; `moegirl.commit({token})` → `{added, committed, backup}`.
- `glossary.read` trả `{path, exists, text, line_count, modified_at, rows, issues, stats, can_save}`.
- `glossary.save(text, allow_errors)`, `glossary.insert(src,dst,mode,note)`, `glossary.find_replace(find,repl,apply,case,whole,regex)`.
- `glossary.find_replace` chỉ thay toàn bộ (subn); không có "thay một chỗ". Trang E dùng "Đổi" = dry-run (apply=false, stage bản nháp) và "Đổi hết" = apply=true (lưu ngay).
- Modal nhập Moegirl: `moegirl.commit_preview()` → `{token, add, conflicts, already_present, lines_after_estimate, target}`; `moegirl.commit(token)`.
