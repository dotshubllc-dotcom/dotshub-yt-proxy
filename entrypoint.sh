#!/bin/sh
# Servidor de PoTokens bgutil en background (localhost:4416). El plugin de yt-dlp
# lo descubre automáticamente y pide un PoToken al resolver el cliente web.
node /bgutil/server/build/main.js --port 4416 >/tmp/bgutil.log 2>&1 &

# Marcador de build para verificar deploys (lo reporta GET / vía env)
export BUILD_TAG=3

# yt-proxy (Python) en el puerto que asigna Render
exec python3 main.py
