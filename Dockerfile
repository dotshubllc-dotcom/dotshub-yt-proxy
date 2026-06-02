FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir yt-dlp

WORKDIR /app
COPY main.py .

EXPOSE 8080
# Auto-update yt-dlp on every cold start so it stays current with YouTube changes
CMD pip install -q --upgrade yt-dlp && python3 main.py
