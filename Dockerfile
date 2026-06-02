FROM python:3.11-slim

# ffmpeg (yt-dlp) + git/node (servidor bgutil de PoTokens)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg git curl ca-certificates gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# bgutil PoToken provider — genera PoTokens (BotGuard), lo mismo que usan los sitios de
# descarga. Es lo que desbloquea los formatos del cliente web desde IPs de servidor.
RUN git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil && \
    cd /bgutil/server && npm ci --no-audit --no-fund && npx tsc

# yt-dlp + plugin bgutil (interfaz provider). En Python 3.11 quedan versiones compatibles.
RUN pip install --no-cache-dir yt-dlp bgutil-ytdlp-pot-provider

WORKDIR /app
COPY main.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8080
CMD ["./entrypoint.sh"]
