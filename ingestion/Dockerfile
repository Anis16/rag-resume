FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=200 \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

COPY cv.md ingest.py .

CMD ["python", "ingest.py"]
