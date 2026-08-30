# -*- coding: utf-8 -*-
"""阶段②：非破坏式画面/配音同步工作台。

用法：python gui/timeline_editor.py --work real_manga2 [--port 8766]

编辑结果保存到 ``timeline_alignment.json``，同时把可编译字段写回
``director.json``。原始 TTS 文件不会被裁切或覆盖。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from motion_comic.alignment import (  # noqa: E402
    ALIGNMENT_VERSION,
    apply_page_timeline,
)

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "timeline.html")
ARGS = None
SAVE_LOCK = threading.Lock()
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    return default


def write_json_atomic(path: str, value: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def asset_path(work: str, value: str, must_exist: bool = True) -> str:
    """Resolve a user-visible work asset while preventing directory escape."""
    if not value:
        raise ValueError("资源路径为空")
    value = value.replace("\\", os.sep).replace("/", os.sep)
    root = os.path.realpath(work)
    candidate = os.path.realpath(value if os.path.isabs(value) else os.path.join(root, value))
    try:
        inside = os.path.commonpath([root, candidate]) == root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("资源必须位于工作目录内")
    if must_exist and not os.path.isfile(candidate):
        raise FileNotFoundError(value)
    return candidate


def portable_path(work: str, path: str) -> str:
    return os.path.relpath(path, os.path.realpath(work)).replace(os.sep, "/")


def existing_audio(work: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return asset_path(work, value)
    except (ValueError, FileNotFoundError):
        return None


def list_audio(work: str) -> list[str]:
    folder = os.path.join(work, "audio")
    result = []
    if os.path.isdir(folder):
        for base, _, files in os.walk(folder):
            for name in files:
                if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                    result.append(portable_path(work, os.path.join(base, name)))
    return sorted(result)


def suggested_source(work: str, page: str, shots: list[dict], saved: dict | None,
                     candidates: list[str]) -> dict:
    if saved:
        path = existing_audio(work, saved.get("audio"))
        if path:
            return {"kind": "file", "path": portable_path(work, path)}

    stem = os.path.splitext(os.path.basename(page))[0].lower()
    direct = [p for p in candidates if os.path.splitext(os.path.basename(p))[0].lower() in
              {stem, f"page_{stem}", f"{stem}_full", f"{stem}_page"}]
    if direct:
        return {"kind": "file", "path": direct[0]}

    parts = []
    for shot in shots:
        path = existing_audio(work, (shot.get("narration") or {}).get("audio"))
        if path:
            rel = portable_path(work, path)
            if not parts or parts[-1] != rel:
                parts.append(rel)
    if len(parts) == 1:
        return {"kind": "file", "path": parts[0]}
    if parts:
        return {"kind": "parts", "parts": parts}
    return {"kind": "missing", "parts": []}


def build_editor_data(work: str) -> dict:
    director = load_json(os.path.join(work, "director.json"), {"shots": []})
    alignment = load_json(os.path.join(work, "timeline_alignment.json"),
                          {"version": ALIGNMENT_VERSION, "pages": {}})
    candidates = list_audio(work)
    pages: dict[str, dict] = {}
    source_shots: dict[str, list[dict]] = {}
    for shot in director.get("shots", []):
        page = os.path.basename(shot.get("page", ""))
        if not page:
            continue
        source_shots.setdefault(page, []).append(shot)
        page_data = pages.setdefault(page, {"page": page, "shots": []})
        narration = shot.get("narration") or {}
        page_data["shots"].append({
            "id": shot.get("id"),
            "bbox": (shot.get("focus") or {}).get("bbox") or narration.get("subject"),
            "text": narration.get("text", ""),
            "transition_out": shot.get("transition_out", "CUT"),
            "transition_duration": shot.get("transition_sec", director.get("crossfade_sec", 0.6)),
            "gap_after": shot.get("gap_after", 0.0),
        })
    result_pages = []
    for page_data in pages.values():
        saved = (alignment.get("pages") or {}).get(page_data["page"])
        page_data["source"] = suggested_source(work, page_data["page"],
                                                source_shots[page_data["page"]], saved, candidates)
        page_data["alignment"] = saved
        result_pages.append(page_data)
    return {
        "pages": result_pages,
        "audio_files": candidates,
        "transition_types": ["CUT", "CROSSFADE", "FADE_BLACK", "FADE_WHITE"],
    }


def materialize_source(work: str, page: str, source: dict) -> str:
    kind = source.get("kind")
    if kind == "file":
        return portable_path(work, asset_path(work, source.get("path", "")))
    if kind != "parts":
        raise ValueError("请先为本页选择或生成配音文件")
    parts = [asset_path(work, path) for path in source.get("parts") or []]
    if not parts:
        raise ValueError("本页没有可用音频")
    if len(parts) == 1:
        return portable_path(work, parts[0])
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(page)[0])
    out_dir = os.path.join(work, "audio", "_timeline_pages")
    os.makedirs(out_dir, exist_ok=True)
    output = os.path.join(out_dir, f"{safe_stem}.wav")
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in parts:
        command += ["-i", path]
    inputs = "".join(f"[{i}:a]" for i in range(len(parts)))
    command += ["-filter_complex", f"{inputs}concat=n={len(parts)}:v=0:a=1[out]",
                "-map", "[out]", "-ac", "1", "-ar", "44100", output]
    subprocess.run(command, check=True)
    return portable_path(work, output)


def save_alignment(work: str, payload: dict) -> dict:
    page = os.path.basename(payload.get("page", ""))
    if not page:
        raise ValueError("缺少页面名称")
    with SAVE_LOCK:
        director_path = os.path.join(work, "director.json")
        director = load_json(director_path, None)
        if not director:
            raise ValueError("未找到 director.json")
        audio = materialize_source(work, page, payload.get("source") or {})
        audio_duration = float(payload.get("audio_duration", 0))
        updated, audio_clips, visual_clips = apply_page_timeline(
            director, page, audio,
            payload.get("audio_clips") or [], payload.get("visual_clips") or [],
            audio_duration=audio_duration)
        alignment_path = os.path.join(work, "timeline_alignment.json")
        alignment = load_json(alignment_path, {"version": ALIGNMENT_VERSION, "pages": {}})
        alignment["version"] = ALIGNMENT_VERSION
        alignment.setdefault("pages", {})[page] = {
            "audio": audio,
            "audio_duration": round(audio_duration, 3),
            "audio_clips": audio_clips,
            "visual_clips": visual_clips,
        }
        write_json_atomic(director_path, updated)
        write_json_atomic(alignment_path, alignment)
    return {"ok": True, "page": page, "audio": audio,
            "audio_clips": len(audio_clips), "visual_clips": len(visual_clips)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code: int, value: dict) -> None:
        self.send_bytes(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def do_GET(self):
        work = os.path.realpath(ARGS.work)
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/data":
                self.send_json(200, build_editor_data(work))
            elif parsed.path == "/media/audio":
                path = asset_path(work, parse_qs(parsed.query).get("path", [""])[0])
                with open(path, "rb") as file:
                    self.send_bytes(200, file.read(), mimetypes.guess_type(path)[0] or "audio/wav")
            elif parsed.path == "/media/image":
                name = os.path.basename(parse_qs(parsed.query).get("page", [""])[0])
                path = asset_path(work, os.path.join("pages", name))
                with open(path, "rb") as file:
                    self.send_bytes(200, file.read(), mimetypes.guess_type(path)[0] or "image/jpeg")
            elif parsed.path in ("/", "/index.html"):
                with open(HTML, "rb") as file:
                    self.send_bytes(200, file.read(), "text/html; charset=utf-8")
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except (ValueError, FileNotFoundError) as exc:
            self.send_json(404, {"ok": False, "error": str(exc)})

    def do_POST(self):
        if urlparse(self.path).path != "/api/alignment":
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.send_json(200, save_alignment(os.path.realpath(ARGS.work), payload))
        except (ValueError, FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    args.work = os.path.realpath(args.work)
    if not os.path.isfile(os.path.join(args.work, "director.json")):
        raise SystemExit("未找到 director.json——请先完成切图与配音准备")
    globals()["ARGS"] = args
    url = f"http://127.0.0.1:{args.port}/"
    print(f"阶段② 画面/配音同步工作台: {url}  (工作目录: {args.work})  Ctrl+C 退出")
    if not args.no_browser:
        webbrowser.open(url)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
