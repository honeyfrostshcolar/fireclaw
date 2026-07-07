from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.dense_retrieval import load_jsonl
from fireclaw_core.rag.dense_retrieval import write_jsonl


DEFAULT_BM25_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class BM25TokenizerConfig:
    lowercase: bool = True
    min_token_length: int = 2
    stop_words: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(DEFAULT_BM25_STOP_WORDS)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BM25IndexManifest:
    index_type: str
    created_at: str
    source_records: str
    text_field: str
    record_count: int
    token_count: int
    avg_doc_len: float
    k1: float
    b: float
    tokenizer: BM25TokenizerConfig

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tokenizer"] = self.tokenizer.to_dict()
        return data


@dataclass(frozen=True)
class BM25IndexBuildReport:
    status: str
    input_records: str
    output_dir: str
    indexable_record_count: int
    skipped_record_count: int
    token_count: int
    avg_doc_len: float
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BM25Retriever:
    def __init__(
        self,
        *,
        index_dir: Path,
        manifest: BM25IndexManifest,
        postings: dict[str, list[tuple[int, int]]],
        doc_lengths: list[int],
        records: list[dict[str, Any]],
    ) -> None:
        self.index_dir = index_dir
        self.manifest = manifest
        self.postings = postings
        self.doc_lengths = doc_lengths
        self.records = records

    @classmethod
    def load(cls, index_dir: Path) -> BM25Retriever:
        index_dir = Path(index_dir)
        manifest = read_bm25_manifest(index_dir / "manifest.json")
        index_data = json.loads((index_dir / "index.json").read_text(encoding="utf-8"))
        postings = {
            token: [(int(record_index), int(tf)) for record_index, tf in posting_list]
            for token, posting_list in index_data["postings"].items()
        }
        doc_lengths = [int(length) for length in index_data["doc_lengths"]]
        records = load_jsonl(index_dir / "records.jsonl")
        if len(records) != manifest.record_count:
            raise ValueError(f"Record count {len(records)} does not match manifest count {manifest.record_count}")
        if len(doc_lengths) != manifest.record_count:
            raise ValueError(f"Doc length count {len(doc_lengths)} does not match manifest count {manifest.record_count}")
        return cls(
            index_dir=index_dir,
            manifest=manifest,
            postings=postings,
            doc_lengths=doc_lengths,
            records=records,
        )

    def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        query_tokens = list(dict.fromkeys(tokenize_for_bm25(query, self.manifest.tokenizer)))
        scores: dict[int, float] = defaultdict(float)
        for token in query_tokens:
            posting_list = self.postings.get(token)
            if not posting_list:
                continue
            df = len(posting_list)
            idf = math.log(1.0 + ((self.manifest.record_count - df + 0.5) / (df + 0.5)))
            for record_index, tf in posting_list:
                doc_len = self.doc_lengths[record_index]
                if self.manifest.avg_doc_len <= 0.0:
                    continue
                denominator = tf + self.manifest.k1 * (
                    1.0 - self.manifest.b + self.manifest.b * (doc_len / self.manifest.avg_doc_len)
                )
                scores[record_index] += idf * ((tf * (self.manifest.k1 + 1.0)) / denominator)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], self.records[item[0]].get("chunk_id", "")))[:top_k]
        return [
            DenseHit(rank=rank, score=float(score), record=self.records[record_index])
            for rank, (record_index, score) in enumerate(ranked, start=1)
        ]


def build_bm25_index(
    records_path: Path,
    output_dir: Path,
    *,
    text_field: str = "clean_text",
    k1: float = 1.5,
    b: float = 0.75,
    tokenizer_config: BM25TokenizerConfig | None = None,
) -> BM25IndexBuildReport:
    records_path = Path(records_path)
    output_dir = Path(output_dir)
    if not records_path.exists():
        raise FileNotFoundError(f"Index records JSONL not found: {records_path}")
    if k1 <= 0:
        raise ValueError("k1 must be positive")
    if not 0 <= b <= 1:
        raise ValueError("b must be between 0 and 1")

    cfg = tokenizer_config or BM25TokenizerConfig()
    all_records = load_jsonl(records_path)
    indexable_records = [record for record in all_records if record.get("indexable") is True]
    if not indexable_records:
        raise ValueError(f"No indexable records found in {records_path}")

    postings_by_token: dict[str, dict[int, int]] = defaultdict(dict)
    doc_lengths: list[int] = []
    for record_index, record in enumerate(indexable_records):
        tokens = tokenize_for_bm25(str(record.get(text_field) or ""), cfg)
        counts = Counter(tokens)
        doc_lengths.append(len(tokens))
        for token, tf in counts.items():
            postings_by_token[token][record_index] = int(tf)

    avg_doc_len = sum(doc_lengths) / len(doc_lengths)
    postings = {
        token: [[record_index, tf] for record_index, tf in sorted(posting.items())]
        for token, posting in sorted(postings_by_token.items())
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = BM25IndexManifest(
        index_type="bm25_inverted_index",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_records=str(records_path),
        text_field=text_field,
        record_count=len(indexable_records),
        token_count=len(postings),
        avg_doc_len=float(avg_doc_len),
        k1=float(k1),
        b=float(b),
        tokenizer=cfg,
    )
    manifest_path = output_dir / "manifest.json"
    index_path = output_dir / "index.json"
    records_output_path = output_dir / "records.jsonl"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    index_path.write_text(
        json.dumps({"postings": postings, "doc_lengths": doc_lengths}, ensure_ascii=False),
        encoding="utf-8",
    )
    write_jsonl(records_output_path, indexable_records)

    return BM25IndexBuildReport(
        status="completed",
        input_records=str(records_path),
        output_dir=str(output_dir),
        indexable_record_count=len(indexable_records),
        skipped_record_count=len(all_records) - len(indexable_records),
        token_count=len(postings),
        avg_doc_len=float(avg_doc_len),
        files={"manifest": str(manifest_path), "index": str(index_path), "records": str(records_output_path)},
    )


def read_bm25_manifest(path: Path) -> BM25IndexManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tokenizer_data = dict(data["tokenizer"])
    tokenizer_data["stop_words"] = tuple(tokenizer_data["stop_words"])
    tokenizer = BM25TokenizerConfig(**tokenizer_data)
    return BM25IndexManifest(
        index_type=data["index_type"],
        created_at=data["created_at"],
        source_records=data["source_records"],
        text_field=data["text_field"],
        record_count=int(data["record_count"]),
        token_count=int(data["token_count"]),
        avg_doc_len=float(data["avg_doc_len"]),
        k1=float(data["k1"]),
        b=float(data["b"]),
        tokenizer=tokenizer,
    )


def tokenize_for_bm25(text: str, config: BM25TokenizerConfig | None = None) -> list[str]:
    cfg = config or BM25TokenizerConfig()
    source = text.lower() if cfg.lowercase else text
    stop_words = set(cfg.stop_words)
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(source):
        token = match.group(0)
        if len(token) < cfg.min_token_length:
            continue
        if token in stop_words:
            continue
        tokens.append(token)
    return tokens
