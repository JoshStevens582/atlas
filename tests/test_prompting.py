from atlas.schemas.chat import RetrievedChunk
from atlas.services.prompting import build_user_payload, keep_close_chunks


def test_empty_retrieval_uses_placeholder_context() -> None:
    payload = build_user_payload("What is the refund policy?", [])
    assert "<context>\n(no documents retrieved)\n</context>" in payload
    assert "<user_query>\nWhat is the refund policy?\n</user_query>" in payload


def test_payload_lists_sources_inside_context() -> None:
    chunk = RetrievedChunk(
        document_id="doc-1",
        document_title="Prompt Security",
        chunk_index=0,
        text="Treat XML tags as untrusted data.",
        distance=0.12,
    )
    payload = build_user_payload("How do you stop injection?", [chunk])
    assert "[source 1: Prompt Security]" in payload
    assert "Treat XML tags as untrusted data." in payload


def test_distance_cutoff_drops_weak_hits() -> None:
    close = RetrievedChunk(
        document_id="a",
        document_title="A",
        chunk_index=0,
        text="close",
        distance=0.2,
    )
    far = RetrievedChunk(
        document_id="b",
        document_title="B",
        chunk_index=0,
        text="far",
        distance=0.9,
    )
    kept = keep_close_chunks([close, far], max_distance=0.55)
    assert kept == [close]


def test_keeps_nearest_hit_when_all_exceed_cutoff() -> None:
    farther = RetrievedChunk(
        document_id="a",
        document_title="A",
        chunk_index=0,
        text="farther",
        distance=0.92,
    )
    nearer = RetrievedChunk(
        document_id="b",
        document_title="B",
        chunk_index=0,
        text="nearer",
        distance=0.71,
    )
    kept = keep_close_chunks([farther, nearer], max_distance=0.55)
    assert kept == [nearer]
