FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.6.1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app/backend

RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -version >/dev/null

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

COPY . ./

EXPOSE 8888 8889 8890

CMD ["python", "-m", "handler"]
