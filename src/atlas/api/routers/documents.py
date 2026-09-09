from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.chat import DocumentOut
from atlas.services.ingest import IngestError, IngestService
from atlas.services.readers import SUPPORTED_SUFFIXES, DocumentReadError, title_from_path

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


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
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


@router.post("/upload", response_model=DocumentOut)
async def upload_document(
    request: Request,
    ingest: Annotated[IngestService, Depends(get_ingest)],
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    if not request.app.state.settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to your environment or a .env file.",
        )
    filename = file.filename or "upload.txt"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Use a .md, .txt, or .pdf file.")

    upload_dir = Path(request.app.state.settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{uuid4()}{suffix}"
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    saved_path.write_bytes(contents)

    try:
        return await ingest.ingest_path(
            saved_path,
            title=title_from_path(Path(filename)),
        )
    except (DocumentReadError, IngestError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    ingest: Annotated[IngestService, Depends(get_ingest)],
) -> dict[str, str]:
    deleted = await ingest.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"status": "deleted"}
