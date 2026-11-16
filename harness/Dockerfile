FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

COPY harness/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY harness/app /app/app
COPY mcp_common /app/mcp_common
COPY web/pages /web/pages
COPY web/static /web/static

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
