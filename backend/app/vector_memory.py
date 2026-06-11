from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .config import settings
from .diagnostics import write_diagnostic
from .models import Memory
from .persona import VECTOR_RECALL_LAYERS
from .providers import OpenAIEmbeddingClient, ProviderError, get_enabled_provider
from .utils import dump_json, load_json, utc_now


COLLECTION_NAME = "aigalgame_memories"
POINT_NAMESPACE = uuid.UUID("22af76c4-2a07-4c41-99d1-8a34fd0d89ce")


@dataclass(frozen=True)
class VectorMemoryHit:
    memory_id: str
    score: float
    source: str = "qdrant"


def _qdrant_imports() -> tuple[Any, Any]:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("qdrant-client is not installed. Run pip install -r backend/requirements.txt") from exc
    return QdrantClient, models


def _point_id(memory_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, memory_id))


def _client() -> Any:
    QdrantClient, _ = _qdrant_imports()
    path = settings.data_dir / "qdrant"
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _collection_exists(client: Any) -> bool:
    try:
        exists = getattr(client, "collection_exists", None)
        if callable(exists):
            return bool(exists(COLLECTION_NAME))
        client.get_collection(COLLECTION_NAME)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ensure_collection(client: Any, vector_size: int) -> None:
    _, models = _qdrant_imports()
    if _collection_exists(client):
        try:
            info = client.get_collection(COLLECTION_NAME)
            vectors = getattr(getattr(info, "config", None), "params", None)
            vectors = getattr(vectors, "vectors", None)
            current_size = getattr(vectors, "size", None)
            if current_size in (None, vector_size):
                return
            client.delete_collection(COLLECTION_NAME)
        except Exception:  # noqa: BLE001
            return
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
    )


def embed_texts(session: Session, texts: list[str], *, diagnostic: dict[str, Any] | None = None) -> list[list[float]]:
    config = get_enabled_provider(session, "embedding")
    if config is None:
        raise ProviderError("Embedding provider is not configured")
    metadata = load_json(config.metadata_json, {})
    batch_size = max(1, min(int(metadata.get("batch_size") or 16), 128))
    client = OpenAIEmbeddingClient(config)
    vectors: list[list[float]] = []
    for index in range(0, len(texts), batch_size):
        vectors.extend(client.embed(texts[index:index + batch_size], diagnostic=diagnostic))
    return vectors


def _memory_payload(memory: Memory) -> dict[str, Any]:
    return {
        "memory_id": memory.memory_id,
        "user_id": memory.user_id,
        "character_id": memory.character_id,
        "layer": memory.layer,
        "content": memory.content,
        "source_event_id": memory.source_event_id,
        "importance": float(memory.importance or 0),
        "confidence": float(memory.confidence or 0),
        "tags": load_json(memory.tags_json, []),
        "metadata": load_json(memory.metadata_json, {}),
        "hidden": bool(memory.hidden),
        "created_at": memory.created_at,
    }


def index_memory_vector(session: Session, memory: Memory) -> None:
    if memory.hidden:
        delete_memory_vector(memory.memory_id)
        memory.vector_status = "hidden"
        memory.vector_updated_at = utc_now()
        return
    vectors = embed_texts(
        session,
        [memory.content],
        diagnostic={
            "feature": "记忆向量",
            "stage": "index_memory",
            "purpose": "Embed memory before Qdrant upsert",
            "input": {"memory_id": memory.memory_id, "layer": memory.layer, "content": memory.content[:240]},
        },
    )
    vector = vectors[0]
    client = _client()
    try:
        _, models = _qdrant_imports()
        _ensure_collection(client, len(vector))
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[models.PointStruct(id=_point_id(memory.memory_id), vector=vector, payload=_memory_payload(memory))],
        )
    finally:
        _close_client(client)
    memory.embedding_json = dump_json(vector)
    memory.vector_status = "ready"
    memory.vector_updated_at = utc_now()
    write_diagnostic(
        "memory_vector_indexed",
        feature="记忆向量",
        stage="index_memory",
        memory_id=memory.memory_id,
        layer=memory.layer,
        dimensions=len(vector),
    )


def safe_index_memory_vector(session: Session, memory: Memory) -> dict[str, Any]:
    try:
        index_memory_vector(session, memory)
        return {"memory_id": memory.memory_id, "status": memory.vector_status, "source": "qdrant"}
    except Exception as exc:  # noqa: BLE001
        memory.vector_status = "error"
        memory.vector_updated_at = utc_now()
        write_diagnostic(
            "memory_vector_index_error",
            feature="记忆向量",
            stage="index_memory",
            memory_id=memory.memory_id,
            layer=memory.layer,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return {"memory_id": memory.memory_id, "status": "error", "source": "qdrant", "message": str(exc)}


def delete_memory_vector(memory_id: str) -> None:
    try:
        client = _client()
        try:
            if not _collection_exists(client):
                return
            client.delete(collection_name=COLLECTION_NAME, points_selector=[_point_id(memory_id)])
        finally:
            _close_client(client)
    except Exception as exc:  # noqa: BLE001
        write_diagnostic(
            "memory_vector_delete_error",
            feature="记忆向量",
            stage="delete_memory",
            memory_id=memory_id,
            error_type=type(exc).__name__,
            message=str(exc),
        )


def _filter(user_id: str, character_id: str, layers: list[str]) -> Any:
    _, models = _qdrant_imports()
    must = [
        models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
        models.FieldCondition(key="character_id", match=models.MatchValue(value=character_id)),
        models.FieldCondition(key="hidden", match=models.MatchValue(value=False)),
    ]
    if layers:
        must.append(models.FieldCondition(key="layer", match=models.MatchAny(any=layers)))
    return models.Filter(must=must)


def _points_from_query_result(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    points = getattr(result, "points", None)
    if points is not None:
        return list(points)
    result_points = getattr(result, "result", None)
    if isinstance(result_points, list):
        return result_points
    return []


def search_memory_vectors(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    query_text: str,
    layers: set[str] | None = None,
    limit: int = 8,
) -> list[VectorMemoryHit]:
    query = " ".join(str(query_text or "").split())
    if not query:
        return []
    vectors = embed_texts(
        session,
        [query],
        diagnostic={
            "feature": "记忆向量",
            "stage": "search_memory",
            "purpose": "Embed user input for memory recall",
            "input": {"user_id": user_id, "character_id": character_id, "query": query[:240]},
        },
    )
    client = _client()
    try:
        if not _collection_exists(client):
            return []
        layer_list = sorted(layers or VECTOR_RECALL_LAYERS)
        qfilter = _filter(user_id, character_id, layer_list)
        query_points = getattr(client, "query_points", None)
        if callable(query_points):
            result = query_points(
                collection_name=COLLECTION_NAME,
                query=vectors[0],
                query_filter=qfilter,
                limit=max(1, min(limit, 30)),
                with_payload=True,
            )
        else:
            result = client.search(
                collection_name=COLLECTION_NAME,
                query_vector=vectors[0],
                query_filter=qfilter,
                limit=max(1, min(limit, 30)),
                with_payload=True,
            )
    finally:
        _close_client(client)
    hits: list[VectorMemoryHit] = []
    for point in _points_from_query_result(result):
        payload = getattr(point, "payload", None) or {}
        memory_id = str(payload.get("memory_id") or "")
        if not memory_id:
            continue
        hits.append(VectorMemoryHit(memory_id=memory_id, score=float(getattr(point, "score", 0.0) or 0.0)))
    write_diagnostic(
        "memory_vector_recalled",
        feature="记忆向量",
        stage="search_memory",
        user_id=user_id,
        character_id=character_id,
        query=query[:240],
        count=len(hits),
        layers=sorted(layers or VECTOR_RECALL_LAYERS),
    )
    return hits
