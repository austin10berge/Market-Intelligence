FROM python:3.12-slim AS base

WORKDIR /app

# install curl for healthchecks and ca-certificates for outbound HTTPS
RUN apt-get update && apt-get install -y curl ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*

# Install dependencies from pyproject.toml
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy all source
COPY src/ src/
COPY discord_bot/ discord_bot/

# Ensure data directory exists
RUN mkdir -p data

# ── test target ──────────────────────────────────────────────────────────────
FROM base AS test
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev]"
COPY tests/ tests/
CMD ["python3", "-m", "pytest", "tests/", "-v", "--tb=short"]

# ── api target ────────────────────────────────────────────────────────────────
FROM base AS api
# Clear any stale bytecode so rebuilt images always run fresh .py source
RUN find /app/src -name "*.pyc" -delete 2>/dev/null || true
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]

# ── discord-bot target ────────────────────────────────────────────────────────
FROM base AS discord-bot
WORKDIR /app/discord_bot
# Make the top-level `src` package importable (bot.py runs from /app/discord_bot,
# so /app is not on sys.path by default). The trade chat cog imports from `src`.
ENV PYTHONPATH=/app
CMD ["python3", "bot.py"]

# ── claude-cli stage: extract self-contained binary from npm package ──────────
FROM node:20-slim AS claude-cli
RUN npm install -g @anthropic-ai/claude-code

# ── pipeline target (scheduled nightly run) ───────────────────────────────────
FROM base AS pipeline
COPY --from=claude-cli /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude
CMD ["python3", "-m", "src.main"]

# ── dashboard target (static web UI served via nginx) ────────────────────────
FROM nginx:alpine AS dashboard
COPY src/web/index.html /usr/share/nginx/html/index.html
COPY src/web/watchlist.html /usr/share/nginx/html/watchlist.html
COPY src/web/backtest.html /usr/share/nginx/html/backtest.html
COPY src/web/technical-analysis.html /usr/share/nginx/html/technical-analysis.html
COPY src/web/scanner.html /usr/share/nginx/html/scanner.html
COPY src/web/index.css /usr/share/nginx/html/index.css
COPY src/web/app.js /usr/share/nginx/html/app.js
COPY src/web/backtest.js /usr/share/nginx/html/backtest.js
COPY src/web/technical-analysis.js /usr/share/nginx/html/technical-analysis.js
COPY src/web/technical-analysis-helpers.js /usr/share/nginx/html/technical-analysis-helpers.js
COPY src/web/scanner.js /usr/share/nginx/html/scanner.js
COPY src/web/v2/ /usr/share/nginx/html/v2/
COPY src/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY src/web/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
