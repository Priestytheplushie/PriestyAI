FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    ffmpeg \
    build-essential \
    docker.io \
    && ARCH=$(uname -m) \
    && if [ "$ARCH" = "x86_64" ]; then CF_ARCH="amd64"; \
       elif [ "$ARCH" = "aarch64" ]; then CF_ARCH="arm64"; \
       else CF_ARCH="amd64"; fi \
    && curl -L -o /usr/local/bin/cloudflared "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
    && chmod +x /usr/local/bin/cloudflared \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .

CMD ["python", "bot.py"]