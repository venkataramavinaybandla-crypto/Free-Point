FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080 \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    RAG_S3_BUCKET=free-point-rag-indexes \
    RAG_POINTERS_TABLE=free-point-rag_pointers

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

COPY mcp/rag/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Bake the ONNX embedding model into the image; runtime is offline.
RUN mkdir -p /opt/fastembed && \
    python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed')" && \
    chown -R appuser:appuser /opt/fastembed

COPY mcp_common /app/mcp_common
COPY mcp/rag/rag_mcp /app/rag_mcp

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

CMD ["python", "-m", "rag_mcp.server"]
