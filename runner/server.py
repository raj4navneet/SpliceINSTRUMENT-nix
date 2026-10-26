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

import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
try:
    import catalog
except ImportError:
    catalog = None

try:
    import downloader
except ImportError:
    downloader = None

PORT = int(os.environ.get("SPLICE_PORT", 38241))
DIRECTORY = os.environ.get("SPLICE_FRONTEND_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend")))
CONFIG_DIR = os.path.expanduser("~/.config/splice-instruments")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token.txt")
IPC_LOG_FILE = os.path.expanduser("~/.local/share/splice-instruments/ipc.log")

def get_splice_token():
    """Extract or load active Splice authentication token."""
    # 0. Check Auth0 native token in Wine prefix cache
    wine_token_file = os.path.expanduser("~/.local/share/splice-instruments/drive_c/users/navigator/AppData/Roaming/com.splice.INSTRUMENT/cache/spl_a0_at")
    if os.path.isfile(wine_token_file):
        try:
            with open(wine_token_file, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            sys.stderr.write(f"[Server] Error reading wine token: {e}\n")

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

        # Download status endpoint
        if self.path.startswith("/api/download/status"):
            task_key = ""
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for param in query.split("&"):
                    if param.startswith("task="):
                        task_key = urllib.parse.unquote(param.split("=", 1)[1])
            st = downloader.get_download_status(task_key) if downloader else {"status": "not_available"}
            encoded = json.dumps(st).encode("utf-8")
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
    var parsed = msg;
    if (typeof msg === "string") {{
      try {{ parsed = JSON.parse(msg); }} catch(e) {{}}
    }}

    var eventName = parsed && (parsed.event || "");

    // HTML5 Audio Preview Playback & Progress Synchronization
    if (eventName === "playPreview" || eventName === "play") {{
      var pUrl = parsed.url;
      var pId = parsed.contentId || parsed.id || "";
      if (pUrl) {{
        if (!window.__splice_preview_audio) {{
          window.__splice_preview_audio = new Audio();
        }}
        var aud = window.__splice_preview_audio;
        aud.pause();
        aud.src = pUrl;
        if (parsed.startOffset) aud.currentTime = parsed.startOffset;
        aud.play().catch(function(e) {{ console.warn("[HTML5 Audio Play Error]", e); }});

        var updatePreviewTree = function(st, prog) {{
          try {{
            var rt = globalThis.getValueTreeRoot && globalThis.getValueTreeRoot("root");
            var cp = rt && rt.getValueTree().getChildWithName("CurrentPreview");
            if (cp) {{
              cp.setProperty("state", st);
              if (pId) cp.setProperty("uuid", pId);
              if (prog !== undefined) cp.setProperty("progress", prog);
            }}
          }} catch(e) {{}}
        }};

        updatePreviewTree(0, 0.0); // 0 = Play

        aud.ontimeupdate = function() {{
          if (aud.duration) {{
            updatePreviewTree(0, aud.currentTime / aud.duration);
          }}
        }};
        aud.onended = function() {{
          updatePreviewTree(1, 1.0); // 1 = Stop
        }};
      }}
    }} else if (eventName === "stopPreview" || eventName === "stop") {{
      if (window.__splice_preview_audio) {{
        window.__splice_preview_audio.pause();
        window.__splice_preview_audio.currentTime = 0;
      }}
      try {{
        var rt = globalThis.getValueTreeRoot && globalThis.getValueTreeRoot("root");
        var cp = rt && rt.getValueTree().getChildWithName("CurrentPreview");
        if (cp) {{
          cp.setProperty("state", 1); // 1 = Stop
          cp.setProperty("progress", 0.0);
        }}
      }} catch(e) {{}}
    }} else if (eventName === "pausePreview" || eventName === "pause") {{
      if (window.__splice_preview_audio) {{
        window.__splice_preview_audio.pause();
      }}
      try {{
        var rt = globalThis.getValueTreeRoot && globalThis.getValueTreeRoot("root");
        var cp = rt && rt.getValueTree().getChildWithName("CurrentPreview");
        if (cp) cp.setProperty("state", 2); // 2 = Pause
      }} catch(e) {{}}
    }} else if (eventName === "resumePreview" || eventName === "resume") {{
      if (window.__splice_preview_audio) {{
        window.__splice_preview_audio.play().catch(function(){{}});
      }}
      try {{
        var rt = globalThis.getValueTreeRoot && globalThis.getValueTreeRoot("root");
        var cp = rt && rt.getValueTree().getChildWithName("CurrentPreview");
        if (cp) cp.setProperty("state", 0); // 0 = Play
      }} catch(e) {{}}
    }}

    // WebAudio Polyphonic Synthesizer for Virtual Keyboard
    if (eventName === "triggerKey" || (parsed && parsed.midiNote !== undefined)) {{
      var note = parsed.midiNote !== undefined ? parsed.midiNote : parsed.note;
      var isPress = parsed.press === true || parsed.pressed === true || parsed.isPressed === true;
      if (note !== undefined) {{
        try {{
          if (!window.__splice_audio_ctx) {{
            var AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (AudioCtx) {{
              window.__splice_audio_ctx = new AudioCtx();
            }}
          }}
          var ctx = window.__splice_audio_ctx;
          if (ctx) {{
            if (ctx.state === "suspended") {{
              ctx.resume().catch(function(){{}});
            }}
            window.__splice_voices = window.__splice_voices || {{}};
            if (isPress) {{
              // Note On
              if (window.__splice_voices[note]) {{
                try {{
                  window.__splice_voices[note].gain.gain.cancelScheduledValues(ctx.currentTime);
                  window.__splice_voices[note].osc1.stop();
                  window.__splice_voices[note].osc2.stop();
                }} catch(e) {{}}
              }}
              var freq = 440 * Math.pow(2, (note - 69) / 12);
              var vel = parsed.velocity !== undefined ? (parsed.velocity / 127) : 0.8;
              var gainNode = ctx.createGain();
              gainNode.gain.setValueAtTime(0.0001, ctx.currentTime);
              gainNode.gain.linearRampToValueAtTime(vel * 0.35, ctx.currentTime + 0.015);
              gainNode.gain.exponentialRampToValueAtTime(vel * 0.2, ctx.currentTime + 0.25);

              var filter = ctx.createBiquadFilter();
              filter.type = "lowpass";
              filter.frequency.setValueAtTime(Math.min(freq * 3.5, 5500), ctx.currentTime);

              var osc1 = ctx.createOscillator();
              osc1.type = "sawtooth";
              osc1.frequency.setValueAtTime(freq, ctx.currentTime);

              var osc2 = ctx.createOscillator();
              osc2.type = "triangle";
              osc2.frequency.setValueAtTime(freq * 1.002, ctx.currentTime);

              osc1.connect(filter);
              osc2.connect(filter);
              filter.connect(gainNode);
              gainNode.connect(ctx.destination);

              osc1.start();
              osc2.start();

              window.__splice_voices[note] = {{ osc1: osc1, osc2: osc2, gain: gainNode }};
            }} else {{
              // Note Off
              var voice = window.__splice_voices[note];
              if (voice) {{
                var curGain = Math.max(voice.gain.gain.value, 0.0001);
                voice.gain.gain.cancelScheduledValues(ctx.currentTime);
                voice.gain.gain.setValueAtTime(curGain, ctx.currentTime);
                voice.gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.2);
                setTimeout(function() {{
                  try {{
                    voice.osc1.stop();
                    voice.osc2.stop();
                  }} catch(e) {{}}
                }}, 220);
                delete window.__splice_voices[note];
              }}
            }}
          }}
        }} catch(e) {{
          console.warn("[WebAudio TriggerKey Exception]", e);
        }}
      }}
    }}

    // Load Preset Bridge Synchronization
    if (eventName === "loadPreset" || eventName === "loadPresetUuid") {{
      try {{
        var rt = globalThis.getValueTreeRoot && globalThis.getValueTreeRoot("root");
        var curPre = rt && rt.getValueTree().getChildWithName("CurrentPreset");
        if (curPre) {{
          if (parsed.familyName || parsed.family) curPre.setProperty("family", parsed.familyName || parsed.family);
          if (parsed.uniqueName || parsed.name) curPre.setProperty("name", parsed.uniqueName || parsed.name);
          if (parsed.displayName) curPre.setProperty("displayName", parsed.displayName);
        }}
      }} catch(e) {{}}
    }}

    // Server IPC Notification & Download Polling
    try {{
      fetch("/api/ipc", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: typeof msg === "string" ? msg : JSON.stringify(msg)
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        console.log("[NativeBridge Response]", data);
        if (data.soundContentId && data.libraryContentId && data.installState) {{
          if (window.__SPLICE_SET_PRESET_INSTALL_STATE__) {{
            window.__SPLICE_SET_PRESET_INSTALL_STATE__(data.soundContentId, data.libraryContentId, data.installState);
          }}
          if (data.installState === 2) {{
            var taskKey = data.libraryContentId + ":" + data.soundContentId;
            var pollCount = 0;
            var pollTimer = setInterval(function() {{
              pollCount++;
              if (pollCount > 300) {{ clearInterval(pollTimer); return; }}
              fetch("/api/download/status?task=" + encodeURIComponent(taskKey))
                .then(function(res) {{ return res.json(); }})
                .then(function(st) {{
                  if (st.status === "completed") {{
                    clearInterval(pollTimer);
                    if (window.__SPLICE_SET_PRESET_INSTALL_STATE__) {{
                      window.__SPLICE_SET_PRESET_INSTALL_STATE__(data.soundContentId, data.libraryContentId, 3);
                    }}
                  }} else if (st.status === "failed") {{
                    clearInterval(pollTimer);
                    if (window.__SPLICE_SET_PRESET_INSTALL_STATE__) {{
                      window.__SPLICE_SET_PRESET_INSTALL_STATE__(data.soundContentId, data.libraryContentId, 4);
                    }}
                  }}
                }}).catch(function() {{}});
            }}, 1000);
          }}
        }} else if (data.libraryContentId && data.event && data.event.indexOf("downloadPack") !== -1) {{
          var taskKey = data.libraryContentId + ":all";
          var pollCount = 0;
          var pollTimer = setInterval(function() {{
            pollCount++;
            if (pollCount > 600) {{ clearInterval(pollTimer); return; }}
            fetch("/api/download/status?task=" + encodeURIComponent(taskKey))
              .then(function(res) {{ return res.json(); }})
              .then(function(st) {{
                if (st.status === "completed") {{
                  clearInterval(pollTimer);
                  location.reload();
                }} else if (st.status === "failed") {{
                  clearInterval(pollTimer);
                }}
              }}).catch(function() {{}});
          }}, 1000);
        }}
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

            # Handle downloadPreset
            if event == "downloadPreset" or "downloadPreset" in str(event):
                sound_id = data.get("soundContentId")
                lib_id = data.get("libraryContentId")
                if sound_id and lib_id and downloader:
                    tok = get_splice_token()
                    downloader.start_download_async(sound_id, lib_id, token=tok)
                    response_data = {
                        "status": "started",
                        "event": event,
                        "soundContentId": sound_id,
                        "libraryContentId": lib_id,
                        "installState": 2, # Downloading
                        "message": f"Download initiated for {sound_id}"
                    }

            # Handle downloadPack
            if event == "downloadPack" or "downloadPack" in str(event):
                lib_id = data.get("libraryContentId") or data.get("packId") or data.get("id")
                if lib_id and downloader:
                    tok = get_splice_token()
                    downloader.start_pack_download_async(lib_id, token=tok)
                    response_data = {
                        "status": "started",
                        "event": event,
                        "libraryContentId": lib_id,
                        "installState": 2, # Downloading
                        "message": f"Pack download initiated for {lib_id}"
                    }

            # Handle triggerKey
            if event == "triggerKey":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return

            # Handle loadPreset
            if "loadpreset" in str(event).lower():
                response_data = {
                    "status": "ok",
                    "event": event,
                    "loaded": True,
                    "preset": data
                }

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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
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
