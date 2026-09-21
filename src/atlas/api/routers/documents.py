from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.api.deps import enforce_upload_rate_limit, require_user
from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.auth import AuthUser
from atlas.schemas.chat import DocumentOut
from atlas.schemas.ingest_job import IngestJobOut
from atlas.services.ingest import IngestError, IngestService
from atlas.services.ingest_queue import IngestQueue, IngestQueueError
from atlas.services.readers import DocumentReadError, title_from_path
from atlas.services.upload_validation import (
    UploadValidationError,
    safe_upload_filename,
    validate_upload_contents,
    validate_upload_suffix,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def get_ingest(request: Request) -> IngestService:
    service = request.app.state.ingest_service
    if not isinstance(service, IngestService):
        raise RuntimeError("Ingest service is not configured.")
    return service


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = request.app.state.session_factory
    if not isinstance(factory, async_sessionmaker):
        raise RuntimeError("Database session factory is not configured.")
    return factory


def get_ingest_queue(request: Request) -> IngestQueue | None:
    queue = getattr(request.app.state, "ingest_queue", None)
    if queue is None:
        return None
    if not isinstance(queue, IngestQueue):
        raise RuntimeError("Ingest queue is misconfigured.")
    return queue


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    _: Annotated[AuthUser, Depends(require_user)],
) -> list[DocumentOut]:
    async with session_factory() as session:
        documents = await DocumentRepository(session).list_documents()
    return [
        DocumentOut(
            id=document.id,
            title=document.title,
            original_filename=document.original_filename,
            chunk_count=document.chunk_count,
            created_at=document.created_at.isoformat(),
        )
        for document in documents
    ]


@router.get("/jobs/{job_id}", response_model=IngestJobOut)
async def get_ingest_job(
    job_id: str,
    queue: Annotated[IngestQueue | None, Depends(get_ingest_queue)],
    _: Annotated[AuthUser, Depends(require_user)],
) -> IngestJobOut:
    if queue is None:
        raise HTTPException(
            status_code=503,
            detail="Ingest queue is not available. Start Redis or check REDIS_URL.",
        )
    job = await queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingest job not found.")
    return job


@router.post("/upload", response_model=DocumentOut | IngestJobOut)
async def upload_document(
    request: Request,
    response: Response,
    ingest: Annotated[IngestService, Depends(get_ingest)],
    queue: Annotated[IngestQueue | None, Depends(get_ingest_queue)],
    file: Annotated[UploadFile, File()],
    _: Annotated[AuthUser, Depends(enforce_upload_rate_limit)],
) -> DocumentOut | IngestJobOut:
    if not request.app.state.settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to your environment or a .env file.",
        )

    filename = safe_upload_filename(file.filename)
    try:
        suffix = validate_upload_suffix(filename)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    max_bytes = int(request.app.state.settings.max_upload_bytes)
    contents = await file.read(max_bytes + 1)
    try:
        validate_upload_contents(contents, max_bytes=max_bytes)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload_dir = Path(request.app.state.settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{uuid4()}{suffix}"
    saved_path.write_bytes(contents)
    title = title_from_path(Path(filename))

    if queue is not None:
        try:
            job = await queue.enqueue(
                path=str(saved_path),
                title=title,
                original_filename=filename,
            )
        except IngestQueueError as exc:
            saved_path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        response.status_code = 202
        return job

    try:
        return await ingest.ingest_path(saved_path, title=title)
    except (DocumentReadError, IngestError) as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    ingest: Annotated[IngestService, Depends(get_ingest)],
    _: Annotated[AuthUser, Depends(require_user)],
) -> dict[str, str]:
    deleted = await ingest.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"status": "deleted"}
