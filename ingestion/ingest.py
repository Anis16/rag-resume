"""
One-time ingestion job: reads cv.md, splits into sections (each "# Heading"
becomes one chunk), embeds each chunk, and upserts into Qdrant.

Run as a Kubernetes Job — see k8s/ingestion-job.yaml. Re-run any time cv.md
changes (e.g. after updating the CV) to refresh the vector store.
"""

import os
import re
import uuid

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
COLLECTION_NAME = "cv_chunks"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"   # 384-dim, small, fast on CPU
CV_PATH = os.path.join(os.path.dirname(__file__), "cv.md")


def chunk_by_heading(text: str) -> list[dict]:
    """Split markdown into chunks at each '# Heading' boundary."""
    sections = re.split(r"\n(?=# )", text.strip())
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        heading_match = re.match(r"^# (.+)\n", section)
        heading = heading_match.group(1) if heading_match else "CV"
        chunks.append({"heading": heading, "text": section})
    return chunks


def main():
    print(f"Loading CV from {CV_PATH}")
    with open(CV_PATH, "r", encoding="utf-8") as f:
        cv_text = f.read()

    chunks = chunk_by_heading(cv_text)
    print(f"Split into {len(chunks)} chunks:")
    for c in chunks:
        print(f"  - {c['heading']}")

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    print(f"Connecting to Qdrant at {QDRANT_URL}")
    client = QdrantClient(url=QDRANT_URL)

    vector_size = model.get_sentence_embedding_dimension()
    client.recreate_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )

    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding.tolist(),
            payload={"heading": chunk["heading"], "text": chunk["text"]},
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"Upserted {len(points)} chunks into '{COLLECTION_NAME}'")


if __name__ == "__main__":
    main()
