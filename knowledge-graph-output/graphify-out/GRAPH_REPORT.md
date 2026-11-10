# Graph Report - knowledge-graph-output  (2026-10-30)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1562 nodes · 3219 edges · 87 communities (62 shown, 25 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 111 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- serve_ui.py
- deepseek_service.py
- NovelBackend
- novel_backend.py
- MoegirlService
- TermManager
- Project
- nv_bindings.js
- moegirl_index.py
- InvalidTranslation
- TermManager
- TermManager
- TermManager
- pageC
- pageD
- translate_text
- .__novel_runner_31256_1793334861490.py
- main
- ApiError
- _check_line
- pageE
- dichTrungsangViet.py
- .__novel_runner_20944_1793274408236.py
- main
- .__novel_runner_27596_1793284817732.py
- main
- main
- re
- json
- AnchorStore
- translate_text
- translate_text
- polish_qwen.py
- moegirl_dataset_import.py
- Path
- read_text_auto
- ApprovalStore
- InvalidTranslation
- InvalidTranslation
- InvalidTranslation
- esc
- log
- process_chunk_api
- fix_han_leftovers
- fix_han_leftovers
- fix_han_leftovers
- bisect_source
- bisect_source
- bisect_source
- bisect_source
- fix_han_leftovers
- _check_line
- _check_line
- _read_text
- check_translation
- check_translation
- check_translation
- _read_text
- _read_text
- translate_text
- make_matcher
- pageG
- argparse
- FailedChunkStore
- Bridge
- FailedChunkStore
- FailedChunkStore
- FailedChunkStore
- AhoCorasick
- ChapterIndex
- _FileFormatter
- ChunkTrace
- _FileFormatter
- ChunkTrace
- _FileFormatter
- ChunkTrace
- _FileFormatter
- RateLimiter
- build_prompt
- load_moegirl_entries
- RateLimiter
- build_prompt
- load_moegirl_entries
- RateLimiter
- load_moegirl_entries
- RateLimiter

## God Nodes (most connected - your core abstractions)
1. `ApiError` - 52 edges
2. `NovelBackend` - 47 edges
3. `pageC()` - 36 edges
4. `pageD()` - 36 edges
5. `pageE()` - 28 edges
6. `MoegirlService` - 26 edges
7. `read_text_auto()` - 24 edges
8. `AnchorStore` - 22 edges
9. `main()` - 22 edges
10. `main()` - 22 edges

## Surprising Connections (you probably didn't know these)
- `wanted_titles()` --calls--> `MoegirlIndex`  [EXTRACTED]
  extract_names_from_dump.py → moegirl_index.py
- `main()` --calls--> `MoegirlIndex`  [EXTRACTED]
  fetch_wikitext_names.py → moegirl_index.py
- `wanted_titles()` --calls--> `_read_tsv()`  [EXTRACTED]
  extract_names_from_dump.py → moegirl_index.py
- `build_names_bgm()` --calls--> `fetch()`  [EXTRACTED]
  names_bgm_service.py → fetch_wikitext_names.py
- `main()` --calls--> `_read_tsv()`  [EXTRACTED]
  fetch_wikitext_names.py → moegirl_index.py

## Import Cycles
- None detected.

## Communities (87 total, 25 thin omitted)

### Community 0 - "serve_ui.py"
Cohesion: 0.06
Nodes (20): serve(), bindings_script(), find_folder(), find_html(), free_port(), Handler, main(), native_pick() (+12 more)

### Community 1 - "deepseek_service.py"
Cohesion: 0.07
Nodes (15): ApiFailure, build_terminology_prompt(), build_translation_prompt(), call_deepseek(), classify_api_error(), clean_content(), count_chapters(), DeepSeekError (+7 more)

### Community 2 - "NovelBackend"
Cohesion: 0.10
Nodes (5): atomic_write(), apply(), NovelBackend, need(), ok()

### Community 3 - "novel_backend.py"
Cohesion: 0.07
Nodes (18): ApiHandler, _authorized(), _conf_key(), _conf_sort(), handle_api_request(), _host_allowed(), _is_main_guard(), main() (+10 more)

### Community 4 - "MoegirlService"
Cohesion: 0.11
Nodes (8): _as_list(), _iter_records(), _latin_entries_for(), MoegirlService, nfc(), _norm_subject(), now_iso(), split_aliases()

### Community 5 - "TermManager"
Cohesion: 0.08
Nodes (6): _is_auto_entry(), _is_han_dominant(), _parse_glossary_lines(), _read_text(), scan_file(), TermManager

### Community 6 - "Project"
Cohesion: 0.08
Nodes (7): clean_source(), decode_bytes(), engine_overrides(), GuardService, load_module_from(), Project, short_id()

### Community 7 - "nv_bindings.js"
Cohesion: 0.13
Nodes (31): base(), boot(), btn(), debounce(), fmtDur(), installNav(), leaf(), noProjectBanner() (+23 more)

### Community 8 - "moegirl_index.py"
Cohesion: 0.11
Nodes (8): CharInfo, is_latin_query(), latin_tokens(), _load(), MoegirlIndex, _read_tsv(), strip_page_suffix(), _latin_key()

### Community 9 - "InvalidTranslation"
Cohesion: 0.08
Nodes (12): _check_line(), _contains_marker(), _find_mapping_line(), _find_markers(), GuardedResult, GuardIssue, InvalidTranslation, _looks_like_dialogue() (+4 more)

### Community 10 - "TermManager"
Cohesion: 0.08
Nodes (6): _extract_json_terms(), _is_auto_entry(), _is_han_dominant(), _parse_line_pairs(), _strip_think(), TermManager

### Community 11 - "TermManager"
Cohesion: 0.08
Nodes (6): _extract_json_terms(), _is_auto_entry(), _is_han_dominant(), _parse_line_pairs(), _strip_think(), TermManager

### Community 12 - "TermManager"
Cohesion: 0.08
Nodes (6): _extract_json_terms(), _is_auto_entry(), _is_han_dominant(), _parse_line_pairs(), _strip_think(), TermManager

### Community 13 - "pageC"
Cohesion: 0.12
Nodes (25): pageC(), aliasesOf(), bindToggle(), closeModal(), doDelete(), dotCls(), fillForm(), importTsv() (+17 more)

### Community 14 - "pageD"
Cohesion: 0.13
Nodes (26): pageD(), bindSpanToggle(), buildProjectCache(), clearFilter(), commit(), dominantReason(), generate(), load() (+18 more)

### Community 15 - "translate_text"
Cohesion: 0.09
Nodes (15): backoff_delay(), build_prompt(), classify_error(), instruction_text(), _latin_pattern(), log(), mask_term(), process_chunk_api() (+7 more)

### Community 16 - ".__novel_runner_31256_1793334861490.py"
Cohesion: 0.14
Nodes (18): append_output(), build_moegirl_catalog(), chunk_id(), chunks_fingerprint(), ensure_moegirl_dataset_cache(), format_duration(), load_progress(), log_error() (+10 more)

### Community 17 - "main"
Cohesion: 0.12
Nodes (17): append_moegirl_auto_terms(), atomic_write_text(), detect_works(), find_untranslated_indices(), get_client(), load_approved_works(), load_glossary(), load_work_anchors() (+9 more)

### Community 18 - "ApiError"
Cohesion: 0.15
Nodes (4): ApiError, build_runner_source(), classify_log(), EngineRunner

### Community 19 - "_check_line"
Cohesion: 0.10
Nodes (12): _check_line(), _contains_marker(), _find_mapping_line(), _find_markers(), GuardedResult, GuardIssue, _looks_like_dialogue(), _normalize() (+4 more)

### Community 20 - "pageE"
Cohesion: 0.15
Nodes (24): fmtBytes(), pageE(), currentInput(), doReplace(), hintOut(), lineHtml(), load(), loadFiles() (+16 more)

### Community 21 - "dichTrungsangViet.py"
Cohesion: 0.13
Nodes (18): build_moegirl_catalog(), chunk_id(), chunks_fingerprint(), ensure_moegirl_dataset_cache(), _latin_pattern(), load_moegirl_entries(), load_progress(), mask_term() (+10 more)

### Community 22 - ".__novel_runner_20944_1793274408236.py"
Cohesion: 0.15
Nodes (20): append_output(), build_moegirl_catalog(), chunk_id(), chunks_fingerprint(), ensure_moegirl_dataset_cache(), format_duration(), load_progress(), log_error() (+12 more)

### Community 23 - "main"
Cohesion: 0.12
Nodes (16): append_moegirl_auto_terms(), atomic_write_text(), detect_works(), find_untranslated_indices(), get_client(), load_approved_works(), load_glossary(), load_work_anchors() (+8 more)

### Community 24 - ".__novel_runner_27596_1793284817732.py"
Cohesion: 0.15
Nodes (20): append_output(), build_moegirl_catalog(), chunk_id(), chunks_fingerprint(), ensure_moegirl_dataset_cache(), format_duration(), load_progress(), log_error() (+12 more)

### Community 25 - "main"
Cohesion: 0.12
Nodes (16): append_moegirl_auto_terms(), atomic_write_text(), detect_works(), find_untranslated_indices(), get_client(), load_approved_works(), load_glossary(), load_work_anchors() (+8 more)

### Community 26 - "main"
Cohesion: 0.12
Nodes (16): append_moegirl_auto_terms(), atomic_write_text(), detect_works(), find_untranslated_indices(), get_client(), load_approved_works(), load_glossary(), load_work_anchors() (+8 more)

### Community 27 - "re"
Cohesion: 0.14
Nodes (13): iter_rows(), main(), _row_keys(), scan(), wanted_titles(), clean_markup(), extract(), fetch() (+5 more)

### Community 28 - "json"
Cohesion: 0.17
Nodes (12): fetch_character(), get_info(), ids_for(), main(), make_entry(), NetError, pairs(), save_json() (+4 more)

### Community 30 - "translate_text"
Cohesion: 0.11
Nodes (13): backoff_delay(), classify_error(), _latin_pattern(), log(), mask_term(), process_chunk_api(), relevant_glossary(), restore_chapter_heading() (+5 more)

### Community 31 - "translate_text"
Cohesion: 0.11
Nodes (13): backoff_delay(), classify_error(), _latin_pattern(), log(), mask_term(), process_chunk_api(), relevant_glossary(), restore_chapter_heading() (+5 more)

### Community 32 - "polish_qwen.py"
Cohesion: 0.17
Nodes (14): build_polish_prompt(), chat(), han_count(), han_phase(), keep_terms(), load_glossary_all(), load_units(), main() (+6 more)

### Community 33 - "moegirl_dataset_import.py"
Cohesion: 0.18
Nodes (12): build_catalog(), clean_latin(), _dedup_latin_entries(), extract_fields(), extract_fields_ex(), find_dataset_dir(), is_latin_name(), main() (+4 more)

### Community 34 - "Path"
Cohesion: 0.14
Nodes (5): _atomic_replace(), file_lock(), LogBuffer, ProgressService, read_sidecar()

### Community 35 - "read_text_auto"
Cohesion: 0.16
Nodes (6): effective_mode(), format_entry(), GlossaryService, parse_glossary(), read_text_auto(), replace_in_glossary()

### Community 36 - "ApprovalStore"
Cohesion: 0.12
Nodes (4): ApprovalStore, deep_merge(), ProjectPaths, validate_config()

### Community 37 - "InvalidTranslation"
Cohesion: 0.14
Nodes (4): GuardedResult, GuardIssue, InvalidTranslation, TranslationGuard

### Community 38 - "InvalidTranslation"
Cohesion: 0.14
Nodes (4): GuardedResult, GuardIssue, InvalidTranslation, TranslationGuard

### Community 39 - "InvalidTranslation"
Cohesion: 0.11
Nodes (6): check_translation(), detect_repetition(), InvalidTranslation, length_ratio(), QualityReport, validate_translation()

### Community 40 - "esc"
Cohesion: 0.20
Nodes (17): download(), esc(), pageB(), load(), renderCounts(), renderDetail(), renderRows(), scan() (+9 more)

### Community 41 - "log"
Cohesion: 0.14
Nodes (8): append_output(), ChunkTrace, format_duration(), log(), log_error(), run_translation(), _translate_one(), translate_with_retry()

### Community 42 - "process_chunk_api"
Cohesion: 0.13
Nodes (7): build_prompt(), estimate_tokens(), _extract_json_terms(), instruction_text(), _parse_line_pairs(), process_chunk_api(), _strip_think()

### Community 43 - "fix_han_leftovers"
Cohesion: 0.14
Nodes (8): _ask_model_for_han_runs(), estimate_tokens(), fix_han_leftovers(), _han_context(), _han_word_by_word_fix(), repl(), limited_create(), _rewrite_lines_with_han()

### Community 44 - "fix_han_leftovers"
Cohesion: 0.14
Nodes (8): _ask_model_for_han_runs(), estimate_tokens(), fix_han_leftovers(), _han_context(), _han_word_by_word_fix(), repl(), limited_create(), _rewrite_lines_with_han()

### Community 45 - "fix_han_leftovers"
Cohesion: 0.14
Nodes (8): _ask_model_for_han_runs(), estimate_tokens(), fix_han_leftovers(), _han_context(), _han_word_by_word_fix(), repl(), limited_create(), _rewrite_lines_with_han()

### Community 46 - "bisect_source"
Cohesion: 0.15
Nodes (7): bisect_source(), max_chars_for_context(), pack_text(), split_clauses(), split_into_chunks(), append_chapter(), split_sentences()

### Community 47 - "bisect_source"
Cohesion: 0.15
Nodes (7): bisect_source(), max_chars_for_context(), pack_text(), split_clauses(), split_into_chunks(), append_chapter(), split_sentences()

### Community 48 - "bisect_source"
Cohesion: 0.15
Nodes (7): bisect_source(), max_chars_for_context(), pack_text(), split_clauses(), split_into_chunks(), append_chapter(), split_sentences()

### Community 49 - "bisect_source"
Cohesion: 0.15
Nodes (7): bisect_source(), max_chars_for_context(), pack_text(), split_clauses(), split_into_chunks(), append_chapter(), split_sentences()

### Community 50 - "fix_han_leftovers"
Cohesion: 0.17
Nodes (7): _ask_model_for_han_runs(), fix_han_leftovers(), _han_context(), _han_word_by_word_fix(), repl(), limited_create(), _rewrite_lines_with_han()

### Community 51 - "_check_line"
Cohesion: 0.17
Nodes (8): _check_line(), _contains_marker(), _find_mapping_line(), _find_markers(), _looks_like_dialogue(), _normalize(), _prep(), scan_text()

### Community 52 - "_check_line"
Cohesion: 0.17
Nodes (8): _check_line(), _contains_marker(), _find_mapping_line(), _find_markers(), _looks_like_dialogue(), _normalize(), _prep(), scan_text()

### Community 53 - "_read_text"
Cohesion: 0.21
Nodes (4): _parse_glossary_lines(), _read_text(), scan_file(), scan_text()

### Community 54 - "check_translation"
Cohesion: 0.17
Nodes (5): check_translation(), detect_repetition(), length_ratio(), QualityReport, validate_translation()

### Community 55 - "check_translation"
Cohesion: 0.17
Nodes (5): check_translation(), detect_repetition(), length_ratio(), QualityReport, validate_translation()

### Community 56 - "check_translation"
Cohesion: 0.17
Nodes (5): check_translation(), detect_repetition(), length_ratio(), QualityReport, validate_translation()

### Community 57 - "_read_text"
Cohesion: 0.25
Nodes (3): _parse_glossary_lines(), _read_text(), scan_file()

### Community 58 - "_read_text"
Cohesion: 0.25
Nodes (3): _parse_glossary_lines(), _read_text(), scan_file()

### Community 59 - "translate_text"
Cohesion: 0.20
Nodes (5): backoff_delay(), classify_error(), restore_chapter_heading(), restore_format(), translate_text()

### Community 60 - "make_matcher"
Cohesion: 0.24
Nodes (3): make_matcher(), page(), WorkDetector

### Community 62 - "pageG"
Cohesion: 0.36
Nodes (7): pageG(), counts(), load(), rowHtml(), scroll(), toggle(), rebind()

### Community 63 - "argparse"
Cohesion: 0.43
Nodes (5): find_info(), ids_of(), load(), main(), show()

## Knowledge Gaps
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TermManager` connect `TermManager` to `.__novel_runner_27596_1793284817732.py`, `_read_text`, `InvalidTranslation`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `pageC()` (e.g. with `nv_bindings.js` and `closeModal()`) actually correct?**
  _`pageC()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `serve_ui.py` be split into smaller, more focused modules?**
  _Cohesion score 0.059562841530054644 - nodes in this community are weakly interconnected._
- **Why does `TermManager` connect `TermManager` to `.__novel_runner_31256_1793334861490.py`, `_read_text`, `InvalidTranslation`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `pageD()` (e.g. with `nv_bindings.js` and `buildProjectCache()`) actually correct?**
  _`pageD()` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Should `deepseek_service.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07183673469387755 - nodes in this community are weakly interconnected._
- **Should `NovelBackend` be split into smaller, more focused modules?**
  _Cohesion score 0.10083256244218317 - nodes in this community are weakly interconnected._