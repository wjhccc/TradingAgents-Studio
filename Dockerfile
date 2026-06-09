FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
# Install with the [all] extra so the container has the Web Studio deps
# (fastapi/uvicorn/websockets) plus the A-share data + sentiment stacks —
# the bare `pip install .` only pulls core CLI deps, which is why the web
# entrypoint failed with ModuleNotFoundError: uvicorn.
RUN pip install --no-cache-dir ".[all]"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents
USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

# Serve the web app from this source tree (not the installed package): the
# backend resolves the built SPA via a path relative to web/backend/main.py,
# and the frontend bundle lives at web/frontend/dist here — it isn't shipped
# inside the pip-installed package. Running uvicorn over the source keeps that
# path valid. Host/reload come from env so the same image serves a published
# port (compose sets TRADINGAGENTS_WEB_HOST=0.0.0.0, RELOAD=0).
ENV PYTHONPATH=/home/appuser/app
EXPOSE 8000

ENTRYPOINT ["python", "-m", "web.backend.run"]

