import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from atlas.schemas.chat import ChatRequest, ThreadDetailOut, ThreadOut
from atlas.services.rag import RagChatService

router = APIRouter(prefix="/api", tags=["chat"])


def get_rag(request: Request) -> RagChatService:
    service = request.app.state.rag_service
    if not isinstance(service, RagChatService):
        raise RuntimeError("RAG service is not configured.")
    return service


def require_openai_key(request: Request) -> None:
    if not request.app.state.settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to your environment or a .env file.",
        )


@router.get("/threads", response_model=list[ThreadOut])
async def list_threads(rag: Annotated[RagChatService, Depends(get_rag)]) -> list[ThreadOut]:
    return await rag.list_threads()


@router.post("/threads", response_model=ThreadOut)
async def create_thread(rag: Annotated[RagChatService, Depends(get_rag)]) -> ThreadOut:
    return await rag.create_thread()


@router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
async def get_thread(
    thread_id: str,
    rag: Annotated[RagChatService, Depends(get_rag)],
) -> ThreadDetailOut:
    thread = await rag.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return thread


@router.post("/chat/stream")
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    rag: Annotated[RagChatService, Depends(get_rag)],
    _: Annotated[None, Depends(require_openai_key)],
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async for event in rag.stream_answer(payload.message, payload.thread_id):
                if await request.is_disconnected():
                    break
                yield _sse(event)
        except LookupError:
            yield _sse({"type": "error", "detail": "Thread not found."})
        except ValueError as exc:
            yield _sse({"type": "error", "detail": str(exc)})
        except Exception:
            yield _sse({"type": "error", "detail": "Answer generation failed."})

    return StreamingResponse(events(), media_type="text/event-stream")


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"
