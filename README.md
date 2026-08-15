# CV RAG bot

A retrieval-augmented Q&A bot that answers questions about a CV, running
entirely on Kubernetes, with a minimal built-in chat UI.

## Architecture

```
                        ┌─────────────┐
 browser ── chat UI ──► │ orchestrator │
                        │  (FastAPI)   │
                        └──────┬───────┘
                               │
                 ┌─────────────┼───────────────┐
                 ▼             ▼               ▼
            ┌────────┐   ┌──────────┐   ┌─────────────┐
            │ redis  │   │  qdrant  │   │ llm-server   │
            │ (cache)│   │(vector DB)│  │ (llama.cpp)  │
            └────────┘   └──────────┘   └─────────────┘
                               ▲
                               │ one-time load
                        ┌──────┴───────┐
                        │ ingestion Job│
                        │  (cv.md →    │
                        │   embeddings)│
                        └──────────────┘
```

**Components** (each is its own image/deployment, all in the `cv-rag`
namespace):

- **`ingestion`** — a Kubernetes `Job`, run once and again any time `cv.md`
  changes. Splits `cv.md` into chunks (one per `# Heading`), embeds each
  chunk with `BAAI/bge-small-en-v1.5`, and upserts them into Qdrant. Wipes
  and rebuilds the collection every run (`recreate_collection`), so there's
  no stale-chunk cleanup to worry about.
- **`orchestrator`** — a FastAPI service exposing `POST /ask` and a static
  chat UI at `GET /`. Per request: check Redis cache → embed the question
  in-process → search Qdrant for the top-3 relevant CV chunks → build a
  grounded prompt → call `llm-server` → cache the answer → return it.
- **`qdrant`** — vector DB holding the embedded CV chunks. Single-replica
  `StatefulSet` with a `PersistentVolumeClaim` — the CV corpus is tiny and
  static, no need for a cluster.
- **`redis`** — caches answers for 7 days, keyed by a hash of the normalized
  question. Repeated questions skip retrieval and the LLM call entirely
  (`"cached": true` in the response).
- **`llm-server`** — `llama.cpp`'s server binary serving a small pretrained,
  pre-quantized instruct model (Qwen2.5-1.5B-Instruct, GGUF, Q4_K_M). No
  fine-tuning: RAG supplies the facts, the base model supplies the language
  ability. Runs with a single slot (`-np 1`) so the full context window
  (2048 tokens) is available to one request at a time — enough headroom
  for a RAG prompt plus a real answer.

**Autoscaling (optional, not installed by default)**: `k8s/http-scaledobject.yaml`
wires up KEDA's HTTP add-on to scale `orchestrator` on live request volume.
It's left out of the default deploy — see "Optional: KEDA autoscaling"
below — since it adds real memory/CPU overhead that a small local cluster
may not have to spare.

## Chat UI

`orchestrator` serves a minimal static chat page at `GET /`
(`orchestrator/static/index.html`) alongside the JSON API. It posts to
`/ask` and renders the answer with its sources — no separate frontend
service, build step, or image needed.

```bash
kubectl port-forward -n cv-rag svc/orchestrator 8000:80
```

Then open `http://localhost:8000` in a browser.

## Prerequisites

- A running Kubernetes cluster (this project targets local minikube).
- `kubectl` pointed at that cluster.
- `docker`, built against the cluster's own daemon so images don't need a
  registry push:
  ```bash
  eval $(minikube docker-env)
  ```
- **`ingestion/cv.md` is empty in this repo — add your own CV content there
  before building the ingestion image.** Use `# Heading` per section; each
  heading becomes one retrievable chunk (e.g. `# Experience`, `# Skills`,
  `# Education`).

## Build images

Images are built straight into the cluster's Docker daemon (see
prerequisites) and referenced without a registry prefix, so there's nothing
to push. `ingestion` and `orchestrator` both install `torch` from PyTorch's
CPU-only wheel index before the rest of `requirements.txt` — without that,
`sentence-transformers` pulls in the full CUDA build of torch (700MB+ of
GPU wheels neither image needs, since embedding runs CPU-only here).

```bash
docker build -t cv-rag-ingest:v1 ./ingestion
docker build -t cv-rag-orchestrator:v1 ./orchestrator
docker build -t cv-rag-llm:v1 ./llm
```

If you'd rather use a real registry, tag and push as usual, then update
`image:` (and drop `imagePullPolicy: Never`) in `k8s/ingestion-job.yaml`,
`k8s/orchestrator.yaml`, and `k8s/llm-server.yaml`.

## Deploy, in order

```bash
kubectl apply -f k8s/data-layer.yaml       # namespace + Qdrant + Redis
kubectl wait --for=condition=ready pod -l app=qdrant -n cv-rag --timeout=120s

kubectl apply -f k8s/ingestion-job.yaml    # loads cv.md into Qdrant
kubectl wait --for=condition=complete job/cv-ingest -n cv-rag --timeout=120s

kubectl apply -f k8s/llm-server.yaml
kubectl apply -f k8s/orchestrator.yaml
kubectl wait --for=condition=ready pod -l app=orchestrator -n cv-rag --timeout=120s
```

## Smoke test

```bash
kubectl port-forward -n cv-rag svc/orchestrator 8000:80

curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Anis experience with Kubernetes?"}'
```

Run the same question twice — the second response should come back with
`"cached": true` and noticeably faster, confirming the Redis cache is
working. On modest CPU-only hardware, a cold (uncached) answer can take
well over a minute — `orchestrator`'s HTTP client to `llm-server` is
configured with a 180s timeout to accommodate that.

## Optional: KEDA autoscaling

Not part of the default deploy (see "Autoscaling" above). To wire it up:

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
kubectl create namespace keda
helm install keda kedacore/keda --namespace keda
helm install http-add-on kedacore/keda-add-ons-http --namespace keda

kubectl apply -f k8s/http-scaledobject.yaml
```

Edit `hosts` in `k8s/http-scaledobject.yaml` to your actual domain first,
and route your Ingress to the HTTP add-on's interceptor service rather than
straight to `orchestrator`. If pods land `Pending` on a small cluster,
reduce the add-on's default replica counts:

```bash
helm upgrade http-add-on kedacore/keda-add-ons-http --namespace keda \
  --set scaler.replicas=1 \
  --set interceptor.replicas.min=1
```

## Updating the CV later

```bash
# edit ingestion/cv.md, rebuild the ingestion image, then:
kubectl delete job cv-ingest -n cv-rag   # Jobs are immutable, delete before re-applying
kubectl apply -f k8s/ingestion-job.yaml
```

## Repo structure

```
ingestion/        # embeds cv.md into Qdrant — run as a Job
  cv.md              # <-- your CV content goes here (empty by default)
  ingest.py
  Dockerfile
  requirements.txt

orchestrator/      # FastAPI RAG service (POST /ask) + chat UI (GET /)
  main.py
  static/
    index.html         # minimal chat UI, calls /ask
  Dockerfile
  requirements.txt

llm/              # llama.cpp server + GGUF model
  Dockerfile

k8s/
  data-layer.yaml         # namespace, Qdrant StatefulSet, Redis Deployment
  ingestion-job.yaml       # one-shot ingestion Job
  llm-server.yaml          # llama.cpp Deployment + Service
  orchestrator.yaml        # orchestrator Deployment + Service
  http-scaledobject.yaml   # optional: KEDA HTTPScaledObject for the orchestrator
```
