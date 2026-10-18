FROM python:3.13.16-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY services/api/requirements.lock /build/requirements.lock
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.lock
COPY services/api /build/services/api
RUN /opt/venv/bin/pip install --no-deps /build/services/api

FROM python:3.13.16-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 can && useradd --uid 10001 --gid 10001 --no-create-home can
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY services/api/alembic.ini /app/services/api/alembic.ini
COPY services/api/alembic /app/services/api/alembic
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "can_api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
