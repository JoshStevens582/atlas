# Chunking and document ingestion

Documents are not stored as one giant string. Atlas splits them so a search hit is a usable passage, not an entire PDF.

## Paragraph packing, then a sliding window

Short paragraphs are packed together until they approach `chunk_size` (900 characters). A paragraph longer than that is split with a sliding window: take `chunk_size` characters, then start the next chunk at `end - overlap`. Overlap is 150 characters. `chunk_size` must be bigger than overlap or the window never moves.

Overlap exists so a sentence that sits on a boundary still appears intact in at least one chunk. It is not “understanding.” It is copying the tail of the previous window onto the head of the next one.

## File types

Atlas reads Markdown, plain text, and PDF. PDF text is extracted per page and joined. Scanned image-only PDFs have no extractable text and are rejected.

Ingestion is a separate job from chat. Upload or seed files are chunked, embedded, and saved before any question is asked. The chat path only queries the vector store.
