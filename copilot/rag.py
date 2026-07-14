"""Pure-Python TF-IDF retriever over data/runbooks/*.md. No embeddings, no vector
DB, no network — deterministic lexical recall, which is ample for a small
scenario-keyed runbook corpus. Swap in an embedding retriever (Ollama /api/embed
or ChromaDB) behind this same interface for semantic recall on a larger corpus."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _split_chunks(markdown: str) -> list[tuple[str, str]]:
    """(heading, body) per markdown heading section; heading-only sections dropped."""
    chunks: list[tuple[str, str]] = []
    heading = "overview"
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            body = "\n".join(lines).strip()
            if body:
                chunks.append((heading, body))
            heading = line.lstrip("#").strip() or heading
            lines = []
        else:
            lines.append(line)
    body = "\n".join(lines).strip()
    if body:
        chunks.append((heading, body))
    return chunks


@dataclass
class Snippet:
    runbook: str
    heading: str
    text: str
    score: float

    def to_dict(self) -> dict:
        return {"runbook": self.runbook, "heading": self.heading,
                "text": self.text, "score": round(self.score, 4)}


class Retriever:
    def __init__(self, docs: list[tuple[str, str, str]]) -> None:
        """docs: list of (runbook, heading, body)."""
        self.docs = docs
        tokenized = [_tokenize(f"{heading} {body}") for _r, heading, body in docs]
        self.vocab: dict[str, int] = {}
        for toks in tokenized:
            for tok in toks:
                self.vocab.setdefault(tok, len(self.vocab))
        n_docs = len(docs)
        n_terms = len(self.vocab)
        if n_docs == 0 or n_terms == 0:
            self.idf = np.zeros(n_terms)
            self.matrix = np.zeros((n_docs, n_terms))
            return
        tf = np.zeros((n_docs, n_terms))
        df = np.zeros(n_terms)
        for i, toks in enumerate(tokenized):
            for tok in toks:
                tf[i, self.vocab[tok]] += 1.0
            for tok in set(toks):
                df[self.vocab[tok]] += 1.0
        self.idf = np.log((1 + n_docs) / (1 + df)) + 1.0
        mat = tf * self.idf
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix = mat / norms

    @classmethod
    def from_dir(cls, runbooks_dir: Path | str) -> "Retriever":
        docs: list[tuple[str, str, str]] = []
        for path in sorted(Path(runbooks_dir).glob("*.md")):
            for heading, body in _split_chunks(path.read_text()):
                docs.append((path.stem, heading, body))
        return cls(docs)

    def _vectorize(self, query: str) -> np.ndarray:
        vec = np.zeros(len(self.vocab))
        for tok in _tokenize(query):
            j = self.vocab.get(tok)
            if j is not None:
                vec[j] += 1.0
        vec = vec * self.idf
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def retrieve(self, query: str, top_k: int) -> list[Snippet]:
        if not self.docs or not self.vocab:
            return []
        scores = self.matrix @ self._vectorize(query)
        order = np.argsort(-scores, kind="stable")[:top_k]
        hits: list[Snippet] = []
        for i in order:
            if scores[i] <= 0:
                continue
            runbook, heading, body = self.docs[i]
            hits.append(Snippet(runbook=runbook, heading=heading, text=body, score=float(scores[i])))
        return hits
