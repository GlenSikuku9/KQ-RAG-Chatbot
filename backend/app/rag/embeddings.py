from functools import lru_cache
from importlib.metadata import version
import logging
import os
import re
from threading import RLock
from typing import Protocol

import numpy as np

from app.config import Settings, get_settings
from app.models.document_chunk import DocumentChunk


logger = logging.getLogger(__name__)


class EmbeddingInputError(ValueError):
    """Input cannot fit the model without losing information."""


class EmbeddingConfigurationError(ValueError):
    """Model configuration or output cannot be used for retrieval."""


class Embedder(Protocol):
    @property
    def fingerprint(self) -> dict: ...
    def segments(self, text: str) -> list[str]: ...
    def validate_passages(self, chunks: list[DocumentChunk]) -> None: ...
    def embed_passages(self, chunks: list[DocumentChunk]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def load_model(name: str, revision: str, cache: str):
    # Download public weights only; inference stays local and telemetry is disabled.
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(
        name, revision=revision, cache_folder=cache, device="cpu", trust_remote_code=False,
    )


class E5Embedder:
    """One lazy CPU model, shared across query requests in a backend process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = RLock()

    def _get_model(self):
        with self._lock:
            if self._model is None:
                if not self.settings.embedding_model_name or not self.settings.embedding_model_revision:
                    logger.error("Embedding model name and immutable revision must be configured.")
                    raise EmbeddingConfigurationError("Set EMBEDDING_MODEL_NAME and EMBEDDING_MODEL_REVISION before indexing.")
                if not re.fullmatch(r"[0-9a-f]{40}", self.settings.embedding_model_revision):
                    logger.error("Embedding revision must be a full immutable commit hash.")
                    raise EmbeddingConfigurationError("EMBEDDING_MODEL_REVISION must be a 40-character commit hash.")
                try:
                    self._model = load_model(
                        self.settings.embedding_model_name, self.settings.embedding_model_revision,
                        str(self.settings.embedding_cache_dir),
                    )
                except ValueError as exc:
                    logger.error("Configured embedding model could not be loaded.")
                    raise EmbeddingConfigurationError("Invalid embedding model configuration.") from exc
            return self._model

    @property
    def fingerprint(self) -> dict:
        model = self._get_model()
        return {
            "model": self.settings.embedding_model_name,
            "revision": self.settings.embedding_model_revision,
            "dimension": model.get_embedding_dimension(),
            "max_tokens": min(512, model.max_seq_length),
            "normalization": True, "query_prefix": "query: ", "passage_prefix": "passage: ",
            "pipeline": "e5-cpu-segments-v2",
            "libraries": {name: version(name) for name in ("sentence-transformers", "transformers", "torch")},
        }

    def segments(self, text: str) -> list[str]:
        """Partition only oversized search inputs; callers retain the full source chunk."""
        model = self._get_model()
        limit = min(512, model.max_seq_length)

        def fits(value: str) -> bool:
            return len(model.tokenizer(
                "passage: " + value, add_special_tokens=True, truncation=False,
            )["input_ids"]) <= limit

        if not text.strip():
            logger.error("Cannot segment empty embedding input.")
            raise ValueError("Empty embedding input.")
        output = []
        remaining = text
        while remaining:
            if fits(remaining):
                output.append(remaining)
                break
            low, high = 0, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if fits(remaining[:middle]):
                    low = middle
                else:
                    high = middle - 1
            boundary = remaining.rfind(" ", low // 2, low)
            end = boundary + 1 if boundary >= 0 else low
            if end == 0 or not remaining[:end].strip() or not fits(remaining[:end]):
                logger.error("Cannot produce a token-safe segment.")
                raise ValueError("Unable to segment input within the model limit.")
            output.append(remaining[:end])
            remaining = remaining[end:]
        return output

    def _validate(self, text: str, prefix: str, label: str) -> str:
        if not text.strip():
            logger.error("Cannot embed empty text: %s.", label)
            raise EmbeddingInputError("Embedding input must not be empty.")
        model = self._get_model()
        value = prefix + text
        tokens = model.tokenizer(value, add_special_tokens=True, truncation=False)["input_ids"]
        limit = min(512, model.max_seq_length)
        if len(tokens) > limit:
            logger.error("Embedding input %s has %s tokens; limit is %s.", label, len(tokens), limit)
            raise EmbeddingInputError(
                f"{label}: {len(tokens)} tokens exceeds {limit}; shorten/reprocess this input. No text was truncated."
            )
        return value

    def validate_passages(self, chunks: list[DocumentChunk]) -> None:
        for chunk in chunks:
            self._validate(chunk.text, "passage: ", f"{chunk.source} chunk {chunk.chunk_index}")

    def _encode(self, values: list[str]) -> list[list[float]]:
        with self._lock:
            model = self._get_model()
            vectors = np.asarray(model.encode(
                values, batch_size=self.settings.embedding_batch_size,
                normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True,
            ))
        if (vectors.shape != (len(values), model.get_embedding_dimension())
                or not np.isfinite(vectors).all()
                or not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)):
            logger.error("Embedding model returned invalid or unnormalized vectors.")
            raise EmbeddingConfigurationError("Invalid embedding model output.")
        return vectors.tolist()

    def embed_passages(self, chunks: list[DocumentChunk]) -> list[list[float]]:
        if not chunks:
            return []
        values = [self._validate(c.text, "passage: ", f"{c.source} chunk {c.chunk_index}") for c in chunks]
        return self._encode(values)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self._validate(text, "query: ", "question")])[0]


@lru_cache(maxsize=1)
def get_embedder() -> E5Embedder:
    return E5Embedder(get_settings())
