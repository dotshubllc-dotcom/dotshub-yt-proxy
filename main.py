#!/usr/bin/env python3
"""
yt-proxy — YouTube URL resolver for dotshub-clips-api
Uses InnerTube ANDROID_VR (bypasses PO token from datacenter IPs).
Cookies loaded for yt-dlp fallback only — NOT sent to InnerTube.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, subprocess, os, logging, base64, tempfile, atexit, re
import urllib.request

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

YT_DLP = os.environ.get('YT_DLP_PATH', 'yt-dlp')
PROXY_SECRET = os.environ.get('YT_PROXY_SECRET', '')

COOKIES_FILE = None
_cookies_b64 = os.environ.get('YT_COOKIES_B64', '')
if _cookies_b64:
    try:
        _tmp = tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False)
        _tmp.write(base64.b64decode(_cookies_b64))
        _tmp.close()
        COOKIES_FILE = _tmp.name
        logging.info(f'Cookies loaded → {COOKIES_FILE}')
        atexit.register(os.unlink, COOKIES_FILE)
    except Exception as e:
        logging.warning(f'Failed to load YT_COOKIES_B64: {e}')


# ── InnerTube clients (NO cookies — keeps requests clean) ────────────────────

_INNERTUBE_URL = 'https://www.youtube.com/youtubei/v1/player?prettyPrint=false'

_CLIENTS = [
    {
        'name': 'ANDROID_VR', 'num': '28', 'version': '1.60.19',
        'ua': 'com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 10; GB) gzip',
        'extra': {'androidSdkVersion': 30},
    },
    {
        'name': 'ANDROID', 'num': '3', 'version': '19.09.37',
        'ua': 'com.google.android.youtube/19.09.37(Linux; U; Android 11) gzip',
        'extra': {'androidSdkVersion': 30},
    },
    {
        'name': 'TVHTML5_SIMPLY_EMBEDDED_PLAYER', 'num': '85', 'version': '2.0',
        'ua': 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.0) AppleWebKit/538.1',
        'extra': {},
    },
]


def innertube_resolve(video_id):
    last_err = None
    for client in _CLIENTS:
        try:
            body = json.dumps({
                'context': {'client': {
                    'clientName':    client['name'],
                    'clientVersion': client['version'],
                    'hl': 'en', 'timeZone': 'UTC', 'utcOffsetMinutes': 0,
                    'userAgent': client['ua'],
                    **client['extra'],
                }},
                'videoId': video_id,
                'playbackContext': {
                    'contentPlaybackContext': {'html5Preference': 'HTML5_PREF_WANTS'}
                },
            }).encode()

            req = urllib.request.Request(_INNERTUBE_URL, data=body, headers={
                'Content-Type': 'application/json',
                'User-Agent': client['ua'],
                'X-Youtube-Client-Name':    client['num'],
                'X-Youtube-Client-Version': client['version'],
                'Origin': 'https://www.youtube.com',
            })

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            ps = data.get('playabilityStatus', {})
            if ps.get('status') not in ('OK', None):
                last_err = f"{client['name']}: {ps.get('status')} — {ps.get('reason','')}"
                logging.warning(f'InnerTube {last_err}')
                continue

            streaming = data.get('streamingData', {})
            title = data.get('videoDetails', {}).get('title', 'video')
            formats  = streaming.get('formats', [])
            adaptive = streaming.get('adaptiveFormats', [])

            # Combined mp4
            combined = [f for f in formats
                        if f.get('mimeType', '').startswith('video/mp4')
                        and f.get('url', '').startswith('http')]
            if combined:
                best = max(combined, key=lambda f: f.get('height', 0) or 0)
                h = best.get('height', 360)
                logging.info(f'[{client["name"]}] combined {h}p — {title[:50]}')
                return {'url': best['url'], 'title': title, 'type': 'combined', 'height': h}

            # Adaptive
            vids = [f for f in adaptive
                    if f.get('mimeType', '').startswith('video/')
                    and f.get('url', '').startswith('http')]
            auds = [f for f in adaptive
                    if f.get('mimeType', '').startswith('audio/')
                    and f.get('url', '').startswith('http')]

            if vids and auds:
                under720 = [f for f in vids if (f.get('height') or 0) <= 720] or vids
                best_v = max(under720, key=lambda f: f.get('bitrate', 0) or 0)
                best_a = max(auds,    key=lambda f: f.get('bitrate', 0) or 0)
                h = best_v.get('height', 480)
                logging.info(f'[{client["name"]}] adaptive {h}p — {title[:50]}')
                return {'videoUrl': best_v['url'], 'audioUrl': best_a['url'],
                        'title': title, 'type': 'adaptive', 'height': h}

            last_err = f"{client['name']}: no usable streams"
            logging.warning(last_err)

        except Exception as e:
            last_err = f"{client['name']}: {e}"
            logging.warning(f'InnerTube error: {last_err}')

    raise ValueError(last_err or 'All InnerTube clients failed')


# ── yt-dlp fallback ───────────────────────────────────────────────────────────

def pick_formats(formats, title):
    combined = [f for f in formats
                if f.get('vcodec', 'none') not in ('none', None)
                and f.get('acodec', 'none') not in ('none', None)
                and f.get('url', '').startswith('http')]
    if combined:
        under720 = [f for f in combined if (f.get('height') or 0) <= 720] or combined
        best = max(under720, key=lambda f: (f.get('height') or 0))
        return {'url': best['url'], 'title': title, 'type': 'combined', 'height': best.get('height') or 360}
    videos = [f for f in formats if f.get('vcodec', 'none') not in ('none', None)
              and f.get('acodec', 'none') in ('none', None) and f.get('url', '').startswith('http')]
    audios = [f for f in formats if f.get('acodec', 'none') not in ('none', None)
              and f.get('vcodec', 'none') in ('none', None) and f.get('url', '').startswith('http')]
    if not videos or not audios:
        return None
    under720v = [f for f in videos if (f.get('height') or 0) <= 720] or videos
    best_v = max(under720v, key=lambda f: (f.get('height') or 0))
    best_a = max(audios, key=lambda f: (f.get('tbr') or f.get('abr') or 0))
    return {'videoUrl': best_v['url'], 'audioUrl': best_a['url'],
            'title': title, 'type': 'adaptive', 'height': best_v.get('height') or 480}


def ytdlp_resolve(yt_url):
    auth_args = ['--cookies', COOKIES_FILE] if COOKIES_FILE else []
    r = subprocess.run(
        [YT_DLP, '--no-config', '--no-warnings', '--no-playlist', '--socket-timeout', '30',
         # Con cookies (YT_COOKIES_B64) el cliente web es el más completo; sin cookies,
         # web_safari/tv son los menos bloqueados. Varios fallbacks.
         '--extractor-args', 'youtube:player_client=web,web_safari,android_vr,tv,mweb',
         # -f permisivo + ignore: NO abortar con "Requested format is not available";
         # dejamos que pick_formats elija de lo que tenga URL utilizable.
         '-f', 'bv*+ba/b/best',
         '--ignore-no-formats-error', '--ignore-errors',
         '--dump-single-json'] + auth_args + [yt_url],
        capture_output=True, text=True, timeout=90
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise ValueError((r.stderr or '').strip()[:300] or 'yt-dlp failed')
    info = json.loads(r.stdout)
    result = pick_formats(info.get('formats', []), info.get('title', 'video'))
    if not result:
        raise ValueError('No usable formats from yt-dlp')
    return result


def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else None


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health'):
            try:
                v = subprocess.run([YT_DLP, '--version'], capture_output=True, text=True, timeout=10)
                ver = v.stdout.strip()
            except Exception:
                ver = 'unknown'
            # ¿está vivo el servidor bgutil de PoTokens?
            try:
                urllib.request.urlopen('http://127.0.0.1:4416/ping', timeout=3)
                pot = 'up'
            except Exception:
                pot = 'down'
            body = f'ok build=3 yt-dlp={ver} cookies={"yes" if COOKIES_FILE else "no"} bgutil={pot}'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/debug'):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            u = (q.get('u') or [''])[0] or 'https://www.youtube.com/watch?v=Qs-Is3xEzGk'
            ck = ['--cookies', COOKIES_FILE] if COOKIES_FILE else []
            cmd = [YT_DLP, '-v', '--no-config', '--no-warnings', '--no-playlist',
                   '--extractor-args', 'youtube:player_client=web,web_safari,tv,mweb'] + ck + \
                  ['--simulate', '--dump-json', u]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            out = (r.stderr or '')[-3500:] + '\n--- STDOUT len: ' + str(len(r.stdout or '')) + ' rc=' + str(r.returncode)
            self._json(200, {'debug': out})
            return
        self._json(404, {'error': 'Not found'})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path != '/resolve':
            self._json(404, {'error': 'Not found'})
            return
        if PROXY_SECRET and self.headers.get('X-Proxy-Secret') != PROXY_SECRET:
            self._json(401, {'error': 'Unauthorized'})
            return
        n = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            self._json(400, {'error': 'Invalid JSON'})
            return
        yt_url = body.get('url', '').strip()
        if not yt_url:
            self._json(400, {'error': 'Missing url'})
            return

        logging.info(f'resolve: {yt_url}')

        video_id = extract_video_id(yt_url)
        if video_id:
            try:
                result = innertube_resolve(video_id)
                self._json(200, result)
                return
            except Exception as e:
                logging.warning(f'InnerTube all failed: {e} — trying yt-dlp')

        try:
            result = ytdlp_resolve(yt_url)
            self._json(200, result)
        except subprocess.TimeoutExpired:
            self._json(504, {'error': 'Timeout resolving video'})
        except Exception as e:
            self._json(422, {'error': str(e)})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Proxy-Secret')

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logging.info(f'yt-proxy listening on :{port}')
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
