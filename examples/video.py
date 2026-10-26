#!/usr/bin/env python3
"""Ask about a video: cut evenly spaced frames with ffmpeg and send them as images in one request.

    python3 examples/video.py clip.mp4 "What happens in this video?" [--frames 8] [--size 768] [--url ...] [--api-key ...]

The model reads images, not video streams, so the clip is sampled client-side: N frames spread over its duration,
scaled so the longer side is at most --size pixels (fewer image tokens, same answer quality for most questions),
each labelled with its timestamp. 8 frames at 768 px is a good default; up to ~32 frames work in one request.
Needs ffmpeg/ffprobe on the machine running this script.
"""
import argparse, base64, json, os, subprocess, sys, tempfile, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("question", nargs="?", default="Describe what happens in this video, in order.")
ap.add_argument("--frames", type=int, default=8)
ap.add_argument("--size", type=int, default=768, help="max width/height of each frame in pixels")
ap.add_argument("--url", default=os.environ.get("DSV41_URL", "http://10.10.100.1:8888"))
ap.add_argument("--api-key", default=os.environ.get("DSV41_API_KEY", ""))
ap.add_argument("--model", default="DeepSeek-V4.1-Flash-1017-UNCENSORED")
ap.add_argument("--max-tokens", type=int, default=600)
A = ap.parse_args()

dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", A.video],
                           capture_output=True, text=True, check=True).stdout.strip())
content = [{"type": "text", "text": f"These are {A.frames} frames from a {dur:.1f}-second video, in order, each labelled with its time."}]
with tempfile.TemporaryDirectory() as td:
    for i in range(A.frames):
        t = dur * (i + 0.5) / A.frames                     # middle of each slice: avoids black first/last frames
        out = os.path.join(td, f"{i:03d}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", A.video, "-frames:v", "1",
                        "-vf", f"scale='min({A.size},iw)':'min({A.size},ih)':force_original_aspect_ratio=decrease",
                        "-q:v", "3", out], check=True)
        b64 = base64.b64encode(open(out, "rb").read()).decode()
        content += [{"type": "text", "text": f"t={t:.1f}s"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]
content.append({"type": "text", "text": A.question})

body = {"model": A.model, "max_tokens": A.max_tokens, "temperature": 0, "messages": [{"role": "user", "content": content}],
        "chat_template_kwargs": {"thinking": False}}
h = {"Content-Type": "application/json"}
if A.api_key:
    h["Authorization"] = "Bearer " + A.api_key
req = urllib.request.Request(A.url + "/v1/chat/completions", json.dumps(body).encode(), h)
try:
    r = json.load(urllib.request.urlopen(req, timeout=600))
except urllib.error.HTTPError as e:
    sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")
print(r["choices"][0]["message"]["content"])
u = r.get("usage", {})
print(f"\n[{A.frames} frames, prompt {u.get('prompt_tokens')} tokens, answer {u.get('completion_tokens')} tokens]", file=sys.stderr)
