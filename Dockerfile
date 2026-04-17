FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY src/ src/

# Create data directory
RUN mkdir -p data

ENTRYPOINT ["python", "-m", "src.main"]
