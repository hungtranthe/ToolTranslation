# TODO — Novel Translator Desktop GUI

> File này là **kết quả audit thực tế** ngày 2026-10-27 (quét TODO/FIXME + kiểm tra cấu trúc + chạy smoke test qua
> `NovelBackend.dispatch`). `BACKEND_TODO.md` (2026-10-22) đã **lỗi thời**: nó vẫn mô tả `GuiDich.py` là file đang tồn tại
> trong khi file này đã bị xoá (chỉ còn `__pycache__/GuiDich.cpython-312.pyc`).
>
> Đánh dấu `[x]` khi xong và ghi ngày + cách kiểm chứng vào dòng đó.

## Nhật ký thay đổi — 2026-10-27 (tích hợp DeepSeek vào màn hình E)

Yêu cầu: (1) tích hợp tính năng DeepSeek vào trang E; (2) chuyển nút **Bắt đầu dịch** từ trang A sang trang E;
(3) ở mục chọn engine cho chọn giữa **DeepSeek** và **dichTrungsangViet.py**.

### Đã làm

| # | File / vị trí | Thay đổi |
|---|---|---|
| 1 | `novel_backend.py:230-231` | `DEFAULT_CONFIG["engine_mode"] = "script"` (giá trị `deepseek` \| `script`) |
| 2 | `novel_backend.py:253-255` | Hằng `ENGINE_MODES` + `ENGINE_MODE_LABELS` |
| 3 | `novel_backend.py:310-312` | `validate_config()` chặn `engine_mode` lạ khi lưu config |
| 4 | `novel_backend.py:1119-1122` | `Project.engine_mode()` — chuẩn hoá, mặc định `script` |
| 5 | `novel_backend.py:2477-2501` | `EngineRunner.preflight(deepseek_check=…)`: chế độ DeepSeek bỏ kiểm tra engine/Ollama, thay bằng kiểm tra API key; trả thêm `engine_mode`/`engine_label`/`deepseek` |
| 6 | `novel_backend.py:3241-3267` | API mới **`engine.set_mode(mode, engine="")`**: ghi `engine_mode`, tự điền `APP_DIR/dichTrungsangViet.py` khi chọn `script` mà chưa có engine, lưu ngay `config.json`, trả `project.info` |
| 7 | `novel_backend.py:3341-3347` | `_deepseek_readiness()` (số key/model/base_url, không lộ key) |
| 8 | `novel_backend.py:3522-3537` | `run.preflight` dùng preflight mới; `run.start` trả `ENGINE_MODE_DEEPSEEK` nếu dự án đang chọn DeepSeek (không chạy nhầm engine script) |
| 9 | `novel_backend.py:3448,3506` | Đăng ký `engine.set_mode`, `run.preflight` |
| 10 | `giao_di_n_e_qu_n_l_glossary/code.html:249-338` | Khối **“Engine & Bắt đầu dịch”** trong `<aside>`: 2 radio engine, đường dẫn + “Chọn script”, toàn bộ điều khiển DeepSeek (file, đầu ra, phạm vi chương, key/model/base, Bắt đầu/Dừng/Dịch lại), thanh tiến trình, link sang trang F |
| 11 | `giao_di_n_e_qu_n_l_glossary/code.html:326` | `#nv-ds-actions` (nhóm nút DeepSeek) bị ẩn ở chế độ script |
| 12 | `nv_bindings.js:922-1116` | `installEnginePanel()` trong `pageE`: đọc `project.info` → vẽ đúng engine; `engine.set_mode` khi đổi radio/chọn script; `deepseek.start/stop/reset/status` + poll 2s; `run.preflight` → `run.start` (kèm xác nhận fingerprint / OUTPUT_WITHOUT_PROGRESS) rồi mở trang F |
| 13 | `nv_bindings.js:284-285` | Trang A: bỏ `installDeepSeek()` (143 dòng), nút “Bắt đầu dịch” → “Sang màn hình E (Dịch)” (`NV.go("glossary")`) |
| 13b | `nv_bindings.js:256-264` | Trang A · “Chọn script” trước đây gọi `project.open` + `location.reload()` (không lưu engine); nay gọi `engine.set_mode` nên engine được ghi vào `config.json` |
| 14 | `giao_di_n_a_t_ng_quan_c_u_h_nh/code.html:327-331` | Nút accent “Bắt đầu dịch” → nút điều hướng “Sang màn hình E (Dịch)”; trang A không còn card DeepSeek |
| 15 | `serve_ui.py:186-192` | `Wiring.__init__`: không có `GuiDich.py`/`ENGINE_DEFAULT` thì dùng `APP_DIR/dichTrungsangViet.py` (vá P0.2) |
| 16 | `serve_ui.py:587-593` | `main()` ép `stdout/stderr` sang UTF-8 — trước đó chạy với console cp1252 (hoặc redirect ra file) sẽ `UnicodeEncodeError` ngay khi in cảnh báo và app không khởi động |

### Hành vi sau thay đổi

- **Màn hình E · engine = dichTrungsangViet.py**: hiện đường dẫn engine (tự điền nếu trống) + “Chọn script”;
  **Bắt đầu dịch** = `run.preflight` → `run.start` → tự mở màn hình F (pause/stop/log/SSE).
- **Màn hình E · engine = DeepSeek**: hiện form DeepSeek (file trong thư mục dự án, đầu ra `.deepseek.txt`,
  chương từ/đến, key/model/base URL); **Bắt đầu dịch** = `deepseek.start` (dùng glossary hiện tại), tiến trình hiện
  ngay trong thẻ (`deepseek.status` poll 2s), kèm **Dừng** và **Dịch lại từ đầu**.
- Lựa chọn engine ghi vào `config.json` của dự án (`engine_mode`) nên mở lại dự án vẫn giữ.
- Trang F báo `ENGINE_MODE_DEEPSEEK` nếu bấm start khi dự án đang ở chế độ DeepSeek (thay vì lặng lẽ chạy engine script).
- Trang A chỉ còn lối tắt “Sang màn hình E (Dịch)”; toàn bộ điều khiển đã nằm ở trang E.

### Kiểm chứng (2026-10-27)

```powershell
python -m py_compile novel_backend.py serve_ui.py      # OK
node --check nv_bindings.js                            # OK
python _tmp_smoke_anchors.py                           # ALL PASS (11/11)
```

- Test `dispatch` trên dự án tạm: `engine.set_mode script` → engine = `…\dichTrungsangViet.py` và ghi `config.json`;
  `engine.set_mode deepseek` → `run.preflight` blocker `NO_API_KEY` khi chưa có key, `ready=true` khi có
  `DEEPSEEK_API_KEYS`; `run.start` → `ENGINE_MODE_DEEPSEEK`; mode lạ → `BAD_ENGINE_MODE`.
- E2E HTTP (`python serve_ui.py --port 8792 --no-browser`): `/p/glossary` có `nv-engine-card`, `btn-start-translate`,
  `NV_ROUTE="glossary"`; `POST /api/engine.set_mode {mode:"deepseek"}` → ok, `mode=deepseek`;
  `POST /api/run.preflight` → `engine_mode=deepseek`; `POST /api/engine.set_mode {mode:"script"}` → engine
  `dichTrungsangViet.py`; `config.json` ghi đúng `engine_mode`.
- 26/26 `id` mà `installEnginePanel()` tham chiếu đều tồn tại trong HTML trang E, 0 id trùng.

### Còn lại / gợi ý tiếp

- **P2.10** (chi tiết ở mục P2) Trang F chưa hiển thị tiến trình DeepSeek — `run.status` chỉ đọc engine script.
- **P2.11** (chi tiết ở mục P2) DeepSeek chưa có pause/resume (chỉ dừng sau mỗi chunk — `deepseek_service.py:517-524`).
- **P3.1** (giữ nguyên) API key DeepSeek vẫn là plaintext trong `novel_translator_app.json` (nay nhập ở trang E).

---

## Cách dùng

- Làm theo thứ tự **P0 → P1 → P2 → P3**; P0 chặn toàn bộ luồng dịch.
- Mỗi mục có dạng: `- [ ] **ID** mô tả — *bằng chứng (file:dòng)* → **cách kiểm chứng**.
- Lệnh kiểm chứng nhanh (chạy tại thư mục repo, PowerShell):

```powershell
python -X utf8 -m py_compile novel_backend.py serve_ui.py dichTrungsangViet.py deepseek_service.py
python -X utf8 _tmp_smoke_anchors.py
python -X utf8 _tmp_smoke_moegirl.py
python -X utf8 serve_ui.py --code-dir .           # mở UI ở chế độ /api/* (chưa có GuiDich.py)
```

## Tổng quan trạng thái

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| 7/7 trang HTML nối backend (`pageA..pageG`) | ✅ xong | 0 `NV.call` chưa đăng ký, 0 id HTML thiếu, 41/41 regex rebind khớp |
| 72 API trong registry | ✅ chạy được | 64 API đã có UI (gồm `engine.set_mode` 27/10); **8 API** chưa có UI (xem P1) |
| Engine runner (patch AST + pause/stop + SSE) | ✅ xong | `EngineRunner` (`novel_backend.py:2453-2636`) |
| Chế độ desktop (pywebview) | ❌ bỏ | `serve_ui.py` chạy được khi **không** có `GuiDich.py` (chỉ còn 1 dòng cảnh báo) |
| Engine mặc định của dự án | ✅ xong 27/10 | `serve_ui.py:186-192` fallback `dichTrungsangViet.py`; `engine.set_mode` tự điền khi chọn `script` |
| Chọn engine ở màn hình E (DeepSeek ↔ dichTrungsangViet.py) | ✅ xong 27/10 | `engine_mode` trong config + UI trang E + API `engine.set_mode` |
| Test tự động | ❌ chưa có | chỉ 2 script `_tmp_smoke_*.py`, 1 test lỗi thời (P0.3) |
| UI mock chưa nối | ⚠️ còn | phân trang trang B, modal restart trang F, toolbar, form cấu hình trang A (P2) |
| Vệ sinh repo / bảo mật | ⚠️ còn | API key plaintext, dataset 80 MB, file rác (P3) |

---

## P0 — Chặn luồng chạy (làm trước)

- [ ] **P0.1** Khôi phục chế độ desktop: tạo lại `GuiDich.py` (pywebview + `DesktopBridge` + `ENGINE_DEFAULT`) **hoặc**
      chuyển hẳn sang `serve_ui.py` và bỏ mọi phụ thuộc `GuiDich`
      — *`serve_ui.py:159-194` (`Wiring.__init__`), `serve_ui.py:215-268` (`BrowserDesktopBridge`), `serve_ui.py:549`*.
      → Kiểm chứng: chạy `python serve_ui.py --code-dir .` **không** còn dòng `[cảnh báo] Không có GuiDich.py...`.
- [x] **P0.2** Gán engine mặc định để dự án mới không bị chặn — **XONG 2026-10-27**
      — đã vá: `serve_ui.py:186-192` (fallback `APP_DIR/dichTrungsangViet.py` khi `GuiDich.py`/`ENGINE_DEFAULT` rỗng)
      + API `engine.set_mode` (`novel_backend.py:3241-3267`) tự điền engine khi người dùng chọn `script` ở màn hình E.
      `Project.engine_path()` không còn trả `"."` trong luồng UI.
      → Kiểm chứng: `run.preflight` không còn `ENGINE_NOT_FOUND`; `engine.set_mode {"mode":"script"}` trên dự án mới
      trả `engine = …\dichTrungsangViet.py` và ghi `config.json` (đã chạy test dispatch + E2E HTTP ngày 27/10).
- [ ] **P0.3** Dựng `tests/` thật (pytest) + `requirements.txt`
      — *BACKEND_TODO.md:37 còn treo; repo không có `tests/`, `requirements.txt`, `README`*.
      Tối thiểu: (a) `py_compile` toàn bộ `.py`; (b) smoke `project.open/config.save/anchors.*/moegirl.*/run.preflight`;
      (c) test `guard.run` với engine được set tường minh.
      → Kiểm chứng: `python -m pytest -q` chạy xanh, và chuyển 2 script `_tmp_smoke_*.py` thành test chính thức.
- [ ] **P0.4** Sửa test lỗi thời trong `_tmp_smoke_moegirl.py`
      — dòng 72-75 kỳ vọng `NO_CATALOG`, nhưng `D:\8man\code\moegirl_terms.json` (21.9 MB) đã tồn tại nên
      `moegirl.build_project_cache` chạy đúng → test FAIL oan. Đổi thành: nếu catalog tồn tại thì assert `ok is True`
      + có `path`, ngược lại assert `NO_CATALOG`.
      → Kiểm chứng: `python -X utf8 _tmp_smoke_moegirl.py` → `ALL PASS` (10/10).

---

## P1 — API backend đã có, UI chưa dùng (8 mục + 1 field chết)

- [ ] **P1.1** `works.export`: thêm nút “Xuất báo cáo tác phẩm” ở trang B
      — hiện “Xuất JSON đối chiếu” chỉ `download(JSON.stringify(S.detail))` phía client (`nv_bindings.jsfi:564-567`).
      → Kiểm chứng: xuất được JSON toàn bộ danh sách + chi tiết, file do backend tạo.
- [ ] **P1.2** `works.discard`: thêm nút “Huỷ thay đổi” ở trang B (đối xứng với `works.save`).
- [ ] **P1.3** `files.read_aux`: hiển thị `error.log`/`progress.json` ngay trong UI (trang G hiện chỉ `system.open`;
      trang A không xem được nội dung log phụ).
- [ ] **P1.4** `logs.clear`: nối cho nút “Xóa hiển thị” ở trang G
      — hiện chỉ ẩn phía client bằng `S.since` (`nv_bindings.js:820`), buffer backend không bao giờ xoá.
      → Kiểm chứng: sau khi bấm, `logs.query` trả buffer rỗng/đã reset.
- [ ] **P1.5** `guard.get_config`: hiển thị + sửa ngưỡng guard (min_source_len, min/max_length_ratio, max_han_ratio,
      strict) trên trang G hoặc trang A (`config.save` nhận `patch`).
- [ ] **P1.6** `project.recent` + field `last_source`: làm tính năng “Dự án gần đây”
      — app state có lưu `recent`/`last_source` (`novel_backend.py:3094`) nhưng **không có API đọc** và
      `last_source` chỉ được ghi, không bao giờ đọc → chưa có UI mở lại dự án.
- [ ] **P1.7** `api.describe`: thêm màn hình/tab “API & chẩn đoán” (hữu ích để debug khi UI lệch backend).
- [ ] **P1.8** `config.get`: quyết định giữ hay bỏ (trang A đang dùng `project.info` thay thế) — nếu giữ thì dùng cho
      form cấu hình đầy đủ ở P2.4.

---

## P2 — UI mock / thiếu chức năng trên giao diện

- [ ] **P2.1** Trang B: phân trang thật
      — nút `first_page/chevron_left/chevron_right/last_page` đang `disabled=""`, footer hardcode
      “Hiển thị **1-5** trên **5** tác phẩm” và “Khớp từ kho: **1,412** bộ dữ liệu Moegirl”
      (`giao_di_n_b_b_o_c_o_t_c_ph_m/code.html:370-372`); JS lại tải `page_size: 500` (`nv_bindings.js:512`) nên UI phân trang chết.
      → Kiểm chứng: `works.list` trả `page/pages`, bấm chuyển trang thì bảng + footer (số thật) đổi theo.
- [ ] **P2.2** Trang B: bỏ/không phụ thuộc mock `setRowStatus('WP_0012', ...)` và `bulkUpdateStatus(...)`
      — còn `onclick` mock trong HTML (`giao_di_n_b_b_o_c_o_t_c_ph_m/code.html:317, 330, 355, 358`); hàng do JS render
      thì dùng `data-act` (đúng). Dọn mock để không chạy sai trước khi `load()` hoàn tất.
- [ ] **P2.3** Trang F: dùng `restart-modal` thật hoặc xoá mock
      — modal + `openRestartModal()/executeRestartFresh()` còn trong `giao_di_n_f_d_ch_ti_n_tr_nh/code.html:160, 407, 427, 471-476`
      nhưng JS không mở modal (dùng `window.confirm` tại `nv_bindings.js:710-716`) → dead code; nếu mở modal sẽ chạy
      logic giả (setTimeout).
      → Kiểm chứng: chỉ còn **một** đường restart, gọi `run.restart_fresh` (token 2 bước).
- [ ] **P2.4** Trang A: form cấu hình đầy đủ
      — `patchFromForm()` chỉ trả `{output_name, engine}` (`nv_bindings.js:233-235`) → `translate.*` (max_chars, retries,
      temperature, top_p, workers, rpm/tpm, context_tokens, max_bisect_depth, preserve_indent, target_language),
      `guard.*`, `moegirl.*` (enabled/scope/max_hints/min_frequency/exclude_subjects), `auto_discover.*`,
      `ollama.model/base_url` **không có input** → phải sửa `config.json` tay. Render theo `CONFIG_RULES`
      (`novel_backend.py:252-285`) để validate đúng min/max (dùng luôn `config.describe.rules` ở `:3187`).
- [ ] **P2.5** Trang A: “Chọn cache” đang chỉ hiện toast (`nv_bindings.js:271`) — thay bằng
      `moegirl.rebuild_cache`/`build_project_cache` hoặc điều hướng sang trang D kèm hint.
- [ ] **P2.6** Toolbar mock ở cả 7 trang: “Đồng bộ cấu hình”, “Mở thư mục đích” (titlebar), `minimize`, `close`
      — không có handler trong `nv_bindings.js` lẫn `serve_ui.py` → nối (`system.open`, `config.save`, window controls)
      hoặc xoá khỏi HTML.
- [ ] **P2.7** Trang C: mặc định tải 500 dòng, không có UI chuyển trang (`nv_bindings.js:1203`)
      — thêm phân trang như trang D (`#page-prev/#page-next/#page-numbers`, `nv_bindings.js:1548-1565`) khi dữ liệu lớn.
- [ ] **P2.8** Rà số liệu hardcode còn lại sau khi dữ liệu thật đã render (badge, “1,412 bộ dữ liệu Moegirl”,
      “128 Hợp lệ / 2 Cảnh báo” ở trang C, footer trang B).
- [x] **P2.9** Chuyển card DeepSeek từ inject-JS sang HTML — **XONG 2026-10-27**
      — `installDeepSeek()` (tạo `#nv-ds` bằng `document.createElement`) đã **xoá** khỏi `pageA`;
      toàn bộ UI DeepSeek giờ nằm tĩnh trong `giao_di_n_e_qu_n_l_glossary/code.html:249-338` (`#nv-engine-card`)
      và được nối bởi `installEnginePanel()` (`nv_bindings.js:922-1116`). Kèm nút “Bắt đầu dịch” chuyển sang trang E.
- [ ] **P2.10** Trang F chưa hiển thị tiến trình DeepSeek
      — `run.status` (`novel_backend.py:3511`) chỉ đọc `EngineRunner`/`ProgressService` của engine script, nên khi
      `engine_mode = deepseek` trang F trống. Cần đưa `deepseek.status` (state/percent/chunk/message) vào
      `ProgressService.status()` hoặc cho trang F gọi thêm `deepseek.status` khi chế độ là DeepSeek.
- [ ] **P2.11** DeepSeek chưa có pause/resume
      — `DeepSeekManager.stop()` chỉ đặt `stop_event` và dừng sau chunk hiện tại (`deepseek_service.py:517-524`).
      Nếu cần ngang bằng `EngineRunner` thì thêm `pause_event` + nút “Tạm dừng” trong `#nv-engine-card`.

---

## P3 — Hạ tầng, vệ sinh repo, bảo mật

- [ ] **P3.1** (Bảo mật) Không lưu API key plaintext
      — `novel_translator_app.json` đang chứa `deepseek.api_keys` dạng rõ (`sk-...`), file nằm ngay trong working tree.
      Đề xuất: đọc từ biến môi trường `DEEPSEEK_API_KEYS` (đã hỗ trợ ở `novel_backend.py:3273`) hoặc `keyring`;
      thêm `novel_translator_app.json` vào `.gitignore`; **rotate key** nếu file đã từng bị commit/chia sẻ.
- [ ] **P3.2** Thêm `.gitignore` ở root: `__pycache__/`, `*.pyc`, `novel_translator_app.json`, `*.bak`, `*.bak_*`,
      `runs/`, `*.progress.json`, `*.error.log`, `.idea/`.
- [ ] **P3.3** Xử lý dữ liệu nặng trong repo: `moegirl/` ≈ 80 MB, `bangumi/` ≈ 6 MB
      — chọn: (a) git-lfs/submodule/release asset, hoặc (b) giữ ngoài repo và dùng `find_dataset_dir()`
      + `moegirl.bgm_dir` (cơ chế dò sẵn có: `novel_backend.py:1539-1545`, `moegirl_dataset_import.find_dataset_dir`).
- [ ] **P3.4** Dọn file rác: `giao_di_n_d_g_i_thu_t_ng_moegirl/code.html.bak`, `wikitext_debug.txt`,
      `moegirl_suggestions.tsv` (root), `__pycache__/GuiDich.cpython-312.pyc` + `-313.pyc` (mồ côi), các `__pycache__` khác.
- [ ] **P3.5** Sửa `MOEGIRL_DATASET_DIR` mặc định của engine
      — `dichTrungsangViet.py:125` trỏ `D:\8man\moe_girl_dataset\moegirl-dataset\moegirl` (không tồn tại; dataset thật ở
      `stitch_noveltranslator_desktop_gui/moegirl`). Backend đã chặn bằng `MOEGIRL_AUTO_IMPORT_DATASET=False`
      (`novel_backend.py:2403`) nên GUI không ảnh hưởng, nhưng chạy engine trực tiếp sẽ in cảnh báo
      (`dichTrungsangViet.py:320`) và không nhập được dataset.
      → Kiểm chứng: chạy engine không qua GUI, không còn dòng `[Moegirl Dataset] Không thấy dataset tại ...`.
- [ ] **P3.6** `GuardService` báo lỗi gây nhầm
      — vẫn dò `translation_guard.py` trước rồi raise `NO_GUARD: "Không tìm thấy translation_guard.py (v2) cạnh engine."`
      (`novel_backend.py:2929-2946`) trong khi guard **đã merge** vào `dichTrungsangViet.py`. Đổi thứ tự: load engine
      adapter trước; thông điệp lỗi nói rõ “engine chưa được chọn / thiếu `scan_text`”.
- [ ] **P3.7** Dọn code trùng/dead path trong engine
      — `InvalidTranslation` định nghĩa **2 lần** (`dichTrungsangViet.py:170` và `2186`, do merge guard);
      placeholder v1 `protect_terms` (`:3200`) / `restore_terms` (`:3225`) còn giữ “để tương thích” nhưng không dùng.
- [ ] **P3.8** Rà 2 chỗ engine còn `input()` (chỉ an toàn khi chạy qua GUI)
      — `dichTrungsangViet.py:1337` (index cũ: backend trả lời `y/n` qua stdin) và `:2043` (output tồn tại không có
      progress: backend chặn trước bằng `OUTPUT_WITHOUT_PROGRESS` + `run.restart_fresh`). Nếu engine đổi/thêm câu hỏi mới,
      stdin đã bị đóng (`novel_backend.py:2612-2618`) → `EOFError` giữa lúc dịch. Nên chuyển sang env var/CLI flag.
- [ ] **P3.9** Ghi chú các script CLI **không** nối GUI (nếu là chủ ý thì ghi vào README để khỏi bị coi là thiếu):
      `polish_qwen.py`, `moegirl_index.py`, `inspect_bgm.py`, `probe_extra_info.py`, `extract_names_from_dump.py`.

---

## Phụ lục A — Đã xong (đối chiếu, không cần làm lại)

| Trang | Chức năng đã hoàn thiện | Bằng chứng |
|---|---|---|
| A | `project.info`, `config.save/reset/export`, `ollama.status`, `system.stats`, chọn file/script/glossary, MD5, estimate; **card DeepSeek + nút “Bắt đầu dịch” đã chuyển sang trang E** (nút ở A chỉ còn là lối tắt `NV.go("glossary")`) | `nv_bindings.js:172-289`, `novel_backend.py:3384, 3412-3419` |
| B | bảng tác phẩm động, chọn dòng/chọn tất cả, bulk duyệt–từ chối–đặt lại, lọc + tìm kiếm (Ctrl+K) + sort, panel chi tiết neo, `data-act` từng dòng | `nv_bindings.js:431-569` |
| C | toàn bộ `anchors.*` (10 API): bảng, modal thêm/sửa, xoá có xác nhận, import/export TSV, nạp lại, lưu, kiểm tra, toggle `Aa/\b/.*` | `nv_bindings.js:1065-1394`; smoke **11/11 PASS** |
| D | 12 API `moegirl.*`: danh sách + lọc + tìm, panel chi tiết + sửa, duyệt/từ chối/bỏ duyệt, generate, cache info/dựng cache/dựng lại kho, export TXT/TSV, commit 2 bước, names_bgm view/ensure, phân trang thật | `nv_bindings.js:1397-1985` |
| E | `glossary.read/save/validate/backup/find_replace/insert` + modal nhập Moegirl (`commit_preview`→`commit`), dirty/stage; **mới 27/10**: khối “Engine & Bắt đầu dịch” (`engine.set_mode`, 2 engine, `deepseek.*` 7 API, `run.preflight`+`run.start`) | `nv_bindings.js:713-1119`, `giao_di_n_e_qu_n_l_glossary/code.html:248-338` |
| F | `run.start/preflight/pause/resume/stop/restart_fresh/status(SSE)/chunk/heatmap/update_chunk`, terminal log, preview chunk, xác nhận fingerprint & output-without-progress | `nv_bindings.js:572-760` |
| G | `logs.query/export` + SSE realtime, lọc level/`Aa`/`.*`, đếm theo level, `guard.run`, mở `error.log`/`progress.json` | `nv_bindings.js:761-850` |
| Backend | `EngineRunner` chạy bản sao engine đã vá bằng AST, sidecar progress, pause/stop qua file flag, `LogBuffer`+SSE, config schema v4 + validate + atomic write, `MoegirlService`, `names_bgm_service`, `deepseek_service` | `novel_backend.py:2439-2621` (EngineRunner), `2929-2946` (GuardService), `252-285` (CONFIG_RULES), `116-176` (file_lock/atomic_write) |

## Phụ lục B — Bằng chứng audit (2026-10-27)

- Quét marker: **0** `TODO` / `FIXME` / `HACK` trong toàn bộ `.py`, `.js`, `.html`.
- Registry ↔ JS: **72** API đăng ký, **63** lời gọi `NV.call`, **0** gọi chưa đăng ký;
  9 API đăng ký chưa dùng = `api.describe`, `config.get`, `files.read_aux`, `guard.get_config`, `logs.clear`,
  `project.recent`, `works.discard`, `works.export`. *(Đếm lại 27/10 sau khi thêm `engine.set_mode`: 64 lời gọi,
  8 API chưa dùng — danh sách y như trên, con số “9” của lần audit đầu là lệch 1.)*
- HTML ↔ JS: 0 `id` thiếu ở cả 7 trang (chỉ `nv-ds`, `nv-bgm-modal` do JS tự tạo).
- Rebind: 41/41 regex `btn(...)` khớp text nút thật (`textContent` gồm cả tên icon: `block Từ chối`, `refresh`, `Đổi hết`…).
- `py_compile`: 16/16 file `.py` OK.
- Smoke: `_tmp_smoke_anchors.py` 11/11 PASS; `_tmp_smoke_moegirl.py` 9/10 (1 test lỗi thời — P0.4).
- `guard.run`: engine rỗng → `NO_GUARD`; engine = `dichTrungsangViet.py` → `ok: true, status: pass`
  ⇒ GuardService tốt, lỗi nằm ở engine mặc định (P0.2).
- Kích thước: `novel_backend.py` 3.684 dòng · `dichTrungsangViet.py` 3.353 · `nv_bindings.js` 1.985 · `serve_ui.py` 626 ·
  `deepseek_service.py` 551 · `moegirl_dataset_import.py` 238 · `names_bgm_service.py` 98.
- **Cập nhật 27/10 (DeepSeek + engine ở trang E)**: registry **72** API (thêm `engine.set_mode`), **64** API đã có lời gọi
  trong `nv_bindings.js`, **0** gọi chưa đăng ký, **8** API còn chưa dùng (đếm lại bằng script 27/10 — audit đầu ghi 9);
  trang A không còn `installDeepSeek()`; 26/26 `id` của `installEnginePanel()` có trong HTML trang E, 0 id trùng;
  `node --check` OK; `py_compile` OK; `_tmp_smoke_anchors.py` 11/11 PASS; E2E HTTP `serve_ui.py --port 8792` xác nhận
  `/p/glossary` render thẻ engine mới và `engine.set_mode`/`run.preflight` trả đúng `engine_mode`.
  `serve_ui.py` cũng được vá `UnicodeEncodeError` khi stdout không phải UTF-8 (`serve_ui.py:587-593`).

