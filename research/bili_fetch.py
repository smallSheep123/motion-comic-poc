# -*- coding: utf-8 -*-
"""下载 B 站漫画解说视频的低清流到本地，用于分镜手法学习（仅供研究）。"""
import json
import subprocess
import sys
import urllib.request

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def get_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def download(bvid: str, out: str, qn: int = 32) -> None:
    view = get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    cid = view["data"]["cid"]
    title = view["data"]["title"]
    dur = view["data"]["duration"]
    print(f"title: {title}\nduration: {dur}s cid: {cid}")
    pu = get_json(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn={qn}&fnval=0&platform=html5"
    )
    d = pu["data"]["durl"][0]
    size_mb = d["size"] / 1e6
    print(f"stream: {d['url'][:80]}... size={size_mb:.0f}MB")
    req = urllib.request.Request(d["url"], headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if done % (20 << 20) < (1 << 20):
                print(f"  {done/1e6:.0f}MB", flush=True)
    print("saved:", out)


if __name__ == "__main__":
    download(sys.argv[1], sys.argv[2])
