#!/usr/bin/env python3
"""edge_proxy.py — keep long requests alive through proxies with a first-byte timeout (Cloudflare: 100 s -> 524).

A cold prefill of a long prompt (a 1M-token prompt takes ~190 s) produces no response byte until it finishes,
streaming or not, so a proxy that waits at most ~100 s for the first byte (Cloudflare's free/pro plans, many load
balancers) cuts it. This proxy sits between such a proxy and the engine:

- responses that start within GRACE seconds (default 20) pass through untouched: status, headers and body;
- otherwise it answers 200 itself and sends a keep-alive every PING seconds (15) until the engine responds:
  an SSE comment (": keep-alive") for streaming requests, which SSE clients ignore, or a space for JSON requests,
  which JSON parsers ignore before the object; then the engine's body follows unchanged. An engine error that
  arrives after the keep-alives started is delivered as the body (an SSE `data:` event for streams).

Run (scripts/dsv41-edge.sh does this): the engine image's python, host network, listen on 127.0.0.1:EDGE_PORT, and
point the tunnel / reverse proxy at it instead of the engine port. Clients on the LAN can keep using the engine port.
It also accepts the API key as `x-api-key` / `api-key` (Anthropic-style clients) and collapses a doubled /v1/v1/ path.
Env: UPSTREAM (http://127.0.0.1:8888), EDGE_HOST (127.0.0.1), EDGE_PORT (8890), GRACE (20), PING (15).
"""
import asyncio, json, os

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8888").rstrip("/")
GRACE, PING = float(os.environ.get("GRACE", "20")), float(os.environ.get("PING", "15"))
HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade", "te", "trailer",
       "proxy-authorization", "proxy-authenticate"}   # content-encoding passes through: bodies are not decompressed


def fwd_headers(h):
    return {k: v for k, v in h.items() if k.lower() not in HOP}


def up_headers(h):
    """Request headers for the engine. The engine takes the key only as `Authorization: Bearer`; Anthropic clients
    send `x-api-key` (others `api-key`), so those become a Bearer header when no Authorization is present."""
    out = fwd_headers(h)
    if not any(k.lower() == "authorization" for k in out):
        key = next((v for k, v in out.items() if k.lower() in ("x-api-key", "api-key")), None)
        if key:
            out["Authorization"] = "Bearer " + key
    return out


def up_path(path_qs):
    """A base URL that already ends in /v1 plus a client that appends /v1/... gives /v1/v1/...: collapse it."""
    while path_qs.startswith("/v1/v1/"):
        path_qs = path_qs[3:]
    return path_qs


async def handle(request: web.Request):
    body = await request.read()
    wants_stream = False
    if body[:1] == b"{":
        try:
            wants_stream = bool(json.loads(body).get("stream"))
        except Exception:
            pass
    sess: ClientSession = request.app["session"]
    up = asyncio.ensure_future(sess.request(request.method, UPSTREAM + up_path(request.rel_url.path_qs),
                                            headers=up_headers(request.headers), data=body or None))
    resp = None
    try:
        r = await asyncio.wait_for(asyncio.shield(up), GRACE)
    except asyncio.TimeoutError:
        r = None
    try:
        if r is None:   # slow start: answer now, keep the connection alive until the engine responds
            resp = web.StreamResponse(status=200, headers={
                "Content-Type": "text/event-stream" if wants_stream else "application/json",
                "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            await resp.prepare(request)
            ping = b": keep-alive\n\n" if wants_stream else b" "
            while True:
                try:
                    r = await asyncio.wait_for(asyncio.shield(up), PING)
                    break
                except asyncio.TimeoutError:
                    await resp.write(ping)
            if r.status != 200:
                err = await r.read()
                await resp.write(b"data: " + err.replace(b"\n", b" ") + b"\n\n" if wants_stream else err)
                return resp
        else:
            resp = web.StreamResponse(status=r.status, headers=fwd_headers(r.headers))
            await resp.prepare(request)
        async for chunk in r.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
        return resp
    except (ConnectionResetError, asyncio.CancelledError):
        up.cancel()      # client went away: drop the upstream request too (the engine aborts it)
        raise
    finally:
        if r is not None:
            r.release()


async def on_start(app):
    app["session"] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=10), connector=TCPConnector(limit=0),
                                   auto_decompress=False)


async def on_stop(app):
    await app["session"].close()


app = web.Application(client_max_size=512 * 1024 * 1024)   # 1M-token prompts and many images
app.router.add_route("*", "/{tail:.*}", handle)
app.on_startup.append(on_start)
app.on_cleanup.append(on_stop)
web.run_app(app, host=os.environ.get("EDGE_HOST", "127.0.0.1"), port=int(os.environ.get("EDGE_PORT", "8890")),
            access_log=None)
