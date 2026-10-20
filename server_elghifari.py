#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EL-GHIFARI AI backend — Groq API + DuckDuckGo web search.
Backend menggunakan Groq; endpoint dan layanan Ollama tidak digunakan.

PENTING:
- Ganti nilai GROQ_API_KEY di bawah dengan API key Groq milik Anda.
- Jangan unggah file ini ke GitHub atau hosting publik jika key sudah diisi.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser
from pathlib import Path
import json
import os
import re
import socket

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
WEB_ROOT = Path(__file__).resolve().parent

# Tempel API key Groq Anda di antara tanda kutip di bawah.
# Contoh format: "gsk_...." — jangan gunakan contoh ini sebagai key sungguhan.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY"),"gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class DDGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._title = False
        self._snippet = False
        self._current = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._current = {"title": "", "url": a.get("href", ""), "snippet": ""}
            self._title = True
        elif self._current and tag in ("a", "div") and "result__snippet" in cls:
            self._snippet = True

    def handle_data(self, data):
        if self._current and self._title:
            self._current["title"] += data
        elif self._current and self._snippet:
            self._current["snippet"] += data

    def handle_endtag(self, tag):
        if self._current and tag == "a" and self._title:
            self._title = False
            if self._current.get("title", "").strip():
                self.results.append(self._current)
                self._current = None
        if tag == "div" and self._snippet:
            self._snippet = False


def web_search(query):
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"},
    )
    with urlopen(req, timeout=12) as resp:
        html = resp.read().decode("utf-8", "replace")
    parser = DDGParser()
    parser.feed(html)
    out, seen = [], set()
    for r in parser.results:
        title = re.sub(r"\s+", " ", r.get("title", "")).strip()
        link = r.get("url", "").strip()
        if not title or not link or link in seen:
            continue
        seen.add(link)
        out.append({
            "title": title,
            "url": link,
            "snippet": re.sub(r"\s+", " ", r.get("snippet", "")).strip(),
        })
        if len(out) >= 8:
            break
    return out


def groq_key_ready():
    # Menolak nilai contoh/placeholder agar tidak salah dianggap sebagai key aktif.
    return bool(GROQ_API_KEY.startswith("gsk_") and len(GROQ_API_KEY) >= 25 and "TEMPELKAN" not in GROQ_API_KEY and "." not in GROQ_API_KEY)


def groq_chat(messages, temperature=0.7, max_tokens=2048, model=None):
    if not groq_key_ready():
        raise RuntimeError(
            "API key Groq belum dikonfigurasi. Isi GROQ_API_KEY di server.py dengan key Groq asli, atau atur variabel lingkungan GROQ_API_KEY."
        )
    chosen_model = str(model or GROQ_MODEL).strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", chosen_model):
        raise RuntimeError("Nama model tidak valid.")
    payload = {
        "model": chosen_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + GROQ_API_KEY,
        },
        method="POST",
    )
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Respons Groq tidak berisi jawaban.")
    return str((choices[0].get("message") or {}).get("content") or "")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print("[EL-GHIFARI]", fmt % args)

    def _json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _groq_error(self, exc):
        if isinstance(exc, HTTPError):
            detail = exc.read().decode("utf-8", "replace")[:1800]
            try:
                parsed = json.loads(detail)
                detail = parsed.get("error", {}).get("message", detail)
            except Exception:
                pass
            return self._json(exc.code, {"error": "Groq API: " + str(detail)})
        return self._json(502, {"error": "Tidak dapat menghubungi Groq API. Periksa internet/API key. Detail: " + str(exc)[:500]})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 15 * 1024 * 1024:
            return self._json(413, {"error": "Request terlalu besar (maksimum 15 MB)."})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return self._json(400, {"error": "JSON request tidak valid."})

        if parsed.path in ("/api/chat", "/api/grok/chat"):
            prompt = str(body.get("prompt") or body.get("message") or body.get("chatInput") or "").strip()
            if not prompt:
                return self._json(400, {"error": "Isi prompt/pesan terlebih dahulu."})
            try:
                answer = groq_chat([{"role": "user", "content": prompt}], model=body.get("model"))
                return self._json(200, {"answer": answer, "reply": answer, "model": GROQ_MODEL})
            except HTTPError as e:
                return self._groq_error(e)
            except Exception as e:
                return self._groq_error(e)

        if parsed.path == "/api/search-chat":
            query = str(body.get("message") or body.get("chatInput") or body.get("prompt") or "").strip()
            if not query:
                return self._json(400, {"reply": "Tuliskan pertanyaan yang ingin dicari di internet."})
            try:
                results = web_search(query)
                if not results:
                    return self._json(200, {"reply": "Pencarian internet tidak menemukan hasil yang terbaca. Coba kata kunci yang lebih spesifik."})
                lines = ["Hasil pencarian web untuk: " + query, ""]
                for i, r in enumerate(results[:6], 1):
                    lines.append(f"{i}. {r['title']}\n{r['url']}\n{r.get('snippet', '').strip()}")
                lines.append("\nCatatan: hasil pencarian web perlu diverifikasi dari sumber aslinya.")
                return self._json(200, {"reply": "\n".join(lines)})
            except Exception as e:
                return self._json(502, {"reply": "Pencarian web gagal. Periksa koneksi internet. Detail: " + str(e)[:300]})
        return self._json(404, {"error": "Endpoint tidak ditemukan."})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            key_ready = groq_key_ready()
            return self._json(200, {
                "ok": True,
                "service": "EL-GHIFARI Groq backend",
                "provider": "Groq",
                "model": GROQ_MODEL,
                "api_key_configured": key_ready,
                "web_search": "DuckDuckGo HTML",
            })
        if parsed.path == "/api/search":
            q = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not q:
                return self._json(400, {"error": "Parameter q wajib diisi."})
            try:
                return self._json(200, {"query": q, "results": web_search(q)})
            except Exception as e:
                return self._json(502, {"error": "Pencarian web gagal: " + str(e)[:300]})
        return super().do_GET()


if __name__ == "__main__":
    os.chdir(WEB_ROOT)
    print("=" * 58)
    print(" EL-GHIFARI AI — GROQ ONLY")
    print(f" Buka browser: http://{HOST}:{PORT}/")
    print(f" Provider AI : Groq | Model: {GROQ_MODEL}")
    print(" API key     :", "terdeteksi" if groq_key_ready() else "BELUM DIATUR — isi GROQ_API_KEY di server.py")
    print("=" * 58)
    print("Biarkan jendela ini tetap terbuka saat memakai aplikasi.")
    print("Tekan Ctrl+C untuk berhenti. Server hanya menerima koneksi komputer ini.")
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except OSError as e:
        print(f"GAGAL menjalankan server pada {HOST}:{PORT}: {e}")
        print("Kemungkinan port 8000 sedang dipakai. Tutup server lama atau ubah ELGHIFARI_PORT.")
        input("Tekan Enter untuk keluar...")
