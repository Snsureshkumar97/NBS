#!/usr/bin/env python3
"""The server gzips text-like responses for a browser that accepts it.

Why: measured 24 Sep 2026, the tool answered in milliseconds but reached the user
through Tailscale's Funnel relays at 1.3-1.6 s to the first byte and only 40-100 KB/s
after it, and its own page (425 KB) went out uncompressed - opening the tool cost
several seconds of pure transfer. Nothing about WHAT is sent may change: only how many
bytes it takes. So this checks that the bytes decompress to exactly what the plain
response is, that a client that does not ask (or forbids it) gets the plain bytes,
that small and non-text bodies are left alone, and that the headers are honest.

A real server on a random local port; no network, no account.
"""
import gzip
import http.client
import io
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. WHO MAY BE SENT GZIP")
A = web_server.accepts_gzip
table = [("gzip", True), ("gzip, deflate, br", True), ("br, gzip;q=0.5", True), ("GZIP", True), ("x-gzip", True),
         ("deflate, br", False), ("identity", False), ("", False), (None, False), ("gzip;q=0", False), ("gzip; q=0.0", False),
         ("*", False), ("gzip;q=abc", False), ("gzipx", False)]
for hdr, want in table:
    check(f"Accept-Encoding {hdr!r} -> {want}", A(hdr) is want, A(hdr))

print("2. THE BODY")
big = ("<div class='row'>trade picker signal 24000 CE</div>\n" * 400).encode()
gz = web_server.gzip_body(big)
check("it round-trips byte for byte", gzip.decompress(gz) == big)
check("text shrinks a great deal (a five-fold saving is why this exists)", len(gz) < len(big) / 5, (len(big), len(gz)))
huge = os.urandom(10) * 0 + (b"x" * 60_000) + b"y"
g1, g2 = web_server.gzip_body(huge), web_server.gzip_body(huge)
check("a big body (the page) is compressed once per content, not once per request", g1 is g2)
for i in range(20):
    web_server.gzip_body(b"z" * (60_000 + i))
check("that memory is bounded", len(web_server._gz_cache) <= 6, len(web_server._gz_cache))
small = web_server.gzip_body(b"a" * 5000)
check("a small body is not kept", gzip.decompress(small) == b"a" * 5000 and len(web_server._gz_cache) <= 6)

print("3. A REAL SERVER")
srv = web_server.Server(("127.0.0.1", 0), web_server.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(path, enc=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {} if enc is None else {"Accept-Encoding": enc}
    c.request("GET", path, headers=h)
    r = c.getresponse()
    body = r.read()
    hdr = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdr, body


try:
    st0, h0, plain = get("/login")
    st1, h1, packed = get("/login", "gzip, deflate, br")
    check("the plain response is a real page", st0 == 200 and len(plain) > 5000 and "content-encoding" not in h0, (st0, len(plain)))
    check("asking for gzip gets gzip, and it says so", st1 == 200 and h1.get("content-encoding") == "gzip"
          and "accept-encoding" in h1.get("vary", "").lower(), h1)
    check("its Content-Length is the compressed size actually sent", int(h1["content-length"]) == len(packed))
    check("decompressed it is EXACTLY the plain page", gzip.decompress(packed) == plain)
    check("and is much smaller", len(packed) < len(plain) / 2.5, (len(plain), len(packed)))
    check("the plain response also tells caches it varies (so a gzip copy is never served to a client that cannot read it)",
          "accept-encoding" in h0.get("vary", "").lower(), h0)
    for enc in ("identity", "gzip;q=0", "deflate, br", ""):
        st, h, b = get("/login", enc)
        check(f"Accept-Encoding {enc!r}: the plain bytes, no Content-Encoding", st == 200 and b == plain and "content-encoding" not in h)
    st2, h2, b2 = get("/no-such-page-here", "gzip")
    st3, h3, b3 = get("/no-such-page-here")
    check("an error page is compressed the same way, keeps its status, and reads the same",
          st2 == st3 == 404 and h2.get("content-encoding") == "gzip" and gzip.decompress(b2) == b3, (st2, st3))
    check("Cache-Control stays no-store on both (compression changes bytes, not what may be kept)",
          h0.get("cache-control") == "no-store" and h1.get("cache-control") == "no-store")
    check("the security headers are still sent on the compressed response",
          "content-security-policy" in h1 and h1.get("x-content-type-options") == "nosniff")
finally:
    srv.shutdown()

print("4. THE SENDER ITSELF: WHICH KINDS OF BODY")
class Out(io.BytesIO):
    pass


def send(body, ctype, enc="gzip", code=200):
    h = object.__new__(web_server.Handler)
    sent = {"headers": []}
    h.headers = {"Accept-Encoding": enc} if enc is not None else {}
    h.wfile = Out()
    h.send_response = lambda c, *a: sent.update(code=c)
    h.send_header = lambda k, v: sent["headers"].append((k.lower(), v))
    h.end_headers = lambda: None
    h._send(body, ctype, code)
    return dict(sent["headers"]), h.wfile.getvalue()


js = ('{"a": [' + ",".join(str(i) for i in range(2000)) + ']}').encode()
hd, out = send(js, "application/json")
check("JSON (the state the page polls) is compressed", hd.get("content-encoding") == "gzip" and gzip.decompress(out) == js
      and int(hd["content-length"]) == len(out))
hd, out = send(js.decode(), "text/html; charset=utf-8")
check("text sent as a str is encoded and compressed", hd.get("content-encoding") == "gzip" and gzip.decompress(out) == js)
hd, out = send(b"\x89PNG" + os.urandom(6000), "image/png")
check("an image is never compressed (it already is)", "content-encoding" not in hd and "vary" not in hd)
hd, out = send(js, "application/octet-stream")
check("an unknown binary type is not compressed", "content-encoding" not in hd)
hd, out = send(js, "application/json", enc=None)
check("a request with no Accept-Encoding header at all gets the plain bytes", "content-encoding" not in hd and out == js)
hd, out = send(b"{}", "application/json")
check("a tiny reply is sent as it is", "content-encoding" not in hd and out == b"{}")
hd, out = send("", "text/html; charset=utf-8", code=404)
check("an empty body stays empty", out == b"" and hd["content-length"] == "0")
h = object.__new__(web_server.Handler)
h.wfile, h.send_response, h.send_header, h.end_headers = Out(), (lambda *a: None), (lambda *a: None), (lambda: None)
try:
    h._send(js, "application/json")
    ok = h.wfile.getvalue() == js
except AttributeError:
    ok = False
check("a handler with no headers object at all (as in the other tests' stand-ins) still sends", ok)

print("COMPRESSION TEST PASSED" if not fails else f"COMPRESSION TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
