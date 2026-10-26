# syntax=docker/dockerfile:1
FROM node:24.22.1-bookworm-slim AS battle-dependencies
WORKDIR /build/battle_service
COPY pokemon_rl/battle_service/package.json pokemon_rl/battle_service/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM python:3.11.17-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PORT=8765 \
    FREEFOLDER_SAVE_DIR=/data \
    FREEFOLDER_AUTO_INSTALL_ENGINE=0
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libatomic1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 game \
    && useradd --uid 10001 --gid game --home-dir /app --no-create-home game \
    && mkdir /data \
    && chown game:game /data
COPY --from=battle-dependencies /usr/local/bin/node /usr/local/bin/node
RUN node --version && python --version
WORKDIR /app
COPY pokemon_rl/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=game:game pokemon_rl/ ./
COPY --from=battle-dependencies --chown=game:game /build/battle_service/node_modules ./battle_service/node_modules
USER game
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/api/engine/status', timeout=3).read()"]
CMD ["python", "-X", "utf8", "-B", "-c", "import os, uvicorn; uvicorn.run('server_deploy:app', host='0.0.0.0', port=int(os.environ.get('PORT', '8765')), workers=1)"]
