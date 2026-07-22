# ArchCode RAG

建筑规范条文级 RAG 检索与问答系统

ArchCode RAG 从建筑规范 PDF 中提取正文条文 构建结构化条文库 并提供精确查询 语义检索 引用式问答 摄入报告和评测能力

## Features

- 多规范 PDF 摄入
- PDF 文本提取和 OCR 回退
- 条文级结构化解析
- 规范编号 章节 条文号 正文 页码和来源文件识别
- 条文号精确查询
- 向量检索 BM25 检索和规则重排
- 基于引用依据的 RAG 回答
- SQLite 和 Chroma 持久化
- Streamlit Web UI
- FastAPI 接口
- 摄入质量报告
- 检索评测和单元测试

## Architecture

```text
PDF
  -> raw text
  -> article parser
  -> articles.jsonl
  -> SQLite and Chroma
  -> retrieval
  -> RAG answer
  -> Web UI and API
```

## Tech Stack

- Python 3.10+
- pdfplumber
- pypdfium2
- RapidOCR
- SQLite
- ChromaDB
- rank-bm25
- jieba
- scikit-learn
- Streamlit
- FastAPI
- pytest

## Project Structure

```text
api/        FastAPI application
config/     settings prompts and standards registry
eval/       evaluation dataset and runner
scripts/    command line entry points
src/        core modules
tests/      unit tests
webui/      Streamlit application
standards/  local PDF folder ignored by Git
```

Generated files are ignored by Git

```text
data/interim/
data/processed/
storage/
reports/
```

## Data Policy

This repository does not publish standard PDFs OCR caches generated databases or vector indexes

Place legally obtained PDFs in `standards/`

## Installation

Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

macOS or Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the environment template

```powershell
Copy-Item .env.example .env
```

Configure standards in `config/standards.yaml`

```yaml
standards:
  gb55037_2022:
    file_name: standards/GB 55037-2022 建筑防火通用规范.pdf
    standard_code: GB 55037-2022
    standard_name: 《建筑防火通用规范》
    status: active
    category: 消防
```

LLM configuration is optional for retrieval-only mode

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
EMBEDDING_BACKEND=hash
```

## Usage

List active standards

```powershell
python scripts\01_extract_pdf.py --list
```

Run ingestion and indexing

```powershell
python scripts\01_extract_pdf.py
python scripts\02_parse_articles.py
python scripts\03_build_index.py
python scripts\07_ingestion_report.py
```

Run retrieval

```powershell
python scripts\04_query_cli.py --retrieve-only --standard "GB 55037-2022" "既有建筑改造需要满足哪些防火要求"
```

Run exact article lookup

```powershell
python scripts\04_query_cli.py --retrieve-only --standard "GB 55037-2022" "1.0.1"
```

Run RAG answer

```powershell
python scripts\04_query_cli.py --standard "GB 55037-2022" "既有建筑改造需要满足哪些防火要求"
```

## Web UI

```powershell
streamlit run webui\app.py
```

Open `http://localhost:8501`

## API

```powershell
uvicorn api.main:app --reload
```

Endpoints

```text
GET  /standards
POST /query
POST /answer
```

Example

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/query `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"question":"既有建筑改造需要满足哪些防火要求","standard_code":"GB 55037-2022","top_k":5}'
```

## Testing

```powershell
pytest
python scripts\06_run_eval.py
```

## Roadmap

- Docker deployment
- GitHub Actions workflow
- Parsed article review UI
- Table extraction improvements
- Larger evaluation dataset
- Citation consistency checks

## License

MIT License

Standard PDFs and standard text content are not covered by this repository license
