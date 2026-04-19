from __future__ import annotations

from collections import Counter
import re
from typing import List


STOPWORDS = {
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
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
}


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())
    return [token for token in tokens if len(token) > 1 and token not in STOPWORDS]


def _split_text(
    text: str,
    *,
    chunk_size: int = 700,
    chunk_overlap: int = 120,
) -> List[str]:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned)
        if sentence.strip()
    ]
    if not sentences:
        return [cleaned[:chunk_size]]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence_len = len(sentence)
        if current and current_len + sentence_len + 1 > chunk_size:
            chunk = " ".join(current).strip()
            if chunk:
                chunks.append(chunk)

            overlap_sentences: list[str] = []
            overlap_len = 0
            for previous in reversed(current):
                if overlap_len + len(previous) + 1 > chunk_overlap:
                    break
                overlap_sentences.insert(0, previous)
                overlap_len += len(previous) + 1

            current = overlap_sentences[:]
            current_len = len(" ".join(current))

        current.append(sentence)
        current_len = len(" ".join(current))

    final_chunk = " ".join(current).strip()
    if final_chunk:
        chunks.append(final_chunk)

    if not chunks:
        return [cleaned[:chunk_size]]
    return chunks


class VectorStoreManager:
    def __init__(self):
        self.chunks: list[str] = []
        self.index: list[dict] | None = None

    def create_chunks(self, text: str) -> List[str]:
        text = (text or "").strip()
        if not text:
            self.chunks = []
            self.index = None
            return self.chunks

        self.chunks = _split_text(text)
        self.index = None
        return self.chunks

    def build_index(self):
        if not self.chunks:
            raise ValueError("Cannot build vector index because document has no chunks.")

        self.index = []
        for idx, chunk in enumerate(self.chunks):
            tokens = _tokenize(chunk)
            self.index.append(
                {
                    "position": idx,
                    "text": chunk,
                    "token_counts": Counter(tokens),
                    "token_set": set(tokens),
                    "text_lower": chunk.lower(),
                }
            )

    def retrieve_context(self, query: str, k: int = 3) -> List[str]:
        if not self.chunks:
            return []
        if self.index is None:
            return self.chunks[: min(k, len(self.chunks))]

        query_tokens = _tokenize(query)
        if not query_tokens:
            return self.chunks[: min(k, len(self.chunks))]

        query_set = set(query_tokens)
        query_lower = (query or "").strip().lower()
        scored: list[tuple[float, int, str]] = []

        for item in self.index:
            token_overlap = len(query_set & item["token_set"])
            token_frequency = sum(
                min(item["token_counts"].get(token, 0), 3)
                for token in query_set
            )
            substring_bonus = 3 if query_lower and query_lower in item["text_lower"] else 0
            score = (token_overlap * 3) + token_frequency + substring_bonus
            if score > 0:
                scored.append((float(score), item["position"], item["text"]))

        if not scored:
            return self.chunks[: min(k, len(self.chunks))]

        scored.sort(key=lambda row: (-row[0], row[1]))
        return [text for _, _, text in scored[: min(k, len(scored))]]
