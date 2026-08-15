FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=200 \
    --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install --no-cache-dir --default-timeout=200 -r requirements.txt

COPY main.py .
COPY static/ static/

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
