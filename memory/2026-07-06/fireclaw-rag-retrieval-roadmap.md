# FireClaw RAG Retrieval Roadmap

**Date:** 2026-07-06
**Last update:** 2026-07-06 19:47:04 +08:00
**Status:** Retrieval roadmap decided; implementation not started in this note.

## Task Goal

Continue FireClaw RAG work after the index-preparation layer. The user asked whether to use a local vector index, then clarified that a simplified model-free local retriever is not ideal for the research direction. The agreed route is to implement a stronger retrieval stack in stages so later experiments can support ablation comparisons.

## Current Progress

The RAG preprocessing pipeline is already implemented and validated:

- extraction tests pass;
- chunking tests pass;
- index-preparation tests pass;
- `small_index_records.jsonl` is the intended embedding input;
- use only records with `indexable == true`;
- use `clean_text` as the retrieval text;
- preserve `chunk_id`, `parent_id`, document/source metadata, and traceability;
- retrieve small chunks first, then expand context through `parent_id` into parent chunks.

The current branch is `rag-dev`, already pushed to GitHub as `origin/rag-dev`.

## User Decision

The user prefers not to start with a purely model-free local retriever as the main implementation because that part is not technically strong enough and does not best support the research plan.

The chosen roadmap is:

1. dense embedding retrieval first;
2. add keyword retrieval with BM25;
3. combine dense retrieval and BM25 as hybrid retrieval;
4. add reranking;
5. compare these stages through ablation experiments.

## Engineering Plan

Implement the retrieval stack incrementally with stable interfaces:

- `EmbeddingProvider`
  - abstracts the embedding backend;
  - should allow future local models and API-based embedding services;
  - tests should not require network or a real model, so use deterministic fake embeddings in unit tests.

- `DenseRetriever`
  - reads indexable records from `small_index_records.jsonl`;
  - embeds `clean_text`;
  - builds a local dense vector index;
  - returns top-k small chunk hits with scores and metadata.

- Parent expansion
  - given dense retrieval hits, load `parent_chunks.jsonl`;
  - expand by `parent_id`;
  - keep citation metadata and source traceability.

- `BM25Retriever`
  - indexes the same `clean_text` records;
  - emphasizes exact firefighting terms and procedural wording.

- `HybridRetriever`
  - combines dense and BM25 retrieval;
  - candidate fusion can start with Reciprocal Rank Fusion or normalized weighted score fusion;
  - keep the fusion method explicit so it can be ablated.

- Reranker layer
  - reranks top-N candidates from dense/BM25/hybrid retrieval;
  - should be interface-based, because actual reranker backend may be local or API-based.

## Research Plan

The retrieval stages naturally support ablation comparisons:

- BM25 only;
- dense only;
- dense + BM25 hybrid;
- dense + BM25 + reranker.

Expected research questions:

- Does dense retrieval improve semantic matching for natural-language operator commands?
- Does BM25 improve precise recall for firefighting terms, equipment names, procedures, and standards?
- Does hybrid retrieval outperform either dense-only or BM25-only retrieval?
- Does reranking reliably move truly relevant chunks into the top results?
- Does parent expansion improve answer grounding and citation quality without adding too much irrelevant context?

This route is more publication-aligned than simply wiring a single enterprise model API. Enterprise models can still be added later as stronger embedding/reranking backends or as baselines, but the main research contribution should remain the FireClaw retrieval architecture, safety-relevant grounding, traceability, and evaluation.

## Important Design Constraint

Do not tie the RAG system to one provider. The first dense implementation may use whichever embedding backend is available, but the FireClaw code should keep retrieval and embedding provider boundaries clear.

No local embedding model is currently confirmed on this device. If using a real dense embedding backend next, either:

- install/download a local Chinese or multilingual embedding model; or
- connect an API embedding provider if credentials are available; or
- implement the dense index interfaces and tests with deterministic fake embeddings first, then add a real provider.

## Local Hardware Feasibility Note

Checked this device on 2026-07-06:

- machine: Dell Precision 5820 Tower X-Series;
- CPU: Intel Core i9-10900X, 10 cores / 20 logical processors;
- RAM: about 16 GB;
- GPU: Windows currently reports only `Microsoft 基本显示适配器`;
- `nvidia-smi` did not run successfully and CUDA/NVIDIA GPU should not be assumed available;
- disk free space is sufficient for model files and generated indexes:
  - C: about 332 GB free;
  - D: about 1.92 TB free;
  - E: about 470 GB free;
- current Python 3.12.4 environment does not yet have `torch`, `sentence_transformers`, or `FlagEmbedding` installed.

Conclusion: BAAI/bge-m3 should be feasible on CPU after installing dependencies and downloading the model, but it will not be fast. With about 5790 chunks it is still a realistic offline indexing job. If dependency installation or runtime memory becomes painful, use `Alibaba-NLP/gte-multilingual-base` as a lighter dense embedding fallback.

Additional correction on 2026-07-06:

- The user explained that this Windows system disk was moved from a broken previous computer into the current computer.
- The previous computer had a GTX 1660, while the current computer should have an RTX 3060 Ti.
- Follow-up device inspection showed:
  - `NVIDIA GeForce GTX 1660 SUPER` is `CM_PROB_PHANTOM`, so it is a stale/phantom device record from the old machine;
  - current active NVIDIA hardware appears as `Microsoft 基本显示适配器` with hardware ID `PCI\VEN_10DE&DEV_2486...`;
  - this strongly suggests the current NVIDIA driver is missing or mismatched after the disk transplant;
  - `nvidia-smi` still cannot access a working NVIDIA driver.
- Before installing GPU PyTorch, install/fix the correct NVIDIA driver for the current RTX 3060 Ti and verify `nvidia-smi` works.

Driver repair attempt update:

- Existing local installer `C:\Users\L\Downloads\556.12-desktop-win10-win11-64bit-international-dch-whql.exe` was found.
- It extracted to `C:\NVIDIA\DisplayDriver\556.12\Win11_Win10-DCH_64\International`.
- The extracted `nvddi.inf` supports the current device ID `PCI\VEN_10DE&DEV_2486&SUBSYS_39711028`.
- NVIDIA setup UI appeared to exit/cancel during compatibility checking.
- Manual `pnputil /add-driver ...\nvddi.inf /install` failed with `0x00000002`.
- `C:\Windows\INF\setupapi.dev.log` showed the extracted driver directory was missing files:
  - first `dbInstaller.exe`;
  - then `cudnn_infer64_7.dll`;
  - then `Display.NvContainer\nvgwls.exe`.
- This indicates the existing `C:\NVIDIA\...` extracted directory is incomplete or stale. Stop patching it file-by-file.
- Next recommended driver step: cleanly redownload or cleanly re-extract a full official NVIDIA driver package for RTX 3060 Ti, then run the installer with clean installation.

Driver fix success:

- User downloaded `C:\Users\L\Downloads\610.62-desktop-win10-win11-64bit-international-nsd-dch-whql.exe`.
- Installed NVIDIA Studio Driver 610.62 with graphics driver only and clean installation.
- Post-install validation:
  - active display device: `NVIDIA GeForce RTX 3060 Ti`;
  - driver name: `oem17.inf`;
  - driver version from WMI: `32.0.16.1062`;
  - `nvidia-smi` works;
  - reported driver version: `610.62`;
  - reported CUDA version: `13.3`;
  - GPU memory: `8192 MiB`;
  - previous GTX 1660 remains only as disconnected/phantom device.
- It is now reasonable to install GPU-enabled PyTorch in `.venv-bge-m3` and test BGE-M3 on CUDA.

PyTorch GPU environment update:

- Dedicated environment remains:
  - `.venv-bge-m3`
  - use `.\.venv-bge-m3\Scripts\python.exe`
- Avoid bare `python` for BGE work because it resolves to Anaconda:
  - `E:\anaconda3\python.exe`
- A first attempt with `torch 2.11.0+cu128` installed but failed to import with `WinError 1114` while loading `torch\lib\c10.dll`.
- The broken `torch 2.11.0+cu128` install was removed from `.venv-bge-m3`; leftover temporary package directories were cleaned.
- Installed stable PyTorch CUDA 12.1 wheel on 2026-07-06 19:02 +08:00:
  - command target: `.\.venv-bge-m3\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121`
  - installed version: `torch 2.5.1+cu121`
  - installed location: `C:\Users\L\Desktop\lpp\fireclaw-master\.venv-bge-m3\Lib\site-packages`
  - dependency adjusted by pip: `sympy 1.13.1`
- Validation succeeded:
  - `import torch` works;
  - `torch.cuda.is_available()` is `True`;
  - `torch.version.cuda` is `12.1`;
  - `torch.cuda.device_count()` is `1`;
  - CUDA device name is `NVIDIA GeForce RTX 3060 Ti`;
  - a test tensor was created on `cuda:0`.
- Bare `python -m pip show torch` still reports `Package(s) not found: torch`, so the Anaconda/global environment was not polluted.
- Minor remaining note:
  - Torch import emitted `No module named 'numpy'` warning during one smoke test.
  - This is not a Torch CUDA failure; install `numpy` or let later `FlagEmbedding`/`transformers` dependencies install it before BGE-M3 testing.

BGE-M3 deployment update:

- Installed BGE-M3 runtime dependencies into `.venv-bge-m3`.
- Initial `pip install numpy FlagEmbedding` pulled very new packages:
  - `FlagEmbedding 1.4.0`
  - `numpy 2.5.1`
  - `pyarrow 24.0.0`
  - `pandas 3.0.3`
  - `scikit-learn 1.9.0`
  - `transformers 5.13.0`
  - `datasets 5.0.0`
- That combination caused a Windows `access violation` during `import FlagEmbedding`, traced with `python -X faulthandler` to the `datasets -> pyarrow` import path.
- Stabilized the dependency set by pinning/downgrading the fragile packages:
  - `numpy 1.26.4`
  - `pyarrow 17.0.0`
  - `pandas 2.2.3`
  - `scikit-learn 1.5.2`
  - `scipy 1.14.1`
  - `transformers 4.44.2`
  - `datasets 2.21.0`
  - `sentence-transformers 3.1.1`
  - `accelerate 0.34.2`
  - `peft 0.13.2`
- After pinning, `import FlagEmbedding` and `from FlagEmbedding import BGEM3FlagModel` both succeeded.
- Official `huggingface.co` model download failed from Python with repeated `SSLEOFError`.
- `HF_ENDPOINT=https://hf-mirror.com` through `huggingface_hub` also failed because the hub client's metadata checks did not accept the mirror response.
- Direct `curl.exe` access to `https://hf-mirror.com/BAAI/bge-m3/...` worked.
- Manually downloaded only the PyTorch/local-inference files to avoid pulling the full ONNX payload:
  - model directory: `.cache/models/bge-m3`
  - `config.json` 687 bytes
  - `tokenizer_config.json` 444 bytes
  - `special_tokens_map.json` 964 bytes
  - `sentencepiece.bpe.model` 5,069,051 bytes
  - `tokenizer.json` 17,098,108 bytes
  - `pytorch_model.bin` 2,271,145,830 bytes
  - `sparse_linear.pt` 3,516 bytes
  - `colbert_linear.pt` 2,100,674 bytes
- Added `.cache/` to `.gitignore` so local model/cache files are not accidentally tracked.
- Compatibility patch applied inside the local virtual environment:
  - file: `.venv-bge-m3/Lib/site-packages/FlagEmbedding/finetune/embedder/encoder_only/m3/runner.py`
  - changed `dtype=torch_dtype` to `torch_dtype=torch_dtype`
  - reason: `FlagEmbedding 1.4.0` passes the newer `dtype` argument, while the stabilized `transformers 4.44.2` expects `torch_dtype`.
- BGE-M3 GPU smoke test succeeded on 2026-07-06 19:46 +08:00:
  - command target: `.\.venv-bge-m3\Scripts\python.exe .tmp\bge_m3_smoke.py`
  - model path: `.cache/models/bge-m3`
  - device: `NVIDIA GeForce RTX 3060 Ti`
  - torch: `2.5.1+cu121`
  - CUDA available: `True`
  - model load time: about `1.990s`
  - encoding time for two short Chinese sentences: about `1.119s`
  - dense output type: `numpy.ndarray`
  - dense output shape: `(2, 1024)`
  - first vector norm: about `0.999756`
- Current conclusion:
  - BAAI/bge-m3 is now deployed and runnable locally with GPU on this device.
  - The next engineering step is to add a real BGE-M3 `EmbeddingProvider` behind the RAG dense retrieval interface, while keeping unit tests model-free with deterministic fake embeddings.

## Dense Index Compatibility Constraint

Clarified with the user on 2026-07-06:

- Dense document vectors can be built offline on a workstation and the resulting index files can be copied to the robot-side computer.
- The robot-side dense search process does not need to re-embed all documents.
- However, the robot-side query still needs to be embedded into the same vector space as the document vectors.
- A dense index built with one embedding model generally cannot be queried with a different embedding model, even if both output the same vector dimension.
- Model-specific details also matter: model name, revision, pooling method, normalization, query/passage prefix or instruction, tokenizer/max length, and vector dimension should be recorded in the index manifest and validated at load/query time.

Deployment implication:

- If the robot runs the same embedding model or a compatible exported/quantized version of it, it can use the workstation-built dense index.
- If the robot uses a different smaller embedding model, build a separate dense index with that model.
- If the robot cannot run any dense query encoder reliably, use BM25 as the onboard fallback, or compute query embeddings/retrieval offboard and send results to the robot.
- For experiments, keep separate indexes for research and edge profiles instead of mixing embeddings from different models.

## Embedding Model Comparison Plan

Updated on 2026-07-06 after user proposed an edge-oriented model comparison; corrected after user clarification that only MiniLM is intended for ONNX Runtime.

The user wants to compare two dense embedding paths:

- `BAAI/bge-m3`: keep the earlier plan, run locally on CPU as a stronger research/workstation embedding model.
- `sentence-transformers/all-MiniLM-L6-v2`: run separately through ONNX Runtime as the lightweight robot-side candidate.

Planned comparison logic:

- build one dense index per model;
- run the same Chinese firefighting/RAG evaluation queries against each index;
- compare retrieval quality, query latency, memory footprint, model size, and index size;
- if quality is similar, prefer the smaller/faster model for robot-side deployment;
- if quality differs greatly, keep the stronger model for research/offboard profile and look for a better edge model or quantization path.

Important caveat:

- `all-MiniLM-L6-v2` is a very small and fast English sentence-transformer model, but it is not a Chinese/multilingual-first model.
- If the FireClaw corpus and operator queries remain mainly Chinese, `all-MiniLM-L6-v2` should be treated as an edge lower-bound baseline rather than a likely final onboard model.
- A fairer small multilingual edge candidate may be `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` or another compact multilingual embedding model.
- Do not mix indexes between models. The BGE-M3 index must be queried with BGE-M3-compatible query embeddings; the MiniLM index must be queried with MiniLM-compatible query embeddings.

## Next Recommended Step

Start implementation with:

1. inspect existing `src/fireclaw_core/rag/` modules and CLI shape;
2. add dense retrieval interfaces and local vector index serialization;
3. add tests using deterministic fake embeddings;
4. add CLI entry points for building and querying the dense index;
5. only after this works, choose or configure a real embedding backend.

## Validation Target

Keep existing RAG tests passing:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_rag_elevated tests/test_rag_extraction.py tests/test_rag_chunking.py tests/test_rag_index_preparation.py
```

Add new dense retrieval tests once implementation begins, ideally without requiring network or a downloaded model.

## Dense Retrieval Design Update

**Timestamp:** 2026-07-06 20:30:41 +08:00

The user approved scope B for the first dense retrieval implementation:

- model-independent dense retrieval core;
- deterministic fake embedding provider for unit tests;
- Numpy exact dense vector index;
- BGE-M3 provider using the already deployed local model at `.cache/models/bge-m3`;
- CLI commands to build and query dense indexes;
- parent expansion from small chunk hits to `parent_chunks.jsonl`.

Wrote design spec:

- `docs/superpowers/specs/2026-07-06-fireclaw-dense-retrieval-design.md`

Important implementation decisions in the spec:

- Use only `indexable == true` rows from `small_index_records.jsonl`.
- Embed `clean_text`.
- Store dense indexes as `manifest.json`, `vectors.npy`, and `records.jsonl`.
- Validate provider/model compatibility at query time so BGE-M3 and MiniLM indexes cannot be mixed.
- Keep BGE-M3 out of normal unit tests; use deterministic fake embeddings for tests and a separate manual BGE-M3 smoke path.
- Add `numpy>=1.26` as a FireClaw dependency when implementing dense retrieval.

Superpowers workflow state:

- Brainstorming design is written and self-reviewed.
- No production code has been modified yet.
- The user should review or approve the written spec before proceeding to the implementation plan and TDD implementation.

## Dense Retrieval Implementation Update

**Timestamp:** 2026-07-06 21:09:27 +08:00

Implemented the first dense retrieval stage using the Superpowers writing-plans and TDD workflow.

Files added:

- `docs/superpowers/specs/2026-07-06-fireclaw-dense-retrieval-design.md`
- `docs/superpowers/plans/2026-07-06-fireclaw-dense-retrieval.md`
- `src/fireclaw_core/rag/dense_retrieval.py`
- `src/fireclaw_core/rag/bge_m3_provider.py`
- `tests/test_rag_dense_retrieval.py`
- `tests/test_rag_dense_cli.py`

Files modified:

- `.gitignore`
  - added `data/rag/fire_rescue/indexes/` so generated dense indexes are not tracked.
- `pyproject.toml`
  - added `numpy>=1.26`.
- `src/fireclaw_core/rag/rag_cli.py`
  - added `build-dense-index`;
  - added `query-dense-index`;
  - added safe JSON output for Windows console encodings.
- `memory/2026-07-06/fireclaw-rag-retrieval-roadmap.md`
  - this implementation note.

Implemented behavior:

- `build_dense_index()` reads prepared JSONL rows, filters `indexable == true`, embeds `clean_text`, and writes:
  - `manifest.json`
  - `vectors.npy`
  - `records.jsonl`
- `DenseRetriever.load()` reads the manifest, vectors, and records, then validates provider/model compatibility before query.
- `DenseRetriever.query()` embeds the query and computes exact Numpy dot-product scores against all stored document vectors.
- `expand_hits_to_parents()` attaches parent chunk metadata/text from `parent_chunks.jsonl`.
- `FakeEmbeddingProvider` supports deterministic unit tests and fake CLI smoke tests.
- `BGEM3EmbeddingProvider` lazily imports `torch` and `FlagEmbedding` only when actually embedding.
- BGE-M3 provider sets local HuggingFace cache env values to project-local `.cache/huggingface` directories to avoid the previous unwritable user-cache warning.

TDD red-green notes:

- First dense build test failed because `fireclaw_core.rag.dense_retrieval` did not exist, then passed after adding the minimal build implementation.
- Dense retriever tests failed because `DenseRetriever` did not exist, then passed after adding manifest loading, provider compatibility validation, and query scoring.
- Parent expansion test failed because `expand_hits_to_parents` did not exist, then passed after adding parent lookup by `parent_id`.
- CLI test failed because `build-dense-index` was not a known command, then passed after adding fake provider and CLI commands.
- BGE-M3 lightweight import test failed because `bge_m3_provider.py` did not exist, then passed after adding lazy provider implementation.
- Windows GBK JSON output test failed because `_write_json_output` did not exist, then passed after adding safe output replacement for unencodable characters.
- BGE cache env test failed because `_set_huggingface_cache_env` did not exist, then passed after adding project-local cache env setup.

Validation:

- Dense tests:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_final_elevated tests/test_rag_dense_retrieval.py tests/test_rag_dense_cli.py
```

Result:

- `8 passed in 0.42s`

- Existing RAG tests:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_rag_final_dense_elevated tests/test_rag_extraction.py tests/test_rag_chunking.py tests/test_rag_index_preparation.py
```

Result:

- `10 passed in 0.13s`

- Real BGE-M3 build smoke:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli build-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --batch-size 16
```

Result:

- status: `completed`
- input records: `data\rag\fire_rescue\index_inputs\small_index_records.jsonl`
- output dir: `data\rag\fire_rescue\indexes\dense\bge-m3`
- indexable records: `5774`
- skipped records: `16`
- vector count: `5774`
- vector dimension: `1024`
- generated files:
  - `manifest.json` 619 bytes
  - `records.jsonl` 14,137,456 bytes
  - `vectors.npy` 23,650,432 bytes
- build took about 12 minutes on the RTX 3060 Ti environment.

- Real BGE-M3 query smoke:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli query-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --query "smoke-filled victim search" --top-k 1 --parents
```

Result:

- command completed successfully;
- top hit: `nist_smv_6_11_0_user_guide__parent_00012__small_001`;
- score: about `0.538944`;
- note: top hit is a very short Smokeview front-matter chunk with `retrieval_weight=0.5` and `too_short`, which suggests later retrieval quality improvements should consider retrieval-weight adjustment, BM25/hybrid filtering, or reranking.

Known caveats:

- BGE-M3 query still emits non-fatal Transformers/FastTokenizer warnings from dependencies.
- The dense-only query quality is not yet the final RAG quality; BM25, hybrid retrieval, reranking, retrieval-weight handling, and MiniLM ONNX remain later roadmap items.
- The generated BGE-M3 dense index is intentionally ignored by git under `data/rag/fire_rescue/indexes/`.
