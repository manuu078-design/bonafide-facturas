#!/usr/bin/env python3
"""
Bonafide Facturas – Servidor
Solo usa librerías que ya vienen con Python (no requiere pip install).

Rutas:
    /            → dashboard (bonafide_facturas.html)
    /cargar      → página móvil para que los empleados suban fotos de facturas
    /nucleo/...  → proxy a la API de NucleoCheck
    /api/...     → bandeja de facturas recibidas (fotos de los locales)

Los datos de la bandeja se guardan en el volumen de Railway
(RAILWAY_VOLUME_MOUNT_PATH) o en ./data si no hay volumen.
El PIN de carga sale de la variable de entorno UPLOAD_PIN (default 1234).
"""
import base64, json, os, pathlib, re, sys, time, uuid
import urllib.request, urllib.error, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 8787))
HOST = "0.0.0.0"
NUCLEO_API = "https://api-prod.nucleocheck.com"
HTML_FILE  = pathlib.Path(__file__).parent / "bonafide_facturas.html"

UPLOAD_PIN = os.environ.get("UPLOAD_PIN", "1234")
DATA_DIR   = pathlib.Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or
                          os.environ.get("DATA_DIR") or
                          (pathlib.Path(__file__).parent / "data"))
PHOTOS_DIR = DATA_DIR / "photos"
INBOX_FILE = DATA_DIR / "inbox.json"

LOCATIONS = ["Palmas del Pilar", "Unicenter", "Leloir", "Obligado", "Juramento"]

MAX_UPLOAD = 30 * 1024 * 1024  # 30 MB por request


def _ensure_dirs():
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)


def load_inbox():
    try:
        return json.loads(INBOX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_inbox(items):
    _ensure_dirs()
    INBOX_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════
# Página de carga para empleados (móvil)
# ═══════════════════════════════════════════════════════════════════════
EMPLOYEE_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Cargar factura – Bonafide</title>
<style>
  *{box-sizing:border-box;font-family:-apple-system,'Segoe UI',Roboto,sans-serif}
  body{margin:0;background:#f5f6f8;color:#222}
  header{background:#1a1a2e;color:#e2c96e;padding:14px 20px;font-weight:700;font-size:18px}
  main{max-width:480px;margin:0 auto;padding:16px}
  .card{background:#fff;border-radius:14px;box-shadow:0 2px 10px rgba(0,0,0,.08);padding:18px;margin-bottom:14px}
  label{display:block;font-weight:600;font-size:14px;margin:12px 0 6px}
  select,input[type=text],input[type=password],textarea{width:100%;padding:12px;border:1px solid #d5d8dc;border-radius:10px;font-size:16px}
  .photo-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:20px;border:2px dashed #b8bcc4;border-radius:12px;background:#fafbfc;color:#555;font-size:16px;font-weight:600;cursor:pointer}
  #thumbs{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
  .thumb{position:relative;width:84px;height:84px}
  .thumb img{width:100%;height:100%;object-fit:cover;border-radius:10px;border:1px solid #ddd}
  .thumb .pdfbox{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#fdecea;color:#c0392b;border-radius:10px;font-weight:700;font-size:12px;border:1px solid #f5c6c1}
  .thumb button{position:absolute;top:-7px;right:-7px;width:22px;height:22px;border-radius:50%;border:none;background:#c0392b;color:#fff;font-size:13px;line-height:1;cursor:pointer}
  .send{width:100%;padding:15px;margin-top:16px;border:none;border-radius:12px;background:#1a1a2e;color:#fff;font-size:17px;font-weight:700;cursor:pointer}
  .send:disabled{opacity:.55}
  #msg{margin-top:12px;padding:12px;border-radius:10px;font-size:15px;display:none}
  #msg.ok{display:block;background:#d4edda;color:#155724}
  #msg.err{display:block;background:#f8d7da;color:#721c24}
  .pin-note{font-size:12px;color:#888;margin-top:4px}
</style>
</head>
<body>
<header>🧾 Bonafide – Cargar factura</header>
<main>
  <div class="card" id="pin-card" style="display:none">
    <label>PIN de acceso</label>
    <input type="password" id="pin" inputmode="numeric" maxlength="8" placeholder="••••">
    <p class="pin-note">Pedíselo al encargado. Se guarda en este dispositivo.</p>
  </div>
  <div class="card">
    <label>Local</label>
    <select id="loc">
      <option value="">— Elegí el local —</option>
      __LOCS__
    </select>
    <label>Fotos de la factura</label>
    <input type="file" id="file" accept="image/*,.pdf" multiple style="display:none">
    <div class="photo-btn" onclick="document.getElementById('file').click()">📷 Sacar foto / elegir archivo</div>
    <div id="thumbs"></div>
    <label>Nota <span style="font-weight:400;color:#999">(opcional)</span></label>
    <input type="text" id="note" placeholder="ej. llegó con el pedido de hoy">
    <button class="send" id="send" onclick="send()">Enviar factura</button>
    <div id="msg"></div>
  </div>
</main>
<script>
let files = [];   // {name, type, data(base64 sin prefijo), preview}
const $ = id => document.getElementById(id);

// PIN guardado en el dispositivo
if (!localStorage.getItem('bf_pin')) $('pin-card').style.display = 'block';
else $('pin').value = localStorage.getItem('bf_pin');
const savedLoc = localStorage.getItem('bf_loc');

$('file').addEventListener('change', async e => {
  for (const f of e.target.files) {
    if (f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')) {
      const b64 = await readB64(f);
      files.push({name: f.name, type: 'application/pdf', data: b64, preview: null});
    } else {
      const c = await compress(f);
      files.push(c);
    }
  }
  e.target.value = '';
  renderThumbs();
});

function readB64(f) {
  return new Promise(res => {
    const r = new FileReader();
    r.onload = () => res(r.result.split(',')[1]);
    r.readAsDataURL(f);
  });
}

// Comprimir imagen a máx 1800px JPEG 0.82 para que suba rápido con 4G
function compress(f) {
  return new Promise(res => {
    const img = new Image();
    img.onload = () => {
      const MAX = 1800;
      let w = img.width, h = img.height;
      if (Math.max(w,h) > MAX) { const k = MAX/Math.max(w,h); w = Math.round(w*k); h = Math.round(h*k); }
      const cv = document.createElement('canvas');
      cv.width = w; cv.height = h;
      cv.getContext('2d').drawImage(img, 0, 0, w, h);
      const url = cv.toDataURL('image/jpeg', 0.82);
      res({name: f.name.replace(/\\.[^.]+$/,'') + '.jpg', type: 'image/jpeg',
           data: url.split(',')[1], preview: url});
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => { // formato raro: mandar tal cual
      readB64(f).then(b64 => res({name: f.name, type: f.type||'image/jpeg', data: b64, preview: null}));
    };
    img.src = URL.createObjectURL(f);
  });
}

function renderThumbs() {
  $('thumbs').innerHTML = files.map((f,i) =>
    `<div class="thumb">${f.preview
        ? `<img src="${f.preview}">`
        : `<div class="pdfbox">PDF</div>`}
      <button onclick="files.splice(${i},1);renderThumbs()">×</button></div>`).join('');
}

if (savedLoc) setTimeout(() => { $('loc').value = savedLoc; }, 0);

async function send() {
  const pin = $('pin').value.trim();
  const loc = $('loc').value;
  const msg = $('msg');
  msg.className = '';
  if (!pin) { $('pin-card').style.display='block'; msg.className='err'; msg.textContent='Ingresá el PIN.'; return; }
  if (!loc) { msg.className='err'; msg.textContent='Elegí el local.'; return; }
  if (!files.length) { msg.className='err'; msg.textContent='Sacá al menos una foto.'; return; }
  const btn = $('send');
  btn.disabled = true; btn.textContent = 'Enviando...';
  try {
    const r = await fetch('/api/upload', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin, loc, note: $('note').value.trim(),
                            photos: files.map(f => ({name: f.name, type: f.type, data: f.data}))})
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || 'Error del servidor');
    localStorage.setItem('bf_pin', pin);
    localStorage.setItem('bf_loc', loc);
    $('pin-card').style.display = 'none';
    files = []; renderThumbs(); $('note').value = '';
    msg.className = 'ok';
    msg.textContent = '✅ Factura enviada. ¡Gracias! Podés cargar otra.';
  } catch(e) {
    msg.className = 'err';
    msg.textContent = '❌ ' + e.message;
    if (/PIN/i.test(e.message)) { localStorage.removeItem('bf_pin'); $('pin-card').style.display='block'; }
  } finally {
    btn.disabled = false; btn.textContent = 'Enviar factura';
  }
}
</script>
</body>
</html>"""
EMPLOYEE_HTML = EMPLOYEE_HTML.replace(
    "__LOCS__",
    "\n      ".join(f'<option>{l}</option>' for l in LOCATIONS))


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):

    # ── Routing ──────────────────────────────────────────────────────────
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._serve_html()
        elif path in ("/cargar", "/cargar/"):
            self._send_bytes(EMPLOYEE_HTML.encode(), "text/html; charset=utf-8")
        elif path == "/api/inbox":
            self._api_inbox(query)
        elif path.startswith("/api/photo/"):
            self._api_photo(path[len("/api/photo/"):], query)
        elif path.startswith("/nucleo/"):
            self._proxy("GET", self.path[8:], None)
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/nucleo/"):
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            self._proxy("POST", self.path[8:], body)
        elif path == "/api/upload":
            self._api_upload()
        elif path == "/api/inbox/update":
            self._api_inbox_update()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── API: bandeja de facturas ─────────────────────────────────────────
    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_UPLOAD:
            self._json_error(413, "Archivo demasiado grande (máx 30 MB). Sacá menos fotos por vez.")
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._json_error(400, "JSON inválido")
            return None

    def _check_pin(self, pin):
        if str(pin or "") != UPLOAD_PIN:
            self._json_error(403, "PIN incorrecto")
            return False
        return True

    def _api_upload(self):
        data = self._read_json()
        if data is None:
            return
        if not self._check_pin(data.get("pin")):
            return
        loc = str(data.get("loc") or "").strip()
        photos = data.get("photos") or []
        if not loc or not photos:
            self._json_error(400, "Faltan el local o las fotos")
            return
        _ensure_dirs()
        item_id = uuid.uuid4().hex[:12]
        saved = []
        for i, ph in enumerate(photos[:8]):
            ext = ".pdf" if "pdf" in str(ph.get("type", "")) else ".jpg"
            fname = f"{item_id}_{i}{ext}"
            try:
                (PHOTOS_DIR / fname).write_bytes(base64.b64decode(ph.get("data", "")))
                saved.append(fname)
            except Exception:
                pass
        if not saved:
            self._json_error(400, "No se pudo guardar ninguna foto")
            return
        items = load_inbox()
        items.insert(0, {
            "id": item_id,
            "loc": loc,
            "note": str(data.get("note") or "")[:300],
            "ts": time.strftime("%d/%m/%Y %H:%M"),
            "photos": saved,
            "status": "new",
        })
        save_inbox(items)
        self._send_json({"ok": True, "id": item_id})

    def _api_inbox(self, query):
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if not self._check_pin(urllib.parse.unquote(params.get("pin", ""))):
            return
        self._send_json(load_inbox())

    def _api_photo(self, name, query):
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if not self._check_pin(urllib.parse.unquote(params.get("pin", ""))):
            return
        if not re.fullmatch(r"[a-f0-9]+_\d+\.(jpg|pdf)", name):
            self.send_error(404)
            return
        f = PHOTOS_DIR / name
        if not f.exists():
            self.send_error(404)
            return
        ctype = "application/pdf" if name.endswith(".pdf") else "image/jpeg"
        self._send_bytes(f.read_bytes(), ctype)

    def _api_inbox_update(self):
        data = self._read_json()
        if data is None:
            return
        if not self._check_pin(data.get("pin")):
            return
        item_id = data.get("id")
        action = data.get("action")
        items = load_inbox()
        item = next((x for x in items if x["id"] == item_id), None)
        if not item:
            self._json_error(404, "No existe")
            return
        if action == "processed":
            item["status"] = "processed"
        elif action == "delete":
            for ph in item.get("photos", []):
                try:
                    (PHOTOS_DIR / ph).unlink()
                except Exception:
                    pass
            items = [x for x in items if x["id"] != item_id]
        else:
            self._json_error(400, "Acción inválida")
            return
        save_inbox(items)
        self._send_json({"ok": True})

    # ── Serve local HTML ─────────────────────────────────────────────────
    def _serve_html(self):
        if not HTML_FILE.exists():
            self.send_error(404, "bonafide_facturas.html no encontrado")
            return
        self._send_bytes(HTML_FILE.read_bytes(), "text/html; charset=utf-8")

    def _send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(data))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj):
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    # ── Proxy to Nucleo API ──────────────────────────────────────────────
    def _proxy(self, method, nucleo_path, body):
        url = f"{NUCLEO_API}/{nucleo_path}"
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "Origin":       "https://prod.nucleocheck.com",
            "Referer":      "https://prod.nucleocheck.com/",
            "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
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

    # ── CORS ─────────────────────────────────────────────────────────────
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
        pass


if __name__ == "__main__":
    if not HTML_FILE.exists():
        print(f"\n❌ No se encontró: {HTML_FILE}")
        sys.exit(1)
    _ensure_dirs()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"✅ Bonafide Facturas escuchando en {HOST}:{PORT}  (datos en {DATA_DIR})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Servidor detenido.")
