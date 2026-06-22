import asyncio
import json
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

app = FastAPI(root_path="/notifier", title="Notifier")

# One asyncio.Queue per connected browser tab
_clients: set[asyncio.Queue] = set()

_WIDGET_JS = r"""
(function () {
  if (window.__notifierWidget) return;
  window.__notifierWidget = true;

  var BASE = '/notifier';
  var DURATION = 6000;

  var style = document.createElement('style');
  style.textContent = [
    '#notifier-wrap{position:fixed;bottom:1.5rem;right:1.5rem;display:flex;flex-direction:column-reverse;gap:.5rem;z-index:99999;pointer-events:none}',
    '.notifier-toast{pointer-events:auto;min-width:280px;max-width:380px;padding:.75rem 1rem;border-radius:8px;',
    'box-shadow:0 4px 20px rgba(0,0,0,.25);font-family:system-ui,sans-serif;font-size:.875rem;',
    'display:flex;flex-direction:column;gap:.3rem;background:#1e1e2e;color:#cdd6f4;',
    'border-left:4px solid #89b4fa;animation:nt-in .22s ease;cursor:pointer}',
    '.notifier-toast[data-level=success]{border-color:#a6e3a1}',
    '.notifier-toast[data-level=warning]{border-color:#fab387}',
    '.notifier-toast[data-level=error]{border-color:#f38ba8}',
    '.nt-title{font-weight:600}',
    '.nt-msg{opacity:.75;font-size:.8125rem}',
    '.nt-action{display:inline-block;margin-top:.2rem;color:#89b4fa;text-decoration:underline;font-size:.8125rem}',
    '.notifier-toast[data-level=success] .nt-action{color:#a6e3a1}',
    '.notifier-toast[data-level=warning] .nt-action{color:#fab387}',
    '.notifier-toast[data-level=error] .nt-action{color:#f38ba8}',
    '@keyframes nt-in{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:translateX(0)}}',
    '@keyframes nt-out{from{opacity:1;transform:translateX(0)}to{opacity:0;transform:translateX(40px)}}',
    '.nt-removing{animation:nt-out .22s ease forwards}',
  ].join('');
  document.head.appendChild(style);

  var wrap = document.createElement('div');
  wrap.id = 'notifier-wrap';
  document.body.appendChild(wrap);

  function removeToast(el) {
    el.classList.add('nt-removing');
    el.addEventListener('animationend', function () { el.remove(); }, { once: true });
  }

  function showToast(data) {
    var el = document.createElement('div');
    el.className = 'notifier-toast';
    el.dataset.level = data.level || 'info';

    var title = document.createElement('div');
    title.className = 'nt-title';
    title.textContent = data.title;
    el.appendChild(title);

    if (data.message) {
      var msg = document.createElement('div');
      msg.className = 'nt-msg';
      msg.textContent = data.message;
      el.appendChild(msg);
    }

    if (data.action_label && data.action_url) {
      var a = document.createElement('a');
      a.className = 'nt-action';
      a.textContent = data.action_label;
      a.href = data.action_url;
      a.addEventListener('click', function (e) { e.stopPropagation(); });
      el.appendChild(a);
    }

    wrap.appendChild(el);
    var timer = setTimeout(function () { removeToast(el); }, DURATION);
    el.addEventListener('click', function () { clearTimeout(timer); removeToast(el); });
  }

  function connect() {
    var src = new EventSource(BASE + '/events');
    src.onmessage = function (e) {
      try { showToast(JSON.parse(e.data)); } catch (_) {}
    };
    src.onerror = function () {
      src.close();
      setTimeout(connect, 3000);
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})();
"""


class Notification(BaseModel):
    title: str
    message: str = ""
    level: str = "info"  # info | success | warning | error
    action_label: str | None = None
    action_url: str | None = None


@app.post("/notify", status_code=202)
async def notify(notification: Notification):
    payload = json.dumps(notification.model_dump())
    dead: set[asyncio.Queue] = set()
    for q in _clients:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.add(q)
    _clients.difference_update(dead)
    return {"clients": len(_clients) - len(dead)}


async def _stream(request: Request) -> AsyncGenerator[str, None]:
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    _clients.add(q)
    try:
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                data = await asyncio.wait_for(q.get(), timeout=25)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        _clients.discard(q)


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/widget.js")
async def widget_js() -> Response:
    return Response(content=_WIDGET_JS, media_type="application/javascript")


@app.get("/livez")
async def livez():
    return {"status": "ok", "clients": len(_clients)}
