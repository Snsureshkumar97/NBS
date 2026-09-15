"""Zerodha tick socket: one made after twisted's reactor has gone to sleep
still connects, a stopped one stops retrying, and a reactor that has died is
reported for a restart instead of being rebuilt into.

Runs the real KiteStreamer and the feed's own rebuild against a stand-in for
ws.kite.trade on 127.0.0.1 - no credentials and no network, so it does not
spend any of Zerodha's three connections per token."""
import base64, hashlib, heapq, os, socket, struct, sys, threading, time, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kiteconnect import KiteTicker
from twisted.internet import reactor
import config, data_providers as dp, feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"      # RFC 6455
NIFTY = 256265
config.KITE_API_KEY = "test-key"                     # never the real one, even to localhost


class FakeKite:
    """ws.kite.trade in miniature. answer=False takes the TCP connection and
    never replies, so the client's opening handshake times out - the failure
    the watchdog then has to rebuild out of."""

    def __init__(self, answer=True):
        self.answer = answer
        self.connections = 0
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            conn, _ = self.srv.accept()
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            if not self.answer:
                while conn.recv(4096):
                    pass
                return
            key = next(l.split(b":", 1)[1].strip() for l in head.split(b"\r\n")
                       if l.lower().startswith(b"sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                         b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()
            px = 2_500_000                                   # paise
            while True:
                px += 5
                pkt = struct.pack(">ii", NIFTY, px)
                body = struct.pack(">HH", 1, len(pkt)) + pkt
                conn.sendall(bytes([0x82, len(body)]) + body)
                time.sleep(0.2)
        except OSError:
            pass
        finally:
            conn.close()

    def _read(self, conn):
        """Answer a close frame, so a stopped socket goes away promptly."""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                if data[0] & 0x0F == 0x8:
                    conn.sendall(b"\x88\x00")
                    conn.close()
                    return
        except OSError:
            pass


def streamer(port):
    KiteTicker.ROOT_URI = f"ws://127.0.0.1:{port}"
    return dp.KiteStreamer("test-key", "test-token")

def from_thread(fn):
    """Call fn on a plain thread - the way the feed and tick loops reach the
    ticker, and the only way the 15 Sep failure shows."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", fn()))
    t.start()
    t.join()
    return out.get("r")

def ticks_within(st, secs):
    end = time.time() + secs
    while time.time() < end:
        if st.tick_count:
            return True
        time.sleep(0.05)
    return False

def wait_for(cond, secs):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()

def put_reactor_to_sleep():
    """Recreate the morning of 15 Sep: no socket open and no timer pending, so
    the reactor sleeps in select() with no timeout at all.

    Twisted leaves a cancelled timer in its heap until it comes due - the 30s
    connect timeout among them - and each would wake the reactor once, which
    only delays the state by half a minute. They are dropped here, on the
    reactor thread, the same way its own runUntilCurrent prunes them."""
    wait_for(lambda: not reactor.getDelayedCalls(), 15)
    done, left = threading.Event(), []

    def prune():
        reactor._insertNewDelayedCalls()
        reactor._pendingTimedCalls = [c for c in reactor._pendingTimedCalls
                                      if not c.cancelled]
        heapq.heapify(reactor._pendingTimedCalls)
        reactor._cancellations = 0
        left.append(len(reactor._pendingTimedCalls))
        done.set()
    reactor.callFromThread(prune)
    done.wait(5)
    time.sleep(0.5)                  # let it get back into select()
    return left == [0]


good = FakeKite()
hole = FakeKite(answer=False)

print("1. A SOCKET OPENED FROM A FEED THREAD TICKS")
a = streamer(good.port)
check("start() says yes", from_thread(a.start) is True, str(a.last_error))
from_thread(lambda: a.subscribe([NIFTY]))
check("ticks arrive", ticks_within(a, 5), f"last_error={a.last_error}")

print("2. STOPPING IT LEAVES THE REACTOR WITH NOTHING TO DO")
from_thread(a.stop)
check("the ticker is let go", a._kws is None)
check("the reactor is asleep with no timer to wake it", put_reactor_to_sleep(),
      f"timers={reactor.getDelayedCalls()}")

print("3. A SOCKET OPENED WHILE IT SLEEPS STILL CONNECTS (15 SEP)")
# Before the fix this socket was scheduled onto a list the sleeping reactor
# never read: no TCP connection, no error, no tick, until a restart.
before = good.connections
b = streamer(good.port)
check("start() says yes", from_thread(b.start) is True, str(b.last_error))
from_thread(lambda: b.subscribe([NIFTY]))
check("the server sees it connect", wait_for(lambda: good.connections > before, 5),
      f"connections {before} -> {good.connections}")
check("ticks arrive", ticks_within(b, 5), f"last_error={b.last_error}")
check("and the subscription went out", b.price(NIFTY) is not None)

print("4. THE WATCHDOG'S REBUILD, OUT OF A FAILED HANDSHAKE")
from_thread(b.stop)
c = streamer(hole.port)
from_thread(c.start)
check("the handshake to a silent server fails", wait_for(lambda: c.last_error, 15),
      str(c.last_error))
feed = feeds.Feed.__new__(feeds.Feed)
feed.email, feed.market = "test@example.invalid", "nse_index"
feed.tokens, feed.opt_tokens, feed.sug_tokens, feed.eq_tokens = {"NIFTY": NIFTY}, {}, {}, {}
feed.streamer, feed.stream_error, feed._stream_born = c, None, 0.0
feeds.user_kite = types.SimpleNamespace(token_for=lambda email: "test-token")
KiteTicker.ROOT_URI = f"ws://127.0.0.1:{good.port}"
from_thread(lambda: feed._rebuild_kite_stream(c))
new = feed.streamer
check("a new socket replaced the old one", new is not c)
check("the new one ticks", ticks_within(new, 5), f"last_error={new.last_error}")
tries = hole.connections
time.sleep(8)
check("the old one stopped retrying", hole.connections == tries,
      f"connections {tries} -> {hole.connections}")

print("5. A DEAD REACTOR IS REPORTED, NOT REBUILT INTO")
check("healthy reactor reports no problem", dp.kite_reactor_problem() is None,
      str(dp.kite_reactor_problem()))
reactor.callFromThread(reactor.stop)
check("the reactor thread ends", wait_for(lambda: not dp._reactor_thread.is_alive(), 10))
problem = dp.kite_reactor_problem()
check("now it reports one", problem is not None, str(problem))
d = streamer(good.port)
check("a new socket refuses to start", from_thread(d.start) is False)
check("and says a restart is needed", "restart the server" in (d.last_error or ""),
      str(d.last_error))
feeds.is_market_open = lambda *a, **k: True
feed.instruments = lambda: ["NIFTY"]
# Born long ago and last rebuilt long ago, so the watchdog's own gates let it
# through to the rebuild - the test is what the rebuild does, not the backoff.
feed._stream_born, feed._last_rebuild, feed._rebuild_gap = 0.0, 0.0, feeds.STALL_SECONDS
silent = types.SimpleNamespace(age_seconds=lambda: 999.0)
feed.streamer = silent
from_thread(lambda: feed._kite_watchdog(silent))
check("the watchdog does not pile a socket onto it", feed.streamer is silent)
check("the feed says a restart is needed", "restart the server" in (feed.stream_error or ""),
      str(feed.stream_error))
feeds._feeds["kite_stream_test"] = feed
try:
    probs = feeds.stream_problems()
    check("the operator page lists it", any(p["error"] == feed.stream_error for p in probs),
          str(probs))
finally:
    feeds._feeds.pop("kite_stream_test", None)
check("the operator page sees the dead engine", feeds.tick_engine_problem() is not None)

print()
print("KITE STREAM TEST PASSED" if not fails else f"KITE STREAM TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
