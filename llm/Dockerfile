# Dockerfile — llama.cpp server serving a pretrained, pre-quantized model
# No fine-tuning, no conversion — downloaded ready-to-use from Hugging Face.
#
# Build: docker build -t your-registry/cv-rag-llm:v1 .
# Push:  docker push your-registry/cv-rag-llm:v1

FROM ghcr.io/ggml-org/llama.cpp:server

# Pull the pretrained, pre-quantized GGUF directly — no local conversion needed.
# Swap the URL for the 0.5B variant if you want something even lighter:
# https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf
ADD https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    /models/model.gguf

EXPOSE 8080

ENTRYPOINT ["/app/llama-server", \
  "-m", "/models/model.gguf", \
  "--host", "0.0.0.0", \
  "--port", "8080", \
  "-c", "2048", \
  "-np", "1"]
