FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends     git     ffmpeg     curl     docker.io     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium

COPY . .

CMD ["python", "bot.py"]
