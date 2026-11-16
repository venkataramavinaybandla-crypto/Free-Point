FROM docker:27-cli AS dockercli

FROM python:3.12-slim-bookworm

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

COPY mcp/rae/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY mcp_common /app/mcp_common
COPY mcp/rae/rae_mcp /app/rae_mcp

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

CMD ["python", "-m", "rae_mcp.server"]
