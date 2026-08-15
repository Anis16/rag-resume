"""
RAG orchestrator for the CV Q&A bot.

Flow per request:
  1. Normalize + check Redis cache -> return immediately on hit
  2. Embed the question (in-process, no network hop)
  3. Query Qdrant for the top-k most relevant CV chunks
  4. Build a grounded prompt and call the LLM server (llama.cpp)
  5. Cache the answer, return it
"""

import hashlib
import json
import os

import httpx
import redis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
LLM_URL = os.environ.get("LLM_URL", "http://llm-server/completion")
COLLECTION_NAME = "cv_chunks"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
TOP_K = 3
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 1 week — CV content doesn't change often

app = FastAPI(title="CV RAG Orchestrator")

# Loaded once at startup, reused across requests — this is what makes
# embedding cheap enough to do in-process instead of as a separate service.
embedder = SentenceTransformer(EMBEDDING_MODEL)
qdrant = QdrantClient(url=QDRANT_URL)
cache = redis.from_url(REDIS_URL, decode_responses=True)

SYSTEM_PROMPT = (
    "You are answering questions <>"
    "based only on the CV context provided below. If the context doesn't "
    "contain the answer, say you don't have that information rather than "
    "guessing. Answer concisely and in third person."
)


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    cached: bool
    sources: list[str]


def cache_key(question: str) -> str:
    normalized = question.strip().lower()
    return "cv_rag:" + hashlib.sha256(normalized.encode()).hexdigest()


def retrieve_context(question: str) -> list[dict]:
    query_vector = embedder.encode(question, normalize_embeddings=True).tolist()
    results = qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=TOP_K,
    )
    return [
        {"heading": r.payload["heading"], "text": r.payload["text"], "score": r.score}
        for r in results
    ]


def build_prompt(question: str, chunks: list[dict]) -> str:
    context = "\n\n".join(c["text"] for c in chunks)
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"### Context\n{context}\n\n"
        f"### Question\n{question}\n\n"
        f"### Answer\n"
    )


async def call_llm(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            LLM_URL,
            json={
                "prompt": prompt,
                "n_predict": 200,
                "temperature": 0.2,   # low temperature — factual Q&A, not creative writing
                "stop": ["###"],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("content", "").strip()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    key = cache_key(req.question)

    cached = cache.get(key)
    if cached:
        payload = json.loads(cached)
        return AskResponse(answer=payload["answer"], cached=True, sources=payload["sources"])

    chunks = retrieve_context(req.question)
    prompt = build_prompt(req.question, chunks)
    answer = await call_llm(prompt)
    sources = [c["heading"] for c in chunks]

    cache.setex(key, CACHE_TTL_SECONDS, json.dumps({"answer": answer, "sources": sources}))

    return AskResponse(answer=answer, cached=False, sources=sources)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
