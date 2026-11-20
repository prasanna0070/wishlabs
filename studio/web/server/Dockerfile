# gstudio-web: FastAPI API + agent + the built SPA. Light image: no CadQuery, KiCad or PlatformIO.
# Build context is studio/ (see deploy/web.sh):  docker build -f web/server/Dockerfile .

# ---------- 1. SPA
FROM node:22-slim AS spa
WORKDIR /src/web/app
COPY web/app/package.json web/app/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY web/app/ ./
RUN npm run build && test -f dist/index.html

# ---------- 2. light engine subset (spec, library, normalize and the other top-level modules). Of the stage packages
# only cad/ ships, for its pure-Python kit footprints (normalize's kit overlap checks); CadQuery is not installed.
# firmware/ ships only __init__.py and settings.py (pydantic): normalize checks behaviour.settings with them, so the
# design agent repairs an unknown setting before a concept render instead of the builder refusing it (422).
FROM python:3.12-slim AS engine
WORKDIR /stage
COPY engine/gstudio/ /tmp/gstudio/
COPY examples/ /tmp/examples/
RUN mkdir -p engine/gstudio examples \
 && cp /tmp/gstudio/*.py engine/gstudio/ \
 && for d in /tmp/gstudio/data /tmp/gstudio/library /tmp/gstudio/cad; do [ -d "$d" ] && cp -r "$d" engine/gstudio/ || true; done \
 && mkdir -p engine/gstudio/firmware \
 && cp /tmp/gstudio/firmware/__init__.py /tmp/gstudio/firmware/settings.py engine/gstudio/firmware/ \
 && for f in /tmp/examples/*/spec.json; do [ -f "$f" ] || continue; s=$(basename $(dirname "$f")); \
      mkdir -p examples/$s && cp "$f" examples/$s/; done

# ---------- 3. runtime
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    STUDIO_ROOT=/srv STATIC_DIR=/srv/web/app/dist PORT=8080
WORKDIR /srv/web/server
COPY web/server/requirements.txt ./
RUN pip install -r requirements.txt shapely==2.2.0
COPY --from=engine /stage/engine /srv/engine
COPY --from=engine /stage/examples /srv/examples
COPY library/ /srv/library/
COPY docs/ARCHITECTURE.md /srv/docs/ARCHITECTURE.md
COPY web/server/app/ ./app/
COPY --from=spa /src/web/app/dist /srv/web/app/dist
RUN useradd -r -u 10001 app && python -c "import app.main" 
USER app
EXPOSE 8080
CMD exec uvicorn app.asgi:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' \
    --timeout-keep-alive 75 --no-access-log
