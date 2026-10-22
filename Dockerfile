FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home memo && mkdir /data && chown memo:memo /data
USER memo
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn memo.main:create_app --factory --host 0.0.0.0 --port 8000"]
