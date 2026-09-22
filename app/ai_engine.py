from __future__ import annotations

import json
import math
import random
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_\-]{2,}", re.UNICODE)
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n{2,}")

STOPWORDS = {
    "это", "как", "что", "для", "или", "при", "его", "она", "они", "мы", "вы", "из", "на", "по", "не", "но",
    "то", "так", "все", "уже", "бы", "же", "до", "от", "со", "без", "над", "под", "про", "когда", "если",
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "not", "you", "your", "into",
    "заметка", "заметки", "можно", "нужно", "будет", "есть", "также", "чтобы", "который", "которая", "которые",
}


def tokenize(text: str) -> list[str]:
    return [t.lower().strip("-_") for t in TOKEN_RE.findall(text or "") if len(t.strip("-_")) >= 2]


def sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -12, 12)))


class PersonalTextModel:
    """Small skip-gram neural model trained only on the user's notes."""

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = model_dir / "personal_model.json"
        self.weights_path = model_dir / "personal_model.npz"
        self.lock = threading.RLock()
        self.vocab: dict[str, int] = {}
        self.index_to_word: list[str] = []
        self.embeddings: np.ndarray | None = None
        self.trained_notes = 0
        self.trained_tokens = 0
        self._load()

    def _load(self) -> None:
        if not self.meta_path.exists() or not self.weights_path.exists():
            return
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            data = np.load(self.weights_path)
            self.index_to_word = list(meta.get("vocab", []))
            self.vocab = {word: i for i, word in enumerate(self.index_to_word)}
            self.embeddings = np.asarray(data["embeddings"], dtype=np.float32)
            self.trained_notes = int(meta.get("trained_notes", 0))
            self.trained_tokens = int(meta.get("trained_tokens", 0))
        except Exception:
            self.vocab = {}
            self.index_to_word = []
            self.embeddings = None

    @property
    def ready(self) -> bool:
        return self.embeddings is not None and len(self.vocab) >= 3

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "vocabulary": len(self.vocab),
            "trained_notes": self.trained_notes,
            "trained_tokens": self.trained_tokens,
            "type": "локальная Skip-gram нейросеть",
        }

    def train(
        self,
        notes: list[dict[str, Any]],
        dimensions: int = 64,
        epochs: int = 4,
        window: int = 3,
        negative_samples: int = 4,
        max_pairs: int = 70000,
    ) -> dict[str, Any]:
        documents = [tokenize(f"{n.get('title', '')} {n.get('content', '')}") for n in notes]
        counts = Counter(token for doc in documents for token in doc if token not in STOPWORDS)
        min_count = 2 if sum(counts.values()) > 100 else 1
        words = [word for word, count in counts.most_common(5000) if count >= min_count]
        if len(words) < 3:
            raise ValueError("Для обучения нужно больше текста: минимум несколько содержательных предложений.")

        vocab = {word: i for i, word in enumerate(words)}
        sequences = [[vocab[t] for t in doc if t in vocab] for doc in documents]
        pairs: list[tuple[int, int]] = []
        rng = random.Random(42)
        for seq in sequences:
            for i, target in enumerate(seq):
                start = max(0, i - window)
                end = min(len(seq), i + window + 1)
                for j in range(start, end):
                    if i != j:
                        pairs.append((target, seq[j]))
        if not pairs:
            raise ValueError("Недостаточно повторяющихся слов и контекстов для обучения.")
        rng.shuffle(pairs)
        pairs = pairs[:max_pairs]

        np_rng = np.random.default_rng(42)
        vocab_size = len(words)
        scale = 0.5 / max(dimensions, 1)
        w_in = np_rng.uniform(-scale, scale, (vocab_size, dimensions)).astype(np.float32)
        w_out = np.zeros((vocab_size, dimensions), dtype=np.float32)
        freqs = np.array([counts[word] for word in words], dtype=np.float64) ** 0.75
        probs = freqs / freqs.sum()
        base_lr = 0.035

        with self.lock:
            for epoch in range(max(1, min(epochs, 12))):
                rng.shuffle(pairs)
                learning_rate = base_lr * (1.0 - 0.65 * epoch / max(epochs, 1))
                for target, context in pairs:
                    negatives = np_rng.choice(vocab_size, size=negative_samples, p=probs)
                    candidates = np.concatenate(([context], negatives))
                    labels = np.zeros(1 + negative_samples, dtype=np.float32)
                    labels[0] = 1.0

                    vin = w_in[target].copy()
                    vouts = w_out[candidates].copy()
                    scores = sigmoid(vouts @ vin)
                    errors = labels - scores
                    grad_in = errors @ vouts
                    grad_out = errors[:, None] * vin[None, :]
                    w_in[target] += learning_rate * grad_in
                    np.add.at(w_out, candidates, learning_rate * grad_out)

            norms = np.linalg.norm(w_in, axis=1, keepdims=True)
            embeddings = w_in / np.maximum(norms, 1e-9)
            self.vocab = vocab
            self.index_to_word = words
            self.embeddings = embeddings.astype(np.float32)
            self.trained_notes = len(notes)
            self.trained_tokens = sum(len(doc) for doc in documents)
            np.savez_compressed(self.weights_path, embeddings=self.embeddings)
            self.meta_path.write_text(
                json.dumps(
                    {
                        "vocab": words,
                        "trained_notes": self.trained_notes,
                        "trained_tokens": self.trained_tokens,
                        "dimensions": dimensions,
                        "algorithm": "skip-gram-negative-sampling",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        return {**self.status(), "pairs": len(pairs), "epochs": epochs}

    def vectorize(self, text: str) -> np.ndarray | None:
        if not self.ready or self.embeddings is None:
            return None
        indices = [self.vocab[t] for t in tokenize(text) if t in self.vocab]
        if not indices:
            return None
        vector = self.embeddings[indices].mean(axis=0)
        norm = float(np.linalg.norm(vector))
        if norm < 1e-9:
            return None
        return vector / norm

    def semantic_search(self, query: str, notes: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        qvec = self.vectorize(query)
        if qvec is None:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for note in notes:
            nvec = self.vectorize(f"{note.get('title', '')}\n{note.get('content', '')}")
            if nvec is None:
                continue
            score = float(np.dot(qvec, nvec))
            scored.append((score, note))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{**note, "score": round(score, 4)} for score, note in scored[:limit]]

    def related(self, source: dict[str, Any], notes: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
        vector = self.vectorize(f"{source.get('title', '')}\n{source.get('content', '')}")
        if vector is None:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for note in notes:
            if note.get("id") == source.get("id"):
                continue
            other = self.vectorize(f"{note.get('title', '')}\n{note.get('content', '')}")
            if other is not None:
                scored.append((float(np.dot(vector, other)), note))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**note, "score": round(score, 4)} for score, note in scored[:limit]]

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        sentences = [s.strip() for s in SENTENCE_RE.split(text or "") if len(s.strip()) > 35]
        if len(sentences) <= max_sentences:
            return " ".join(sentences) if sentences else (text or "").strip()
        doc_vec = self.vectorize(text)
        scored: list[tuple[float, int, str]] = []
        for i, sentence in enumerate(sentences):
            sentence_vec = self.vectorize(sentence)
            semantic = float(np.dot(doc_vec, sentence_vec)) if doc_vec is not None and sentence_vec is not None else 0.0
            position = 1.0 / (1.0 + i * 0.16)
            richness = min(len(set(tokenize(sentence))) / 16.0, 1.0)
            score = semantic * 0.65 + position * 0.2 + richness * 0.15
            scored.append((score, i, sentence))
        selected = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda x: x[1])
        return " ".join(item[2] for item in selected)


def extract_keywords(text: str, limit: int = 8) -> list[str]:
    tokens = [t for t in tokenize(text) if t not in STOPWORDS and not t.isdigit() and len(t) > 2]
    counts = Counter(tokens)
    scores: list[tuple[float, str]] = []
    total = max(len(tokens), 1)
    for word, count in counts.items():
        length_bonus = min(len(word), 12) / 12
        score = (count / total) * math.log(total + 2) + length_bonus * 0.04
        scores.append((score, word))
    scores.sort(reverse=True)
    return [word for _, word in scores[:limit]]
