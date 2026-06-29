
#!/usr/bin/env python3
"""
Bonafide Facturas – Servidor local
Solo usa librerías que ya vienen con Python (no requiere pip install).

Uso:
    python3 server.py

Luego abrir en Chrome:  http://localhost:8787
"""
import json, os, pathlib, sys
import urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# En Railway/Render el puerto viene de la variable de entorno PORT
PORT = int(os.environ.get("PORT", 8787))
HOST = "0.0.0.0"  # Escuchar en todas las interfaces (necesario para cloud)
NUCLEO_API = "https://api-prod.nucleocheck.com"
HTML_FILE  = pathlib.Path(__file__).parent / "bonafide_facturas.html"


class Handler(BaseHTTPRequestHandler):

    # ── Routing ──────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_html()
        elif self.path.startswith("/nucleo/"):
            self._proxy("GET", self.path[8:], None)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/nucleo/"):
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            self._proxy("POST", self.path[8:], body)
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Serve local HTML ─────────────────────────────────────────────────
    def _serve_html(self):
        if not HTML_FILE.exists():
            self.send_error(404, "bonafide_facturas.html no encontrado")
            return
        data = HTML_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    # ── Proxy to Nucleo API ──────────────────────────────────────────────
    def _proxy(self, method, nucleo_path, body):
        url = f"{NUCLEO_API}/{nucleo_path}"
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json",
            # Simular pedido desde el navegador de Nucleo (necesario para evitar 403)
            "Origin":       "https://prod.nucleocheck.com",
            "Referer":      "https://prod.nucleocheck.com/",
            "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        # Forward auth token from incoming request
        auth = self.headers.get("Authorization") or self.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data   = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            data   = e.read()
            status = e.code
        except Exception as e:
            self._json_error(502, str(e))
            return

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    # ── CORS headers (allows requests from localhost) ─────────────────────
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json_error(self, code, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Solo imprime rutas /nucleo/ para no llenar la terminal
        if "/nucleo/" in (args[0] if args else ""):
            print(f"  → Nucleo: {args[0][:80]}")


if __name__ == "__main__":
    if not HTML_FILE.exists():
        print(f"\n❌ No se encontró: {HTML_FILE}")
        print("   Asegurate de que server.py esté en la misma carpeta que bonafide_facturas.html\n")
        sys.exit(1)

    httpd = HTTPServer((HOST, PORT), Handler)
    print(f"\n{'='*50}")
    print(f"  ✅ Bonafide Facturas – servidor iniciado")
    print(f"  👉 Escuchando en: {HOST}:{PORT}")
    if HOST == "0.0.0.0":
        print(f"  👉 Local: http://localhost:{PORT}")
    print(f"{'='*50}")
    print("  (Presioná Ctrl+C para detener)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor detenido.")
