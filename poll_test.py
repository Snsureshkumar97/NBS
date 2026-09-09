"""_poll_queue: a closed market must not blank the panel or open a trade."""
import sys, queue, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import pandas as pd, numpy as np
import gui, skin_bridge

idx = pd.date_range(end="2026-08-18 15:30", periods=200, freq="15min")
base = 24000 + np.cumsum(np.random.RandomState(2).randn(200) * 8)
DF = pd.DataFrame({"Open": base, "High": base+12, "Low": base-12,
                   "Close": base, "Volume": 1000}, index=idx)

class Rec(dict): pass
REC = Rec({"index": "NIFTY", "candles": DF, "spot": float(DF["Close"].iloc[-1]),
           "action": "NO TRADE", "trend": {"label": "SIDEWAYS", "score": 0}})

class App(gui.SignalApp):
    def __init__(self):
        # bypass the real constructor; wire up only what _poll_queue touches
        self.indices = ["NIFTY", "BANKNIFTY"]
        self.states = {k: dict(gui._PER_INDEX_DEFAULTS) for k in self.indices}
        self.current = "NIFTY"; self._focus = "NIFTY"
        self.msg_queue = queue.Queue()
        self.streamer = None
        self._summary_done = True
        self.root = None
        class Lbl:
            def __init__(self): self.text = ""; self.fg = ""
            def config(self, **k):
                self.text = k.get("text", self.text); self.fg = k.get("fg", self.fg)
            def cget(self, key): return getattr(self, key, "")
        for n in ("status_label","market_label","badge_label"):
            setattr(self, n, Lbl())
        self.considered = []
        self.tracked = []
        self.rendered = 0
        self.tabs_painted = 0
        self.text_set = []
    def _consider_signal(self, rec, confirm_now=False): self.considered.append(rec)
    def _track_open_trade(self, rec): self.tracked.append(rec)
    def _render_current(self):
        self.rendered += 1
        self.badge_label.config(text="  WAITING  ", fg="#8b93a7")   # what the tracker really does
    def _paint_tabs(self): self.tabs_painted += 1
    def _set_text(self, content, changed=False): self.text_set.append(content)
    def _save_summary(self, silent=False): pass

def drain(app):
    """Run the body of _poll_queue once, without rescheduling itself."""
    class Root:
        def after(self, *a): pass
    app.root = Root()
    gui.SignalApp._poll_queue(app)

# ---- 1. a stale snapshot renders, but opens nothing --------------------
app = App()
app.msg_queue.put(("report", "NIFTY", REC, "REPORT TEXT", [], True))
drain(app)
assert app.rendered == 1, "stale snapshot was not drawn"
assert not app.considered and not app.tracked, "a closed market must not arm a trade"
assert "MARKET CLOSED" in app.market_label.text, app.market_label.text
assert "last data" in app.market_label.text
assert app.badge_label.text.strip() == "MKT CLOSED", \
    f"badge got overwritten: {app.badge_label.text!r}"
print("stale snapshot:", app.market_label.text.strip())
print("               ", app.status_label.text.strip())
print("               badge:", app.badge_label.text.strip())

# ---- 2. a live report behaves exactly as before ------------------------
app2 = App()
app2.msg_queue.put(("report", "NIFTY", REC, "REPORT TEXT", []))
drain(app2)
assert app2.considered and app2.tracked, "live report stopped arming trades"
assert "MARKET OPEN" in app2.market_label.text
print("live report:  ", app2.market_label.text.strip())

# ---- 3. closing time must not erase a ticket already on screen ---------
app3 = App()
app3.report_text = "MY TICKET: BUY 24000 CE"
app3._summary_done = True
app3.msg_queue.put(("closed", "Market is closed.\nOpens 09:15 IST."))
drain(app3)
assert not app3.text_set, f"the ticket was overwritten with {app3.text_set}"
assert "closed" in app3.status_label.text.lower()
print("close-time banner went to the status line, ticket kept")

# ---- 4. with nothing on screen, the banner does fill the panel ---------
app4 = App()
app4.report_text = ""
app4._summary_done = True
app4.msg_queue.put(("closed", "Market is closed.\nOpens 09:15 IST."))
drain(app4)
assert app4.text_set, "empty panel should show the closed banner"
print("empty panel still explains itself:", app4.text_set[0].splitlines()[0])

print("\nPOLL-QUEUE TEST PASSED")
