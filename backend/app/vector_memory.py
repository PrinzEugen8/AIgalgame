from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .diagnostics import write_diagnostic
from .models import Memory
from .persona import FIXED_MEMORY_LAYERS, VECTOR_RECALL_LAYERS
from .providers import OpenAIEmbeddingClient, ProviderError, get_enabled_provider
from .utils import dump_json, load_json, utc_now


COLLECTION_NAME = "aigalgame_memories"
POINT_NAMESPACE = uuid.UUID("22af76c4-2a07-4c41-99d1-8a34fd0d89ce")


@dataclass(frozen=True)
class VectorMemoryHit:
    memory_id: str
    score: float
    source: str = "qdrant"


def _summary_text(value: str, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


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
            raise ProviderError(
                f"Qdrant collection vector size mismatch: current={current_size}, incoming={vector_size}. "
                "Create a backup and rebuild the memory vector collection before reindexing."
            )
        except ProviderError:
            raise
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


def _memory_recall_item(
    memory: Memory,
    *,
    activated: bool,
    reason: str,
    source: str = "",
    score: float | None = None,
    rank: int | None = None,
) -> dict[str, Any]:
    item = {
        "memory_id": memory.memory_id,
        "user_id": memory.user_id,
        "character_id": memory.character_id,
        "layer": memory.layer,
        "content": memory.content,
        "summary": _summary_text(memory.content),
        "source_event_id": memory.source_event_id,
        "importance": float(memory.importance or 0),
        "confidence": float(memory.confidence or 0),
        "tags": load_json(memory.tags_json, []),
        "metadata": load_json(memory.metadata_json, {}),
        "vector_status": memory.vector_status,
        "vector_updated_at": memory.vector_updated_at,
        "hidden": bool(memory.hidden),
        "created_at": memory.created_at,
        "activated": activated,
        "reason": reason,
        "source": source,
    }
    if score is not None:
        item["score"] = round(float(score), 6)
    if rank is not None:
        item["rank"] = int(rank)
    return item


def _memory_vector(memory: Memory) -> list[float]:
    raw = load_json(memory.embedding_json, [])
    if not isinstance(raw, list):
        return []
    vector: list[float] = []
    for value in raw:
        try:
            vector.append(float(value))
        except (TypeError, ValueError):
            return []
    return vector


def _cosine_score(left: list[float], right: list[float]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return None
    return dot / (left_norm * right_norm)


def explain_memory_recall(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    query_text: str,
    vector_layers: set[str] | None = None,
    fixed_layers: set[str] | None = None,
    vector_limit: int = 8,
    fixed_limit: int = 8,
    inactive_limit: int = 200,
    include_hidden: bool = False,
) -> dict[str, Any]:
    query = " ".join(str(query_text or "").split())
    vector_limit = max(1, min(int(vector_limit or 8), 30))
    fixed_limit = max(0, min(int(fixed_limit or 8), 30))
    inactive_limit = max(1, min(int(inactive_limit or 200), 1000))
    vector_layer_set = set(vector_layers or VECTOR_RECALL_LAYERS)
    fixed_layer_set = set(fixed_layers or FIXED_MEMORY_LAYERS)
    rows = session.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.character_id == character_id)
        .order_by(Memory.created_at.desc())
    ).scalars().all()
    considered = [memory for memory in rows if include_hidden or not memory.hidden]

    fixed_candidates = [
        memory
        for memory in considered
        if not memory.hidden and memory.layer in fixed_layer_set
    ]
    fixed_selected = sorted(fixed_candidates, key=lambda item: (float(item.importance or 0), item.created_at or ""), reverse=True)[:fixed_limit]
    fixed_active_ids = {memory.memory_id for memory in fixed_selected}

    vector_candidates = [
        memory
        for memory in considered
        if not memory.hidden and memory.layer in vector_layer_set
    ]
    vector_hits: list[VectorMemoryHit] = []
    vector_error = ""
    recall_source = "qdrant"
    if query:
        try:
            vector_hits = search_memory_vectors(
                session,
                user_id=user_id,
                character_id=character_id,
                query_text=query,
                layers=vector_layer_set,
                limit=vector_limit,
            )
        except Exception as exc:  # noqa: BLE001
            vector_error = str(exc)
            recall_source = "error"
    hit_by_id = {hit.memory_id: hit for hit in vector_hits}
    vector_active_ids = set(hit_by_id)
    fallback_active_ids: set[str] = set()
    if query and not vector_active_ids and not vector_error:
        recall_source = "sqlite_recent"
        fallback = sorted(vector_candidates, key=lambda item: item.created_at or "", reverse=True)[:vector_limit]
        fallback_active_ids = {memory.memory_id for memory in fallback}
    elif vector_active_ids:
        recall_source = "qdrant"

    query_vector: list[float] = []
    score_error = ""
    if query:
        try:
            query_vector = embed_texts(
                session,
                [query],
                diagnostic={
                    "feature": "memory_recall_eval",
                    "stage": "score_memory",
                    "purpose": "Embed query for memory recall evaluation scores",
                    "input": {"user_id": user_id, "character_id": character_id, "query": query[:240]},
                },
            )[0]
        except Exception as exc:  # noqa: BLE001
            score_error = str(exc)
    local_scores: dict[str, float] = {}
    if query_vector:
        for memory in vector_candidates:
            score = _cosine_score(query_vector, _memory_vector(memory))
            if score is not None:
                local_scores[memory.memory_id] = score
    local_ranks = {
        memory_id: index + 1
        for index, (memory_id, _score) in enumerate(sorted(local_scores.items(), key=lambda item: item[1], reverse=True))
    }

    activated: list[dict[str, Any]] = []
    inactive: list[dict[str, Any]] = []
    active_ids = fixed_active_ids | vector_active_ids | fallback_active_ids
    by_id = {memory.memory_id: memory for memory in considered}
    for memory in fixed_selected:
        activated.append(
            _memory_recall_item(
                memory,
                activated=True,
                reason="fixed_layer_context",
                source="fixed",
                score=local_scores.get(memory.memory_id),
                rank=local_ranks.get(memory.memory_id),
            )
        )
    for hit in vector_hits:
        memory = by_id.get(hit.memory_id)
        if memory is None or memory.memory_id in fixed_active_ids:
            continue
        activated.append(
            _memory_recall_item(
                memory,
                activated=True,
                reason="vector_top_k",
                source=hit.source,
                score=hit.score,
                rank=local_ranks.get(memory.memory_id),
            )
        )
    if fallback_active_ids:
        for memory in sorted(vector_candidates, key=lambda item: item.created_at or "", reverse=True):
            if memory.memory_id not in fallback_active_ids or memory.memory_id in fixed_active_ids:
                continue
            activated.append(
                _memory_recall_item(
                    memory,
                    activated=True,
                    reason="sqlite_recent_fallback",
                    source="sqlite_recent",
                    score=local_scores.get(memory.memory_id),
                    rank=local_ranks.get(memory.memory_id),
                )
            )

    for memory in considered:
        if memory.memory_id in active_ids:
            continue
        reason = "not_selected_layer"
        source = ""
        if memory.hidden:
            reason = "hidden"
        elif memory.layer in fixed_layer_set:
            reason = "fixed_limit_exceeded"
            source = "fixed"
        elif memory.layer in vector_layer_set:
            source = recall_source
            if not query:
                reason = "empty_query"
            elif memory.vector_status != "ready":
                reason = f"vector_not_ready:{memory.vector_status or 'unknown'}"
            elif memory.memory_id in local_ranks and local_ranks[memory.memory_id] > vector_limit:
                reason = "outside_vector_top_k"
            elif memory.memory_id not in local_scores:
                reason = "missing_or_mismatched_embedding"
            else:
                reason = "not_returned_by_vector_store"
        inactive.append(
            _memory_recall_item(
                memory,
                activated=False,
                reason=reason,
                source=source,
                score=local_scores.get(memory.memory_id),
                rank=local_ranks.get(memory.memory_id),
            )
        )
    inactive.sort(key=lambda item: (str(item.get("reason") or ""), int(item.get("rank") or 999999), -float(item.get("importance") or 0)))
    inactive_truncated = len(inactive) > inactive_limit
    inactive = inactive[:inactive_limit]

    payload = {
        "ok": True,
        "query": query,
        "user_id": user_id,
        "character_id": character_id,
        "policy": {
            "fixed_layers": sorted(fixed_layer_set),
            "vector_layers": sorted(vector_layer_set),
            "fixed_limit": fixed_limit,
            "vector_limit": vector_limit,
            "inactive_limit": inactive_limit,
            "include_hidden": include_hidden,
            "recall_source": recall_source,
        },
        "summary": {
            "memory_count": len(rows),
            "considered_count": len(considered),
            "activated_count": len(activated),
            "inactive_count": len(inactive),
            "inactive_truncated": inactive_truncated,
            "vector_ready_count": len([memory for memory in vector_candidates if memory.vector_status == "ready"]),
            "vector_pending_or_error_count": len([memory for memory in vector_candidates if memory.vector_status != "ready"]),
            "vector_error": vector_error,
            "score_error": score_error,
        },
        "activated": activated,
        "inactive": inactive,
    }
    write_diagnostic(
        "memory_recall_evaluated",
        feature="memory_recall_eval",
        stage="evaluate",
        user_id=user_id,
        character_id=character_id,
        query=query[:240],
        activated_count=len(activated),
        inactive_count=len(inactive),
        vector_error=vector_error,
        score_error=score_error,
        recall_source=recall_source,
    )
    return payload
