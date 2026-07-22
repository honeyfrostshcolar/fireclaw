# GB55037 Fire Code RAG Bot

A Retrieval-Augmented Generation (RAG) chatbot for querying the Chinese national building fire safety code **《建筑防火通用规范》GB55037-2022**, built end-to-end with LangChain.

## Background

This is the **LangChain implementation** of a three-platform parallel exploration of the same RAG project:

| Platform | Implementation | Control Level |
|---|---|---|
| Coze | Drag-and-drop + config | Low (platform-managed) |
| Dify | Visual workflow | Medium (tunable splitting / retrieval) |
| **LangChain (this repo)** | **Pure code** | **Full** |

The goal: understand RAG end-to-end by writing every component (PDF loader → text splitter → embedder → vector store → retriever → LLM chain) from scratch, instead of relying on pre-built platforms.

## Tech Stack

- **Framework**: LangChain (LCEL)
- **LLM**: DeepSeek-V3 (via OpenRouter)
- **Embedding**: `BAAI/bge-small-zh-v1.5` (512-dim, Chinese-specialized, runs locally on CPU)
- **Vector store**: Chroma (local, persistent)
- **PDF loader**: pypdf

## Project Structure

```
langchain_rag/
├── data/
│   └── gb55037.pdf              # Not in repo — see Setup below
├── chroma_db/                   # Auto-generated, gitignored
├── .env                         # Gitignored — see .env.example
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── test_openrouter.py           # API connectivity test
├── test_langchain_hello.py      # LangChain hello world
├── step2_load_pdf.py            # PDF → page-level Documents
├── step2b_split.py              # Chunk into ~500-char segments
├── step3_test_embedding.py      # Verify BGE embedding works
├── step4_build_vectordb.py      # Embed all chunks + persist to Chroma
└── step5_rag.py                 # Full RAG: retrieve + answer with DeepSeek
```

## Setup

1. **Clone and enter:**
```bash
   git clone <this-repo-url>
   cd langchain_rag
```

2. **Create and activate virtual environment:**
```bash
   python -m venv venv
   venv\Scripts\activate           # Windows
   # source venv/bin/activate      # Mac/Linux
```

3. **Install dependencies:**
```bash
   pip install -r requirements.txt
```

4. **Configure API key:**
   - Sign up at [OpenRouter](https://openrouter.ai) and get an API key
   - Copy `.env.example` to `.env` and fill in your key:
```
     OPENROUTER_API_KEY=sk-or-v1-...
```

5. **Add the PDF:** Place `gb55037.pdf` in `./data/` directory.

6. **Build the vector database** (one-time, ~1 minute):
```bash
   python step4_build_vectordb.py
```

7. **Run the RAG:**
```bash
   python step5_rag.py
```

## Sample Output

**Query**: 民用建筑的耐火等级是怎么规定的?

Response: structured answer citing specific clauses (5.3.1, 5.3.2, 5.3.3) and page numbers, synthesized from top-5 retrieved chunks.

**Query**: 民用建筑的容积率上限是多少? (out of scope)

Response: correctly identifies the question is outside the regulation's scope and refuses to fabricate an answer.

## Engineering Notes

- **API key safety**: All keys loaded via `.env` (gitignored). Never committed.
- **Chinese tuning**: Splitter uses Chinese punctuation (`。 ; ,`) as separators.
- **Local embedding**: BGE model avoids API costs and OpenAI geo-restrictions in mainland China.
- **DeepSeek choice**: Cost-effective, accessible without VPN in mainland China, sufficient quality for RAG generation.

## Author

Built as a learning project while transitioning from architectural design to AI application engineering.