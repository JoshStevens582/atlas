# Embeddings and semantic search

An embedding is a list of numbers that represents the meaning of a piece of text. Sentences about similar topics land close together in that vector space.

Atlas uses OpenAI `text-embedding-3-small` for both documents and questions. The same model must embed both sides. Mixing embedding models breaks nearest-neighbor search.

## How a query is answered

1. Split source files into overlapping chunks.
2. Embed each chunk and upsert it into a Chroma collection with cosine distance.
3. Embed the user question.
4. Ask Chroma for the nearest chunks (`retrieve_k`, default 5).
5. Drop hits whose cosine distance is worse than `max_distance` (default 0.85).
   If every hit is weaker than that, keep the single nearest chunk so a real
   document is not dropped to an empty prompt.
6. Run **BM25** (keyword search) over the same chunks. Rare words and ids
   that vectors miss still rank.
7. Fuse the two ranked lists with reciprocal rank fusion (RRF). A chunk in
   both lists ranks above a one-list hit.
8. Number those chunks `[1]`, `[2]` in the `<context>` envelope and call the chat model.
9. After the model writes, keep only citation numbers that match a real chunk.
   Sources used shows which cards the answer actually cited.

Lower cosine distance means a closer match. The **Sources used** panel in the Atlas UI shows this distance for every retrieved chunk so a reviewer can see why a passage was chosen.
