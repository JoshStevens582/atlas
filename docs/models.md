# OpenAI models in Atlas

| Setting | Env variable | Default | Used for |
| :--- | :--- | :--- | :--- |
| Chat | `OPENAI_CHAT_MODEL` | `gpt-6-luna` | Ask (streaming Responses API) |
| Re-rank | `RERANK_MODEL` | `gpt-6-luna` | Re-order retrieved chunks |
| Answer check | `ANSWER_CHECK_MODEL` | `gpt-6-luna` | Supported / partly / not verdict |
| Upload self-test | `RETRIEVAL_CHECK_MODEL` | `gpt-6-luna` | Questions after document ingest |
| Embeddings | `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Chroma vectors (unchanged) |

Production: set all four Luna variables in `/opt/atlas/.env`, then:

```bash
cd /opt/atlas && docker compose up -d --force-recreate api
```

Verify on the laptop (needs `OPENAI_API_KEY`):

```bash
uv run python -m atlas.eval_cli
```
