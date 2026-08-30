# -*- coding: utf-8 -*-
"""下载 pixiv 作品页面（用法：python download.py [work_id] [out_dir]）。"""
import json
import os
import sys
import urllib.request

WORK_ID = sys.argv[1] if len(sys.argv) > 1 else "148948528"
OUT = sys.argv[2] if len(sys.argv) > 2 else "real_manga/pages"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.pixiv.net/"}


def fetch(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else json.loads(data)


def main():
    meta = fetch(f"https://www.pixiv.net/ajax/illust/{WORK_ID}/pages")
    pages = meta["body"]
    print(f"total pages: {len(pages)}")
    manifest = []
    for i, p in enumerate(pages):
        dst = f"{OUT}/p{i:02d}.jpg"
        data = None
        tried = []
        for key in ("original", "regular"):
            url = p["urls"][key]
            tried.append(key)
            try:
                data = fetch(url, binary=True)
                break
            except Exception as e:
                print(f"  p{i:02d} {key} failed: {e}")
        if data is None:
            sys.exit(f"p{i:02d} download failed: {tried}")
        with open(dst, "wb") as f:
            f.write(data)
        manifest.append({"file": dst, "w": p["width"], "h": p["height"],
                         "source": tried[0] if data else "?"})
        print(f"  p{i:02d} <- {len(data)//1024}KB ({p['width']}x{p['height']}, {tried[0]})")
    with open(f"{OUT}/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("done:", len(manifest), "pages")


if __name__ == "__main__":
    main()
