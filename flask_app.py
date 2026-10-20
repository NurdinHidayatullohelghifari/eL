# -*- coding: utf-8 -*-
"""EL-GHIFARI AI API for PythonAnywhere WSGI; API key is read from environment only."""
import os
import re
import json
import time
import base64
import ipaddress
from collections import defaultdict, deque
from threading import Lock
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Flask, request, jsonify

try:
    from dotenv import load_dotenv
    # Supports a private ~/.env file; WSGI may load the project .env before import too.
    load_dotenv(os.path.expanduser("~/.env"), override=False)
    project_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(project_env, override=False)
except ImportError:
    pass

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b").strip()
DEFAULT_ORIGIN = "https://nurdinhidayatullohelghifari.github.io"
ALLOWED_ORIGINS = {
    item.strip().rstrip("/")
    for item in os.environ.get("FRONTEND_ORIGINS", DEFAULT_ORIGIN).split(",")
    if item.strip()
}
ALLOWED_MODELS = {
    item.strip() for item in os.environ.get(
        "GROQ_ALLOWED_MODELS",
        "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"
    ).split(",") if item.strip()
}
ALLOWED_MODELS.update({GROQ_MODEL, GROQ_VISION_MODEL})
RATE_LIMIT = max(1, min(120, int(os.environ.get("EL_RATE_LIMIT_PER_MINUTE", "20"))))
RATE_BUCKETS = defaultdict(deque)
RATE_LOCK = Lock()


def api_key_ready():
    return GROQ_API_KEY.startswith("gsk_") and len(GROQ_API_KEY) > 20 and "YOUR_" not in GROQ_API_KEY


def origin_allowed(origin):
    return bool(origin) and origin.rstrip("/") in ALLOWED_ORIGINS


def client_ip():
    # Do not trust arbitrary X-Forwarded-For headers; use the WSGI remote address.
    return request.remote_addr or "unknown"


def rate_limited(ip):
    now = time.time()
    with RATE_LOCK:
        bucket = RATE_BUCKETS[ip]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            return True
        bucket.append(now)
        # Avoid unbounded stale IP entries in a long-lived worker.
        if len(RATE_BUCKETS) > 5000:
            for key in list(RATE_BUCKETS.keys())[:1000]:
                if not RATE_BUCKETS[key] or now - RATE_BUCKETS[key][-1] >= 600:
                    RATE_BUCKETS.pop(key, None)
    return False


@app.before_request
def browser_origin_guard():
    if request.method == "OPTIONS":
        return _cors_preflight()
    origin = request.headers.get("Origin", "").rstrip("/")
    # Allow command-line/health checks without Origin; restrict browser-origin calls.
    if origin and not origin_allowed(origin):
        return jsonify(error="Origin tidak diizinkan. Periksa FRONTEND_ORIGINS."), 403


def _cors_preflight():
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and not origin_allowed(origin):
        return jsonify(error="Origin tidak diizinkan."), 403
    response = app.make_response(("", 204))
    if origin and origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = "Origin"
    return response


@app.after_request
def security_headers(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def call_groq(messages, model, max_tokens=3072):
    if not api_key_ready():
        raise RuntimeError("API key Groq belum dikonfigurasi pada environment server.")
    if model not in ALLOWED_MODELS:
        raise ValueError("Model tidak diizinkan. Pilih model dari GROQ_ALLOWED_MODELS.")
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.6}
    req = Request(GROQ_URL, data=json.dumps(payload).encode("utf-8"), headers={
        "Authorization": "Bearer " + GROQ_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "EL-GHIFARI-AI/1.0"
    }, method="POST")
    try:
        with urlopen(req, timeout=100) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:1200]
        try:
            detail = json.loads(raw).get("error", {}).get("message", raw)
        except Exception:
            detail = raw
        # Do not return request headers or any secret.
        raise GroqHTTPError(exc.code, str(detail))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Groq tidak mengembalikan jawaban. Periksa model dan kuota.")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    answer = str(content or "").strip()
    if not answer:
        raise RuntimeError("Jawaban AI kosong. Coba kirim ulang pesan.")
    return answer


class GroqHTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def parse_groq_error(exc):
    if isinstance(exc, GroqHTTPError):
        # Pass useful provider message, but never credentials.
        return jsonify(error="Groq API menolak permintaan: " + str(exc)[:700]), exc.status
    if isinstance(exc, URLError):
        return jsonify(error="Server tidak dapat menghubungi Groq melalui HTTPS. Periksa koneksi keluar hosting."), 502
    if isinstance(exc, ValueError):
        return jsonify(error=str(exc)), 400
    message = str(exc)
    if "API key Groq" in message:
        return jsonify(error=message), 503
    return jsonify(error="Permintaan AI gagal. Periksa konfigurasi model, API key, kuota, dan Error Log PythonAnywhere."), 502


def clean_messages(raw, prompt):
    result = []
    if isinstance(raw, list):
        for item in raw[-16:]:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                result.append({"role": item["role"], "content": content.strip()[:12000]})
    # Frontend usually includes the current prompt in messages. Add it if missing.
    if not result or result[-1]["role"] != "user" or result[-1]["content"] != prompt:
        result.append({"role": "user", "content": prompt})
    return result[-16:]


@app.get("/")
def index():
    return jsonify(service="EL-GHIFARI AI API", status="online", health="/health", chat="/api/chat")


@app.get("/health")
def health():
    return jsonify(ok=True, service="EL-GHIFARI AI backend", provider="Groq",
                   model=GROQ_MODEL, vision_model=GROQ_VISION_MODEL,
                   api_key_configured=api_key_ready(), frontend_origins=sorted(ALLOWED_ORIGINS))


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        return ("", 204)
    if rate_limited(client_ip()):
        return jsonify(error="Batas permintaan tercapai. Coba lagi satu menit kemudian."), 429
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Body harus berupa JSON."), 400
    prompt = str(body.get("prompt") or body.get("message") or body.get("chatInput") or "").strip()
    image_data = body.get("imageData")
    if not prompt and not image_data:
        return jsonify(error="Tuliskan pesan atau lampirkan gambar."), 400
    if len(prompt) > 12000:
        return jsonify(error="Pesan terlalu panjang (maksimum 12.000 karakter)."), 413
    system = {"role": "system", "content": (
        "Anda adalah EL-GHIFARI AI, asisten AI serbaguna yang membantu terutama dalam bahasa Indonesia. "
        "Jawab jelas, relevan, ramah, dan terstruktur. Gunakan konteks percakapan yang diberikan. "
        "Jangan mengarang fakta, sumber, atau tindakan yang belum dilakukan. Jika tidak yakin, katakan. "
        "Gunakan Markdown jika membantu. Jangan mengaku sebagai ChatGPT atau OpenAI."
    )}
    try:
        if image_data:
            mime = str(body.get("imageMime") or "image/jpeg").lower().split(";")[0]
            if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                return jsonify(error="Format gambar harus JPEG, PNG, WEBP, atau GIF."), 400
            image_data = str(image_data).strip()
            if image_data.startswith("data:") and "," in image_data:
                header, image_data = image_data.split(",", 1)
                if "image/" in header:
                    mime = header[5:].split(";", 1)[0].lower()
            if len(image_data) > 12 * 1024 * 1024:
                return jsonify(error="Gambar terlalu besar. Gunakan gambar yang lebih kecil."), 413
            try:
                base64.b64decode(image_data, validate=True)
            except Exception:
                return jsonify(error="Data gambar tidak valid. Silakan lampirkan ulang."), 400
            # Image-capable current Groq model; don't trust client to switch it to text-only model.
            vision_model = GROQ_VISION_MODEL
            conversation = clean_messages(body.get("messages"), prompt)
            previous = conversation[:-1]
            multimodal_content = [
                {"type": "text", "text": prompt or "Jelaskan gambar ini secara teliti dalam bahasa Indonesia."},
                {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + image_data}}
            ]
            messages = [system] + previous + [{"role": "user", "content": multimodal_content}]
            answer = call_groq(messages, vision_model)
            return jsonify(answer=answer, reply=answer, model=vision_model)
        requested_model = str(body.get("model") or GROQ_MODEL).strip()
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", requested_model):
            return jsonify(error="Nama model tidak valid."), 400
        if requested_model not in ALLOWED_MODELS:
            requested_model = GROQ_MODEL
        messages = [system] + clean_messages(body.get("messages"), prompt)
        answer = call_groq(messages, requested_model)
        return jsonify(answer=answer, reply=answer, model=requested_model)
    except Exception as exc:
        return parse_groq_error(exc)


class DDGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.in_title = False
        self.in_snippet = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "")
        if tag == "a" and "result__a" in classes:
            self.current = {"title": "", "url": attrs.get("href", ""), "snippet": ""}
            self.in_title = True
        elif self.current and "result__snippet" in classes:
            self.in_snippet = True
    def handle_data(self, data):
        if not self.current: return
        if self.in_title: self.current["title"] += data
        elif self.in_snippet: self.current["snippet"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self.in_title and self.current:
            self.in_title = False
            if self.current["title"].strip(): self.results.append(self.current)
            self.current = None
        if tag in ("div", "a") and self.in_snippet: self.in_snippet = False


def web_search(query):
    query = str(query).strip()[:300]
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 EL-GHIFARI-AI"})
    with urlopen(req, timeout=12) as resp:
        html = resp.read().decode("utf-8", "replace")
    parser = DDGParser(); parser.feed(html)
    results = []; seen = set()
    for item in parser.results:
        title = re.sub(r"\s+", " ", item.get("title", "")).strip()
        link = item.get("url", "").strip()
        if not title or not link or link in seen: continue
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https"): continue
        seen.add(link)
        results.append({"title": title[:300], "url": link, "snippet": re.sub(r"\s+", " ", item.get("snippet", "")).strip()[:1000]})
        if len(results) >= 8: break
    return results


@app.route("/api/search-chat", methods=["POST", "OPTIONS"])
def search_chat():
    if request.method == "OPTIONS": return ("", 204)
    if rate_limited(client_ip()):
        return jsonify(error="Batas permintaan tercapai. Coba lagi satu menit kemudian."), 429
    body = request.get_json(silent=True) or {}
    query = str(body.get("message") or body.get("chatInput") or body.get("prompt") or "").strip()
    if not query: return jsonify(reply="Tuliskan pertanyaan yang ingin dicari di internet."), 400
    try:
        results = web_search(query)
        if not results: return jsonify(reply="Pencarian tidak menemukan hasil yang terbaca. Coba kata kunci lain.")
        lines = ["Hasil pencarian web untuk: " + query, ""]
        for i, result in enumerate(results[:6], 1):
            lines.append(f"{i}. {result['title']}\n{result['url']}\n{result['snippet']}")
        lines.append("\nPeriksa sumber aslinya untuk memastikan ketepatan informasi.")
        return jsonify(reply="\n".join(lines), results=results[:6])
    except Exception:
        return jsonify(reply="Pencarian web gagal. Hosting mungkin tidak mengizinkan koneksi keluar atau DuckDuckGo sedang membatasi permintaan."), 502


@app.route("/api/search", methods=["GET", "OPTIONS"])
def search():
    if request.method == "OPTIONS": return ("", 204)
    query = str(request.args.get("q", "")).strip()
    if not query: return jsonify(error="Parameter q wajib diisi."), 400
    try: return jsonify(query=query, results=web_search(query))
    except Exception: return jsonify(error="Pencarian web gagal. Periksa koneksi keluar hosting."), 502


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Permintaan terlalu besar. Maksimum 15 MB."), 413


@app.errorhandler(500)
def internal_error(_error):
    # Do not disclose stack traces or environment secrets to public clients.
    return jsonify(error="Terjadi kesalahan internal. Periksa Error Log PythonAnywhere."), 500
