# What retrieval-augmented generation is

Atlas is a retrieval-augmented generation (RAG) assistant. It does not answer from the model’s training data when a document is available. It searches indexed passages first, then writes an answer from those passages.

## The two-step loop

1. **Retrieve.** The question is converted into an embedding vector. Atlas compares that vector to stored chunk vectors in ChromaDB and keeps the closest matches under a distance cutoff.
2. **Generate.** Those passages, the recent chat turns, and the new question are sent to OpenAI. The model writes the reply. Retrieval is search. Generation is the API write.

## What Atlas will not do

If search returns no close chunks, Atlas must say it does not know based on the indexed documents. It must not invent refunds, APIs, or company policies.

Chat history is stored in SQLite. Document chunks are stored in ChromaDB. Those stores are kept separate so conversation chatter does not pollute document search.
