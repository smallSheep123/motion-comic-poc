# -*- coding: utf-8 -*-
"""阶段② 时间轴对齐编辑器：语音块波形 + 句子时间戳微调 + 图文对照。

前置（离线管线，不在 UI 内）：阶段① 拆封 → 视觉模型生成解说词 → TTS 配音
→ cut_and_render 生成 director.json（每镜文本/格子框/音频路径）。

用法：python gui/timeline_editor.py --work real_manga2 [--port 8766]
微调保存直接写回各镜 wav（含停留时长静音），随后运行 compile+render 出片。
"""
import argparse
import base64
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "timeline.html")
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

    def do_GET(self):
        work = ARGS.work
        if self.path.startswith("/api/data"):
            d = load_json(os.path.join(work, "director.json"), {"shots": []})
            shots = []
            for sh in d.get("shots", []):
                n = sh.get("narration") or {}
                if not n.get("text"):
                    continue
                shots.append({
                    "id": sh["id"], "page": os.path.basename(sh["page"]),
                    "bbox": (sh.get("focus") or {}).get("bbox") or n.get("subject"),
                    "text": n["text"],
                    "audio": (n.get("audio") or "").replace("\\", "/"),
                })
            pages = sorted({s["page"] for s in shots})
            self._send(200, json.dumps({"shots": shots, "pages": pages},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/audio?p="):
            from urllib.parse import unquote, parse_qs, urlparse
            p = parse_qs(urlparse(self.path).query).get("p", [""])[0]
            root = os.path.abspath(work)
            ap = os.path.abspath(os.path.join(root, p))
            if not ap.startswith(root) or not os.path.exists(ap):
                self._send(404, b"not found", "text/plain")
                return
            with open(ap, "rb") as f:
                self._send(200, f.read(), "audio/wav")
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
        if self.path == "/api/save_audio":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            audio = payload.get("audio", "").replace("\\", "/")
            root = os.path.abspath(ARGS.work)
            ap = os.path.abspath(os.path.join(root, audio))
            if not ap.startswith(root) or not ap.endswith(".wav"):
                self._send(400, b"bad path", "text/plain")
                return
            raw = base64.b64decode(payload["wav_b64"])
            with open(ap, "wb") as f:
                f.write(raw)
            if payload.get("pad_zero"):  # 停留时长已写成静音，编译期不再外加
                d = load_json(os.path.join(root, "director.json"), None)
                if d:
                    for sh in d["shots"]:
                        if sh.get("narration", {}).get("audio", "").replace("\\", "/") == audio:
                            sh["narration"]["pad_in"] = 0
                            sh["narration"]["pad_out"] = 0
                    with open(os.path.join(root, "director.json"), "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
            self._send(200, json.dumps({"ok": True, "bytes": len(raw)}).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--port", type=int, default=8766)
    ARGS = ap.parse_args()
    globals()["ARGS"] = ARGS
    url = f"http://127.0.0.1:{ARGS.port}/"
    print(f"阶段② 时间轴对齐: {url}  (工作目录: {ARGS.work})  Ctrl+C 退出")
    webbrowser.open(url)
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
