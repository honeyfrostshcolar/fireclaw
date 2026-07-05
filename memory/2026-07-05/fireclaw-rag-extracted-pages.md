# FireClaw RAG Page Extraction

**Date:** 2026-07-05
**Status:** Page-level extraction completed for the current English fire-rescue PDF corpus.

## Task Goal

The user decided to start the RAG pipeline with the existing PDF corpus and asked to implement only the `extracted/` stage first, without chunking or indexing.

## Design Decision

The first extraction layer is page-level JSONL, not Markdown and not semantic chunks.

Reasoning:

- `extracted/` should be a faithful, auditable intermediate layer.
- Page-level extraction preserves PDF page numbers for later RAG citations.
- Semantic chunking belongs in a later `chunks/` layer.
- Markdown can be generated later for human review, but JSONL is easier for programmatic metadata and downstream chunking.

## Files Added

- `src/fireclaw_core/rag/__init__.py`
- `src/fireclaw_core/rag/extraction.py`
- `src/fireclaw_core/rag/corpus_extraction.py`
- `src/fireclaw_core/rag/rag_cli.py`
- `tests/test_rag_extraction.py`

`pyproject.toml` was not updated because the `apply_patch` tool repeatedly failed to read that file in this Windows sandbox. The CLI can still be invoked as a module once a real Python environment is available:

```powershell
python -m fireclaw_core.rag.rag_cli extract-pages
```

## Extraction Output

Generated files:

- `data/rag/fire_rescue/extracted/pages.jsonl`
- `data/rag/fire_rescue/extracted/extraction_report.json`

Output summary:

- Documents: `28`
- Succeeded: `28`
- Failed: `0`
- Pages / JSONL lines: `3906`
- Characters: `7,857,510`
- Extraction method used for generated artifacts: `pdftotext`

Each `pages.jsonl` row contains:

- `doc_id`
- `source_file`
- `page`
- `text`
- `char_count`
- `word_count`
- `text_quality`
- `extraction_method`
- `title`
- `source_url`
- `publisher`
- `authority_level`
- `allowed_use`
- `domain`
- `language`

## Commands / Verification

- Verified `pdftotext` availability outside sandbox:
  - `pdftotext -h`
- Batch extracted all PDFs under:
  - `data/rag/fire_rescue/raw`
- Verified extraction report:
  - status: `completed`
  - document_count: `28`
  - page_count: `3906`
- Verified `pages.jsonl` line count equals report page count.
- Parsed first and last JSONL records with PowerShell `ConvertFrom-Json`.
- First record included expected metadata for:
  - `arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection`
- Last record included expected metadata for:
  - `nist_fds_6_11_0_user_guide`

## Environment Notes

- The workspace currently does not have a usable Python interpreter on PATH.
- `python.exe` points to the Windows Store stub:
  - `C:\Users\lenovo\AppData\Local\Microsoft\WindowsApps\python.exe`
- `py` and `pytest` were not found.
- Because of that, the new Python unit tests were not executed in this environment.
- `pdftotext` is available at:
  - `C:\Users\lenovo\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdftotext.exe`
- `pdftotext` initially needed external execution because MiKTeX setup wrote outside the workspace sandbox.

## Notable Extraction Issue

`nist_fds_6_11_0_user_guide.pdf` emitted a `pdftotext` font encoding warning:

```text
Syntax Error: Unknown character collection 'PDFAUTOCAD-Indentity0'
```

The text output was still produced and was accepted. The report records this warning for that document.

## Next Recommended Step

Build the `chunks/` stage from `pages.jsonl`, using page metadata as source of truth:

- start with page-aware chunking;
- preserve `doc_id`, `source_file`, `page_start`, `page_end`, `title`, `source_url`, `authority_level`;
- target approximately 500-1000 tokens per chunk with overlap;
- do not connect to robot execution yet.
