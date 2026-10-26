#!/usr/bin/env python3
"""
Splice INSTRUMENT Linux Native Web GUI Server & IPC Bridge
- Serves static Vue 3 / Vite frontend
- Automatically detects and decrypts Splice session token from ~/.config/splice-instruments/ or Google Chrome
- Bridges globalThis.nativeBridge IPC messages (downloadPreset, downloadPack, loadPreset) to local backend
"""
import http.server
import socketserver
import os
import sys
import json
import sqlite3
import shutil
import tempfile
import hashlib
import subprocess

sys.path.insert(0, os.path.dirname(__file__))
try:
    import catalog
except ImportError:
    catalog = None

PORT = int(os.environ.get("SPLICE_PORT", 38241))
DIRECTORY = os.environ.get("SPLICE_FRONTEND_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend")))
CONFIG_DIR = os.path.expanduser("~/.config/splice-instruments")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token.txt")
IPC_LOG_FILE = os.path.expanduser("~/.local/share/splice-instruments/ipc.log")

def get_splice_token():
    """Extract or load active Splice authentication token."""
    # 1. Check cached token file
    if os.path.isfile(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            sys.stderr.write(f"[Server] Error reading {TOKEN_FILE}: {e}\n")

    # 2. Extract from Chrome Cookies
    chrome_cookie_path = os.path.expanduser("~/.config/google-chrome/Default/Cookies")
    if os.path.isfile(chrome_cookie_path):
        try:
            with tempfile.NamedTemporaryFile() as tmp:
                shutil.copy2(chrome_cookie_path, tmp.name)
                conn = sqlite3.connect(tmp.name)
                c = conn.cursor()
                c.execute("SELECT encrypted_value FROM cookies WHERE host_key LIKE '%splice.com%' AND name='_splice_token_prod'")
                row = c.fetchone()
                conn.close()

            if row and row[0]:
                enc_val = row[0]
                if enc_val.startswith(b"v11") and len(enc_val) > 19:
                    # Retrieve libsecret password or use known default
                    secret = None
                    for st_bin in ["/nix/store/87a0pwb7dzaiabw8gwqkcinkl8wnr6hr-libsecret-0.21.7/bin/secret-tool", "secret-tool"]:
                        try:
                            res = subprocess.run([st_bin, "search", "application", "chrome"], capture_output=True, text=True, timeout=2)
                            for line in res.stdout.splitlines():
                                if line.strip().startswith("secret = "):
                                    secret = line.split("=", 1)[1].strip().encode()
                                    break
                            if secret:
                                break
                        except Exception:
                            pass

                    if not secret:
                        secret = b"2IJjQvk9ZL3r9Xp5RTkNRQ=="

                    iv16 = enc_val[3:19].hex()
                    payload_cbc = enc_val[19:]
                    key16 = hashlib.pbkdf2_hmac('sha1', secret, b'saltysalt', 1, 16).hex()

                    openssl_bin = "/nix/store/ld12llamdxx5m5gi40jipihxshnypz6g-openssl-3.6.4-bin/bin/openssl"
                    if not os.path.exists(openssl_bin):
                        res = subprocess.run(["which", "openssl"], capture_output=True, text=True)
                        openssl_bin = res.stdout.strip() or "openssl"

                    proc = subprocess.Popen([openssl_bin, "enc", "-d", "-aes-128-cbc", "-K", key16, "-iv", iv16],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    out, _ = proc.communicate(payload_cbc)
                    if proc.returncode == 0 and len(out) > 16:
                        tok = out[16:].decode('utf-8', errors='ignore')
                        os.makedirs(CONFIG_DIR, exist_ok=True)
                        with open(TOKEN_FILE, "w") as f:
                            f.write(tok)
                        sys.stderr.write(f"[Server] Automatically detected and cached Splice token to {TOKEN_FILE}\n")
                        return tok
        except Exception as e:
            sys.stderr.write(f"[Server] Chrome cookie extraction notice: {e}\n")

    return ""


class SpliceBridgeHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        # Acknowledge splash screen
        if self.path.endswith("splash-painted"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"status":"acknowledged"}')
            return

        # Config / Token API
        if self.path == "/api/config" or self.path == "/api/token":
            token = get_splice_token()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            resp = {
                "authenticated": bool(token),
                "hasToken": bool(token),
                "tokenPreview": (token[:10] + "...") if token else ""
            }
            self.wfile.write(json.dumps(resp).encode())
            return

        # Live Catalog API
        if self.path == "/api/catalog":
            cat_data = catalog.get_catalog() if catalog else []
            encoded = json.dumps(cat_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)
            return

        # Serve index.html with injected token, catalog, and nativeBridge
        if self.path == "/" or self.path == "/index.html" or self.path.startswith("/?"):
            index_path = os.path.join(DIRECTORY, "index.html")
            if os.path.isfile(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    content = f.read()

                token = get_splice_token()
                cat_data = catalog.get_catalog() if catalog else []
                cat_json = json.dumps(cat_data)
                bridge_script = f"""
<script>
window.__SPLICE_AUTO_TOKEN__ = "{token}";
window.__SPLICE_CATALOG_DATA__ = {cat_json};
window.nativeBridge = {{
  postMessage: function(msg) {{
    console.log("[NativeBridge IPC Dispatch]", msg);
    try {{
      fetch("/api/ipc", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: typeof msg === "string" ? msg : JSON.stringify(msg)
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        console.log("[NativeBridge Response]", data);
      }}).catch(function(err) {{
        console.error("[NativeBridge Error]", err);
      }});
    }} catch(e) {{
      console.error("[NativeBridge Dispatch Exception]", e);
    }}
  }}
}};
</script>
"""
                injected = content.replace("</head>", bridge_script + "\n</head>", 1)
                encoded = injected.encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(encoded)
                return

        return super().do_GET()

    def do_POST(self):
        # IPC Bridge endpoint
        if self.path == "/api/ipc":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {"raw": body}

            event = data.get("event", "unknown")
            sys.stderr.write(f"[Server IPC] Event received: {event} | Payload: {json.dumps(data)}\n")

            # Log to IPC log file
            try:
                os.makedirs(os.path.dirname(IPC_LOG_FILE), exist_ok=True)
                with open(IPC_LOG_FILE, "a") as f:
                    f.write(json.dumps(data) + "\n")
            except Exception:
                pass

            response_data = {"status": "ok", "event": event, "handled": True}
            if "download" in str(event).lower():
                response_data["message"] = f"Download request for {event} accepted."

            encoded = json.dumps(response_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)
            return

        # Token update endpoint
        if self.path == "/api/token":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
                token = data.get("token", "").strip()
                if token:
                    os.makedirs(CONFIG_DIR, exist_ok=True)
                    with open(TOKEN_FILE, "w") as f:
                        f.write(token)
                    encoded = json.dumps({"status": "ok", "saved": True}).encode("utf-8")
                else:
                    encoded = json.dumps({"status": "error", "message": "Empty token"}).encode("utf-8")
            except Exception as e:
                encoded = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)
            return

        self.send_response(404)
        self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    active_token = get_splice_token()
    status_str = f"Authenticated (token prefix: {active_token[:10]}...)" if active_token else "Offline / Unauthenticated"
    sys.stderr.write(f"[Server] Splice Account Status: {status_str}\n")
    with socketserver.TCPServer(("127.0.0.1", PORT), SpliceBridgeHandler) as httpd:
        print(f"Serving {DIRECTORY} at http://127.0.0.1:{PORT}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
