FROM python:3.12-slim AS base

WORKDIR /app

# install curl for healthchecks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install dependencies from pyproject.toml
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy all source
COPY src/ src/
COPY discord_bot/ discord_bot/

# Ensure data directory exists
RUN mkdir -p data

# ── api target ────────────────────────────────────────────────────────────────
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── discord-bot target ────────────────────────────────────────────────────────
FROM base AS discord-bot
WORKDIR /app/discord_bot
CMD ["python3", "bot.py"]

# ── pipeline target (scheduled nightly run) ───────────────────────────────────
FROM base AS pipeline
CMD ["python3", "-m", "src.main"]

# ── dashboard target (static web UI served via nginx) ────────────────────────
FROM nginx:alpine AS dashboard
COPY src/web/index.html /usr/share/nginx/html/index.html
COPY src/web/watchlist.html /usr/share/nginx/html/watchlist.html
COPY src/web/backtest.html /usr/share/nginx/html/backtest.html
COPY src/web/index.css /usr/share/nginx/html/index.css
COPY src/web/app.js /usr/share/nginx/html/app.js
COPY src/web/backtest.js /usr/share/nginx/html/backtest.js
COPY src/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY src/web/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
