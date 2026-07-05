# FireClaw RAG Chunking Implementation

**Date:** 2026-07-05
**Status:** Chunking code written; tests not executed because no usable Python interpreter is available in the current environment.

## Task Goal

The user approved the previously discussed chunking strategy and asked to start writing the chunking code.

## Implemented Files

- `src/fireclaw_core/rag/chunking.py`
- `src/fireclaw_core/rag/corpus_chunking.py`
- `tests/test_rag_chunking.py`

Updated:

- `src/fireclaw_core/rag/rag_cli.py`

## Implemented Behavior

The chunking stage reads page-level extraction output:

- `data/rag/fire_rescue/extracted/pages.jsonl`

and writes:

- `data/rag/fire_rescue/chunks/parent_chunks.jsonl`
- `data/rag/fire_rescue/chunks/small_chunks.jsonl`
- `data/rag/fire_rescue/chunks/chunk_report.json`

The implementation follows the agreed design:

- page-aware chunking;
- recursive splitting by heading, paragraph, sentence, then fixed character fallback;
- parent chunks as larger answer-context units;
- small chunks as shorter retrieval units;
- `parent_id` linkage from each small chunk back to its parent chunk;
- source metadata preserved on both parent and small chunks.

## Main API

Python API:

```python
from pathlib import Path
from fireclaw_core.rag.corpus_chunking import chunk_corpus_pages

report = chunk_corpus_pages(
    pages_path=Path("data/rag/fire_rescue/extracted/pages.jsonl"),
    output_dir=Path("data/rag/fire_rescue/chunks"),
)
```

CLI:

```powershell
python -m fireclaw_core.rag.rag_cli chunk-pages
```

Useful options:

- `--corpus-root`
- `--pages`
- `--output-dir`
- `--limit-docs`
- `--parent-target-chars`
- `--parent-max-chars`
- `--parent-min-chars`
- `--parent-max-pages`
- `--small-target-chars`
- `--small-max-chars`
- `--small-min-chars`
- `--small-overlap-chars`

## Default Chunking Parameters

Parent chunks:

- `parent_target_chars`: `7000`
- `parent_max_chars`: `9000`
- `parent_min_chars`: `800`
- `parent_max_pages`: `2`

Small chunks:

- `small_target_chars`: `1800`
- `small_max_chars`: `2600`
- `small_min_chars`: `300`
- `small_overlap_chars`: `300`

## Metadata Preserved

Parent chunks and small chunks preserve:

- `doc_id`
- `source_file`
- `page_start`
- `page_end`
- `heading`
- `text`
- `char_count`
- `word_count`
- `title`
- `source_url`
- `publisher`
- `authority_level`
- `allowed_use`
- `domain`
- `language`
- `split_method`

Small chunks additionally preserve:

- `chunk_id`
- `parent_id`

Parent chunks use:

- `parent_id`

## Tests Added

`tests/test_rag_chunking.py` covers:

- recursive splitting prefers headings before character fallback;
- page metadata is preserved;
- small chunks link back to parent chunks;
- corpus-level chunking writes parent JSONL, small JSONL, and report files.

## Verification Status

Attempted:

```powershell
python --version
```

Observed:

```text
python.exe cannot run; system cannot access this file.
```

The current environment still only exposes the Windows Store Python stub:

- `C:\Users\lenovo\AppData\Local\Microsoft\WindowsApps\python.exe`

No `py`, `pytest`, project `.venv`, or bundled workspace Python runtime was available. Therefore tests and real chunk generation were not executed in this session.

## Notable Implementation Detail

Chinese heading detection patterns were written using ASCII `\uXXXX` escapes to avoid Windows PowerShell encoding corruption when editing source files. The regexes still target Chinese structures such as:

- `第一章`
- `一、`
- `（一）`
- `注意：`
- `警告：`

## Next Recommended Step

Install or activate a real Python 3.11+ environment, then run:

```powershell
python -m pytest tests/test_rag_chunking.py
python -m fireclaw_core.rag.rag_cli chunk-pages
```

After generation, inspect:

- `data/rag/fire_rescue/chunks/chunk_report.json`
- a few sample rows from `parent_chunks.jsonl`
- a few sample rows from `small_chunks.jsonl`


## 2026-07-05 Update: Python Installed and Chunks Generated

The user asked to install Python 3.11.

Installed Python:

- version: `Python 3.11.9`
- path: `C:\Users\lenovo\AppData\Local\Programs\Python\Python311\python.exe`

Created project virtual environment:

- `.venv`

Installed project development dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Validation commands completed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rag_extraction.py tests\test_rag_chunking.py
```

Result:

- `6 passed`

Generated real chunk files:

```powershell
.\.venv\Scripts\python.exe -m fireclaw_core.rag.rag_cli chunk-pages
```

Output summary:

- status: `completed`
- documents: `28`
- failed: `0`
- pages: `3906`
- parent chunks: `2095`
- small chunks: `5790`
- parent average chars: `3761.7`
- small average chars: `1485.05`
- parent too long count: `0`
- small too long count: `0`

Generated files:

- `data/rag/fire_rescue/chunks/parent_chunks.jsonl`
- `data/rag/fire_rescue/chunks/small_chunks.jsonl`
- `data/rag/fire_rescue/chunks/chunk_report.json`

Implementation fix made during real generation:

- `_load_pages_by_doc()` now reads `pages.jsonl` with `utf-8-sig` to tolerate UTF-8 BOM.
- `_pack_small_texts()` now drops overlap when `overlap + next piece` would exceed `small_max_chars`, so small chunks do not exceed the configured hard limit.
- Added test assertion that generated small chunks do not exceed `config.small_max_chars`.