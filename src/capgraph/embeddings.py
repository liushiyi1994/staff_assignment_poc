"""Local sentence-transformers wrapper. One model instance, normalized vectors."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from .settings import settings


@lru_cache
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings["embedding.model"])


def embed(texts: list[str]) -> np.ndarray:
    """Returns (n, dims) float32, L2-normalized — cosine == dot product."""
    vecs = _model().encode(texts, normalize_embeddings=True, show_progress_bar=len(texts) > 200)
    return np.asarray(vecs, dtype=np.float32)
