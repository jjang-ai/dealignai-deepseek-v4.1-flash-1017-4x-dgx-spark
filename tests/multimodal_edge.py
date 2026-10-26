#!/usr/bin/env python3
"""Multimodal edge cases: odd and large inputs must get an answer or a clean 4xx, never a dead engine.

    python3 tests/multimodal_edge.py [--url http://10.10.100.1:8888] [--api-key KEY]

Needs ffmpeg (for JPEG/WebP/video frames). Exit code = number of failures.
"""
import argparse, base64, json, os, subprocess, sys, tempfile, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=os.environ.get("DSV41_URL", "http://10.10.100.1:8888"))
ap.add_argument("--api-key", default=os.environ.get("DSV41_API_KEY", ""))
ap.add_argument("--model", default="DeepSeek-V4.1-Flash-1017-UNCENSORED")
A = ap.parse_args()
H = {"Content-Type": "application/json", **({"Authorization": "Bearer " + A.api_key} if A.api_key else {})}
TD = tempfile.mkdtemp()


def ff(args, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, out], check=True)
    return open(out, "rb").read()


def uri(data, mime):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def ask(images, text, max_tokens=40):
    content = [{"type": "image_url", "image_url": {"url": u}} for u in images] + [{"type": "text", "text": text}]
    body = {"model": A.model, "max_tokens": max_tokens, "temperature": 0, "messages": [{"role": "user", "content": content}],
            "chat_template_kwargs": {"thinking": False}}
    req = urllib.request.Request(A.url + "/v1/chat/completions", json.dumps(body).encode(), H)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:160]


def alive():
    req = urllib.request.Request(A.url + "/v1/loads", headers=H)
    try:
        return urllib.request.urlopen(req, timeout=60).status == 200
    except Exception:
        return False


red4k = ff(["-f", "lavfi", "-i", "color=red:s=3840x2160:d=1", "-frames:v", "1"], f"{TD}/4k.png")
blue_jpg = ff(["-f", "lavfi", "-i", "color=blue:s=640x480:d=1", "-frames:v", "1"], f"{TD}/b.jpg")
green_webp = ff(["-f", "lavfi", "-i", "color=green:s=640x480:d=1", "-frames:v", "1"], f"{TD}/g.webp")
tiny = ff(["-f", "lavfi", "-i", "color=yellow:s=8x8:d=1", "-frames:v", "1"], f"{TD}/t.png")
alpha = ff(["-f", "lavfi", "-i", "color=red@0.5:s=256x256:d=1,format=rgba", "-frames:v", "1"], f"{TD}/a.png")
pano = ff(["-f", "lavfi", "-i", "color=blue:s=4000x300:d=1", "-frames:v", "1"], f"{TD}/p.png")
frames = [uri(ff(["-f", "lavfi", "-i", f"color={c}:s=640x360:d=1", "-frames:v", "1"], f"{TD}/f{i}.jpg"), "image/jpeg")
          for i, c in enumerate(["red", "green", "blue", "yellow"] * 8)]

CASES = [
    ("4K PNG (3840x2160)", lambda: ask([uri(red4k, "image/png")], "What colour is this image? One word."), "red"),
    ("JPEG", lambda: ask([uri(blue_jpg, "image/jpeg")], "What colour is this image? One word."), "blue"),
    ("WebP", lambda: ask([uri(green_webp, "image/webp")], "What colour is this image? One word."), "green"),
    ("tiny 8x8 PNG", lambda: ask([uri(tiny, "image/png")], "What colour is this image? One word."), "yellow"),
    ("PNG with alpha", lambda: ask([uri(alpha, "image/png")], "What colour is this image? One word."), "red"),
    ("panorama 4000x300", lambda: ask([uri(pano, "image/png")], "What colour is this image? One word."), "blue"),
    ("32 frames in one request", lambda: ask(frames, "How many images did you get, and what colour is the last one? Answer like: 32, yellow", 30), "yellow"),
    ("corrupt image data -> 4xx", lambda: ask([uri(b"\x89PNG\r\n\x1a\nnot really a png", "image/png")], "Colour?"), None),
    ("text as image -> 4xx", lambda: ask(["data:image/png;base64," + base64.b64encode(b"hello").decode()], "Colour?"), None),
]
fails = 0
for name, fn, want in CASES:
    t = time.time(); st, out = fn(); dt = time.time() - t
    ok = (st == 200 and want in out.lower()) if want else (400 <= st < 500)
    ok = ok and alive()
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name:28s} {dt:5.1f}s  {st} {out[:90]!r}")
print(f"\n{len(CASES) - fails}/{len(CASES)} passed; engine alive: {alive()}")
sys.exit(fails)
