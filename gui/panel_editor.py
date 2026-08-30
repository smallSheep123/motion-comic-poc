# -*- coding: utf-8 -*-
"""阶段① 格子切割编辑器：AI 预标 + 人工微调。

用法：python gui/panel_editor.py --work real_manga2 [--port 8765]
产物：panels_final.json（切割框 + 每格解说词）
之后的解说词生成 / TTS / 时间轴对齐由离线管线与阶段②服务处理。
"""
import argparse
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "editor.html")
ARGS = None


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        work = ARGS.work
        if self.path.startswith("/api/pages"):
            pages = sorted(f for f in os.listdir(os.path.join(work, "pages"))
                           if f.lower().endswith((".jpg", ".png", ".webp")))
            ai = load_json(os.path.join(work, "panels_vlm.json"), {})
            final_path = os.path.join(work, "panels_final.json")
            final = load_json(final_path, None)
            data = []
            for pg in pages:
                entry = {"page": pg}
                if final and pg in final:
                    entry["panels"] = final[pg]
                    entry["source"] = "final"
                else:
                    entry["panels"] = [{"bbox": b, "text": ""} for b in ai.get(pg, [])]
                    entry["source"] = "ai" if ai.get(pg) else "empty"
                data.append(entry)
            self._json({"pages": data})
        elif self.path.startswith("/image/"):
            name = os.path.basename(self.path.split("?", 1)[0][len("/image/"):])
            path = os.path.join(work, "pages", name)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    ctype = "image/png" if name.endswith(".png") else "image/jpeg"
                    self._send(200, f.read(), ctype)
            else:
                self._send(404, b"not found", "text/plain")
        elif self.path in ("/", "/index.html"):
            with open(HTML, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/save":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            page = os.path.basename(payload["page"])
            panels = [{"bbox": [round(float(v), 4) for v in p["bbox"]],
                       "text": str(p.get("text", ""))} for p in payload["panels"]]
            final_path = os.path.join(ARGS.work, "panels_final.json")
            final = load_json(final_path, {})
            final[page] = panels
            with open(final_path, "w", encoding="utf-8") as f:
                json.dump(final, f, ensure_ascii=False, indent=1)
            self._json({"ok": True, "count": len(panels)})
        else:
            self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="作品目录（含 pages/）")
    ap.add_argument("--port", type=int, default=8765)
    ARGS = ap.parse_args()
    globals()["ARGS"] = ARGS

    url = f"http://127.0.0.1:{ARGS.port}/"
    print(f"阶段① 格子切割: {url}  (工作目录: {ARGS.work})  Ctrl+C 退出")
    webbrowser.open(url)
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
