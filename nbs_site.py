"""
nbs_site.py — the public website for NBS Signal Tool
================================================================================
Every page a visitor can reach without an account, plus the login and Zerodha
connect screens that sit between them and the app:

    /              what it is, and the short version of everything below
    /how-it-works  the rule set, step by step, including what blocks a signal
    /screen        every screen in the app, with what each part is for
    /results       what it measured when it was tested, and what it has done here
    /security      what is stored, how, and what never leaves this server
    /access        how accounts and the Zerodha connection actually work
    /faq           the questions worth asking
    /disclaimer    the SEBI position and the risk statement, in full
    /login         the way in
    /connect       each user's own Zerodha session

WHY THE SITE IS LIGHT AND THE APP IS DARK
    They are doing different jobs. The app is stared at for hours next to a
    broker terminal, where a dark screen is easier on the eyes and lets green
    and red carry meaning. These pages are read once, in daylight, probably on
    a phone — so they are white, and the screenshots sit on them as objects
    with a border rather than bleeding into the background.

WHY THE MEASURED RESULT IS NOT BURIED
    A marketing site for a signal tool writes itself, and it writes itself
    dishonestly: the winning days, a confidence dial, a running P&L in green.
    This site has all three, because they are what the app actually renders —
    so the measurement gets its own page in the main navigation, is linked from
    the home page above the fold, and says plainly that across three years and
    8,837 signals the rule set was roughly break-even before costs and negative
    after them.

    India regulates investment advice. Publishing buy/sell calls can fall under
    SEBI's Research Analyst and Investment Adviser rules, and a disclaimer is
    not a licence. See /disclaimer, and the SEBI section of the README before
    money enters this picture in any form.

ABOUT THE SCREENSHOTS
    They are the real thing — PNGs rendered straight from the app's own canvas
    by the shot_*.py scripts, the same code path that draws the window on a
    Mac. Nothing here is a mockup of a screen that does not exist. Some of them
    say DEMO or PREVIEW because they were taken from demo and market-closed
    sessions.
"""

import os

import accounts
import config

BRAND = "NBS Signal Tool"
TAGLINE = "Nifty · Bank Nifty · Sensex"

NAV = [
    ("/how-it-works", "How it works"),
    ("/screen",       "The screen"),
    ("/results",      "Results"),
    ("/security",     "Security"),
    ("/access",       "Access"),
    ("/faq",          "FAQ"),
]

# The only files this server will serve as images. A whitelist rather than a
# directory, because "serve any png from the app folder" and "serve any file
# from the app folder" are one path-traversal bug apart, and the app folder is
# where .env used to live.
SHOTS = {
    "board":   ("gui_light.png",      "The signal board",
                "One ticket, its entry, its three targets and its stop — with the levels frozen at entry so they cannot drift while the trade is open. The dial on the right is confidence, and every input that fed it is listed underneath, including the ones that abstained."),
    "chart":   ("closed_chart.png",   "The chart tab",
                "Candles, both EMAs, VWAP, and the same T1/T2/T3 and stop drawn where they actually sit. Underneath, in sentences, the reason the rule set reached that call — the EMA stack, the MACD histogram, where RSI sits in its band."),
    "targets": ("gui_portfolio.png",  "Targets as cards",
                "Each target shows how far price has travelled toward it rather than just whether it was hit. T1 reached at 11:44:09; T2 is 38% of the way; the stop is 0% of the way, which is the number you actually want to watch."),
    "waiting": ("gui_day_done.png",   "A quiet day, which is most days",
                "Range-bound, ADX 10.7, momentum fading, nothing issued. The direction is shown as a PREVIEW rather than a ticket, because a view and a tradeable setup are different things. The session log below carries what already closed."),
    "dark":    ("gui_dark.png",       "The dark theme",
                "The same board at night. Both themes are painted by the app itself rather than by the operating system, so it looks identical on a Mac, on Windows and on Linux — and can be rendered to a PNG with no display at all, which is how the layout is checked."),
    "wide":    ("gui_fullscreen.png", "Full width",
                "The layout solves for the space it is given instead of scaling a fixed design, so a wider window gets roomier cards rather than a stretched copy of the small one."),
}


def shot_path(slug):
    """Absolute path of a whitelisted screenshot, or None."""
    entry = SHOTS.get(slug)
    if not entry:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), entry[0])
    return path if os.path.isfile(path) else None


def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# ===========================================================================
# LOOK
# ===========================================================================
CSS = """
/* Black. Kite's semantics kept — #4caf50 up, #ff5722 down, a blue accent,
   hairline borders, 3px corners, no gradients — but inverted onto near-black,
   because this is looked at for hours and a white field at 09:15 is a lamp
   pointed at your face.

   The accent is lifted from Kite's #4d94e8 to #4d94e8: the darker blue reads
   fine on white and goes muddy on black, and an accent you have to hunt for
   has stopped being one. Up and down keep their exact hues, because those two
   carry meaning, and re-tuning them per theme is how a red comes to look like
   an amber on one screen and not the other. */
:root{
  --bg:#0b0b0d; --surface:#141417; --raised:#1b1b20; --sunken:#0f0f12;
  --bd:#2a2a31; --bd-soft:#1e1e24;
  --ink:#e8e8ec; --ink-2:#a2a2ac; --ink-3:#6f6f7b;
  --up:#4caf50; --down:#ff5722; --warn:#f6a500; --accent:#4d94e8;
  --r:3px; --r-sm:3px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased}
img{max-width:100%;display:block}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1060px;margin:0 auto;padding:0 22px}
/* Left-aligned, not centred: the page heading above it sits at the
   wrapper's left edge, and a centred column under a left-aligned title
   reads as an accidental indent rather than as a measure. */
.narrow{max-width:760px;margin:0}

/* ---------- header ---------- */
header{position:sticky;top:0;z-index:30;background:rgba(11,11,13,.9);
  backdrop-filter:saturate(180%) blur(12px);border-bottom:1px solid var(--bd)}
.hd{max-width:1060px;margin:0 auto;padding:11px 22px;display:flex;
  align-items:center;gap:18px;justify-content:space-between}
.brand{display:flex;align-items:center;gap:11px;font-weight:700;letter-spacing:-.2px;
  color:var(--ink);font-size:15.5px;flex:none}
.brand:hover{text-decoration:none}
.brand small{display:block;font-weight:500;font-size:10.5px;color:var(--ink-3);
  letter-spacing:.4px;text-transform:uppercase}
nav{display:flex;gap:20px;align-items:center;font-size:14px}
nav a{color:var(--ink-2);font-weight:500;white-space:nowrap}
nav a:hover{color:var(--ink);text-decoration:none}
nav a.on{color:var(--ink);font-weight:650}
.btn{display:inline-block;background:var(--accent);color:#fff;font-weight:700;
  padding:9px 17px;border-radius:10px;font-size:14.5px;border:0;cursor:pointer;
  font-family:inherit}
.btn:hover{text-decoration:none;filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--bd);box-shadow:none}
@media(max-width:1000px){nav .hide{display:none}}

/* Below 1000px the header links are hidden to keep the bar from wrapping, so
   they reappear here as a strip that scrolls sideways. A hamburger would need
   state and JavaScript to hide six links that fit perfectly well in a row you
   can push with your thumb. */
.subnav{display:none}
@media(max-width:1000px){
  .subnav{display:block;position:sticky;top:56px;z-index:29;background:var(--bg);
    border-bottom:1px solid var(--bd-soft);overflow-x:auto;
    -webkit-overflow-scrolling:touch;scrollbar-width:none}
  .subnav::-webkit-scrollbar{display:none}
  .subnav ul{display:flex;gap:20px;list-style:none;margin:0;
    padding:11px 22px;white-space:nowrap;font-size:14px}
  .subnav a{color:var(--ink-2);font-weight:500}
  .subnav a.on{color:var(--accent);font-weight:700}
}

/* ---------- page head ---------- */
.phead{padding:60px 0 34px;border-bottom:1px solid var(--bd-soft);margin-bottom:52px}
.phead h1{font-size:clamp(30px,4.6vw,44px);line-height:1.12;margin:0 0 14px;
  letter-spacing:-.6px;font-weight:800}
.phead p{color:var(--ink-2);font-size:17px;max-width:660px;margin:0}

/* ---------- hero ---------- */
.hero{padding:74px 0 8px;text-align:center}
.eyebrow{display:inline-flex;align-items:center;gap:8px;background:var(--surface);
  border:1px solid var(--bd);border-radius:999px;padding:6px 14px;font-size:12.5px;
  font-weight:600;color:var(--ink-2);margin-bottom:26px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);flex:none}
h1{font-size:clamp(34px,6vw,58px);line-height:1.08;margin:0 0 20px;
  letter-spacing:-.8px;font-weight:800}
h1 .thin{color:var(--ink-3);font-weight:500;letter-spacing:-.6px;
  display:block;font-size:clamp(15px,2.4vw,20px);margin-top:14px}
.lede{font-size:clamp(16px,2.1vw,19px);color:var(--ink-2);max-width:660px;
  margin:0 auto 30px}
.cta{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.shotwrap{margin:52px 0 0;border:1px solid var(--bd);border-radius:var(--r);
  overflow:hidden;background:var(--sunken);
  box-shadow:none}

/* ---------- sections ---------- */
section{padding:70px 0;border-top:1px solid var(--bd-soft)}
section.first{border-top:0;padding-top:0}
.kicker{font-size:11.5px;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;
  color:var(--accent);margin:0 0 12px}
h2{font-size:clamp(24px,3.4vw,32px);line-height:1.2;margin:0 0 16px;
  letter-spacing:-.4px;font-weight:750}
h3{font-size:17px;margin:0 0 7px;font-weight:700}
h4{font-size:15px;margin:26px 0 8px;font-weight:700}
.sub{color:var(--ink-2);max-width:680px;margin:0 0 36px;font-size:16.5px}
p{margin:0 0 15px}
.mut{color:var(--ink-3)}
.prose p{color:var(--ink-2);font-size:16px}
.prose b{color:var(--ink)}
.prose ul{color:var(--ink-2);padding-left:22px;margin:0 0 16px}
.prose li{margin-bottom:8px}

/* ---------- cards ---------- */
.grid{display:grid;gap:16px}
.g3{grid-template-columns:repeat(3,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}
@media(max-width:840px){.g3,.g2{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--bd);border-radius:var(--r);
  padding:22px 22px 24px}
.card p{color:var(--ink-2);font-size:14.5px;margin:0}
.card .idx{font-size:12px;font-weight:700;color:var(--accent);letter-spacing:.6px;
  text-transform:uppercase;margin-bottom:10px}

/* ---------- steps ---------- */
ol.steps{list-style:none;counter-reset:s;margin:0;padding:0;display:grid;gap:14px}
ol.steps li{counter-increment:s;background:var(--surface);border:1px solid var(--bd);
  border-radius:var(--r);padding:20px 22px 20px 68px;position:relative}
ol.steps li::before{content:counter(s);position:absolute;left:22px;top:19px;
  width:30px;height:30px;border-radius:50%;background:var(--raised);
  border:1px solid var(--bd);color:var(--accent);font-weight:700;font-size:14px;
  display:flex;align-items:center;justify-content:center}
ol.steps p{color:var(--ink-2);font-size:14.5px;margin:0}
ol.steps p + p{margin-top:9px}

/* ---------- tables ---------- */
.tbl{width:100%;border-collapse:collapse;font-size:14.5px;margin:0 0 8px}
.tbl th{text-align:left;font-size:11px;letter-spacing:.8px;text-transform:uppercase;
  color:var(--ink-3);font-weight:700;padding:0 14px 9px 0;border-bottom:1px solid var(--bd)}
.tbl td{padding:13px 14px 13px 0;border-bottom:1px solid var(--bd-soft);
  color:var(--ink-2);vertical-align:top}
.tbl td:first-child{color:var(--ink);font-weight:650;white-space:nowrap}
.tbl code{background:var(--raised);border-radius:5px;padding:2px 6px;font-size:13px}

/* ---------- figures ---------- */
figure{margin:0 0 44px}
figure:last-child{margin-bottom:0}
figure img{border:1px solid var(--bd);border-radius:var(--r);background:var(--sunken);
  box-shadow:none}
figcaption{color:var(--ink-2);font-size:14.5px;margin-top:14px;max-width:760px}
figcaption b{color:var(--ink);display:block;margin-bottom:3px}

/* ---------- callouts ---------- */
.callout{border-radius:var(--r);padding:26px 28px 22px;margin:0}
.callout h2,.callout h3{margin-top:0}
.callout p:last-child{margin-bottom:0}
.warm{background:#1c1710;border:1px solid #3a2f18;color:#d8c9a8}
.warm .kicker{color:#e0a93a}
.warm h2,.warm h3{color:#f0bf55}
.warm b{color:#f7d489}
.warm a{color:#f0bf55;text-decoration:underline}
.cool{background:var(--surface);border:1px solid var(--bd);color:var(--ink-2)}
.figs{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0 20px}
@media(max-width:760px){.figs{grid-template-columns:repeat(2,1fr)}}
.fig{background:#221c12;border:1px solid #3a2f18;border-radius:var(--r-sm);
  padding:16px 16px 14px}
.fig .n{font-size:25px;font-weight:700;letter-spacing:-.6px;color:#f0bf55;line-height:1.1}
.fig .l{font-size:12px;color:#b3a894;margin-top:5px;line-height:1.45}

/* ---------- lists with marks ---------- */
ul.plain{list-style:none;margin:0;padding:0;display:grid;gap:11px}
ul.plain li{display:flex;gap:12px;align-items:flex-start;color:var(--ink-2);font-size:15px}
ul.plain .x,ul.plain .t{flex:none;width:20px;height:20px;border-radius:50%;
  font-size:12px;display:flex;align-items:center;justify-content:center;
  margin-top:2px;font-weight:700}
ul.plain .x{background:#2a1610;border:1px solid #5c2a18;color:#ff8a65}
ul.plain .t{background:#122017;border:1px solid #1f4a2c;color:#7ed492}

/* ---------- faq ---------- */
details{background:var(--surface);border:1px solid var(--bd);border-radius:var(--r-sm);
  padding:16px 20px;margin-bottom:10px}
details[open]{background:var(--raised)}
summary{cursor:pointer;font-weight:650;font-size:15.5px;list-style:none}
summary::-webkit-details-marker{display:none}
summary::after{content:"+";float:right;color:var(--ink-3);font-weight:400;font-size:19px;
  line-height:1}
details[open] summary::after{content:"\\2212"}
details p{color:var(--ink-2);font-size:14.5px;margin:12px 0 0}

/* ---------- next / prev ---------- */
.next{display:flex;gap:14px;flex-wrap:wrap;margin-top:56px;padding-top:28px;
  border-top:1px solid var(--bd-soft)}
.next a{background:var(--surface);border:1px solid var(--bd);border-radius:var(--r-sm);
  padding:14px 18px;flex:1 1 220px;color:var(--ink)}
.next a:hover{text-decoration:none;border-color:var(--accent)}
.next b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.8px;
  color:var(--ink-3);font-weight:700;margin-bottom:3px}

/* ---------- footer ---------- */
footer{border-top:1px solid var(--bd-soft);padding:48px 0 60px;color:var(--ink-3);
  font-size:13.5px;margin-top:70px;background:var(--sunken)}
footer .legal{background:#1c1710;border:1px solid #3a2f18;border-radius:var(--r-sm);
  padding:18px 20px;margin-bottom:32px;line-height:1.7;color:#c9bc9e;font-size:13px}
footer .legal b{color:#f0bf55}
.fcols{display:grid;grid-template-columns:2fr 1fr 1fr;gap:26px;margin-bottom:30px}
@media(max-width:760px){.fcols{grid-template-columns:1fr 1fr}}
.fcols h5{font-size:11px;text-transform:uppercase;letter-spacing:.9px;color:var(--ink-3);
  margin:0 0 12px;font-weight:700}
.fcols ul{list-style:none;margin:0;padding:0;display:grid;gap:9px}
.fcols a{color:var(--ink-2)}
.foot{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;
  padding-top:22px;border-top:1px solid var(--bd)}

/* ---------- motion ----------------------------------------------------- */
/* Everything below is decoration, and decoration that moves has a cost: it
   competes for attention with the numbers, and for some people it causes
   actual discomfort. So it is all short, none of it loops forever except the
   one thing that is genuinely conveying "this is live", and the whole lot is
   switched off for anyone who has asked their system for reduced motion. */
@media(prefers-reduced-motion:no-preference){
  .reveal{opacity:0;transform:translateY(14px);
    transition:opacity .6s cubic-bezier(.2,.7,.3,1),
               transform .6s cubic-bezier(.2,.7,.3,1)}
  .reveal.in{opacity:1;transform:none}
  /* Staggered, so a row of cards arrives as a sequence rather than a flash. */
  .reveal[data-d="1"]{transition-delay:.07s}
  .reveal[data-d="2"]{transition-delay:.14s}
  .reveal[data-d="3"]{transition-delay:.21s}

  /* The hero chart draws itself once, the way a chart actually fills in. */
  .drawline{stroke-dasharray:1200;stroke-dashoffset:1200;
    animation:draw 2.2s cubic-bezier(.3,.8,.4,1) .3s forwards}
  @keyframes draw{to{stroke-dashoffset:0}}
  .fadein{opacity:0;animation:fadein .5s ease 1.6s forwards}
  @keyframes fadein{to{opacity:1}}
  .popin{opacity:0;transform:scale(.82);
    animation:popin .45s cubic-bezier(.2,1.4,.4,1) forwards}
  @keyframes popin{to{opacity:1;transform:scale(1)}}

  /* The one continuous loop: a pulse travelling the pipeline, which is the
     diagram saying "this runs every second" rather than being ornament. */
  .flow{stroke-dasharray:5 9;animation:flow 1.4s linear infinite}
  @keyframes flow{to{stroke-dashoffset:-14}}
}
@media(prefers-reduced-motion:reduce){
  .reveal{opacity:1;transform:none}
  .fadein,.popin{opacity:1}
}

/* ---------- the pipeline diagram --------------------------------------- */
.diagram{background:var(--surface);border:1px solid var(--bd);
  border-radius:var(--r);padding:22px 18px 16px;margin:0 0 14px}
.diagram svg{width:100%;height:auto;display:block;overflow:visible}
.diagram figcaption{color:var(--ink-3);font-size:13px;margin-top:14px;
  text-align:center}
.dg-label{font-size:11px;font-weight:700;letter-spacing:.4px;
  fill:var(--ink-2);text-transform:uppercase}
.dg-sub{font-size:10.5px;fill:var(--ink-3)}
.dg-box{fill:var(--raised);stroke:var(--bd)}
.dg-gate{fill:#1c1710;stroke:#3a2f18}
.dg-out{fill:#122017;stroke:#1f4a2c}
.dg-wait{fill:var(--raised);stroke:var(--bd)}
.dg-wire{stroke:var(--bd);stroke-width:1.5;fill:none}

/* ---------- the hero chart --------------------------------------------- */
.heroart{margin:34px auto 0;max-width:560px}
.heroart svg{width:100%;height:auto;display:block;overflow:visible}

/* ---------- forms (login / connect) ---------- */
.mid{max-width:520px;margin:0 auto;padding:56px 0 20px}
.panel{background:var(--surface);border:1px solid var(--bd);border-radius:var(--r);
  padding:26px 26px 28px;box-shadow:none}
.panel h1{font-size:22px;letter-spacing:-.4px;margin:0 0 6px}
.panel .sub{font-size:14.5px;margin:0 0 20px}
label.f{display:block;font-size:12px;color:var(--ink-2);font-weight:700;
  margin:14px 0 6px;letter-spacing:.2px}
input[type=email],input[type=password],input[type=text]{width:100%;background:var(--sunken);
  color:var(--ink);border:1px solid var(--bd);border-radius:9px;padding:11px 12px;
  font-size:15px;font-family:inherit}
input:focus{outline:2px solid var(--accent);outline-offset:1px}
button.wide{width:100%;margin-top:20px}
.err{background:#2a1610;border:1px solid #5c2a18;color:#ff8a65;border-radius:var(--r-sm);
  padding:11px 13px;font-size:13.5px;margin-bottom:16px}
.ok{background:#122017;border:1px solid #1f4a2c;color:#7ed492;border-radius:var(--r-sm);
  padding:11px 13px;font-size:13.5px;margin-bottom:16px}
.warnbox{background:#1c1710;border:1px solid #3a2f18;color:#d8c9a8;border-radius:var(--r-sm);
  padding:11px 13px;font-size:13.5px;margin-bottom:16px}
.warnbox b{color:#f0bf55}
.hint{color:var(--ink-3);font-size:12.5px;margin-top:8px}
.alt{text-align:center;margin-top:16px;font-size:13.5px;color:var(--ink-3)}
.state{display:inline-flex;align-items:center;gap:8px;border-radius:999px;
  padding:6px 13px;font-size:12.5px;font-weight:700;border:1px solid var(--bd);
  background:var(--surface);color:var(--ink-2)}
.state .d{width:8px;height:8px;border-radius:50%;flex:none;background:var(--ink-3)}
.state.on{background:#122017;border-color:#1f4a2c;color:#7ed492}
.state.on .d{background:var(--up)}
.state.off{background:#1c1710;border-color:#3a2f18;color:#e0a93a}
.state.off .d{background:var(--warn)}
.rows{margin:18px 0 0;border-top:1px solid var(--bd-soft)}
.row{display:flex;justify-content:space-between;gap:16px;padding:11px 0;
  border-bottom:1px solid var(--bd-soft);font-size:14px}
.row b{font-weight:600;color:var(--ink-2)}
.row span{color:var(--ink);text-align:right}
form.inline{display:inline}
button.link{background:none;border:0;color:var(--ink-3);font-size:13px;cursor:pointer;
  padding:0;text-decoration:underline;font-family:inherit}
"""

# The tab icon. An SVG rather than an .ico because it is the same mark as the
# header logo, scales to any tab size, and costs a few hundred bytes — and
# because a missing favicon is a 404 in every visitor's console.
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           '<rect width="24" height="24" rx="6" fill="#4d94e8"/>'
           '<path d="M5 16.5l3.6-4.2 2.9 2.6 3-4.4 4.5 3.4" stroke="#ffffff" '
           'stroke-width="2" fill="none" stroke-linecap="round" '
           'stroke-linejoin="round"/>'
           '<circle cx="19" cy="13.9" r="2" fill="#8ee6a8"/></svg>')


def robots_txt(base=""):
    """Public pages are meant to be found; everything behind the login is not.

    The gated paths are already refused without a session — this only keeps
    them out of search results, where a login form indexed under someone's
    brand name is noise at best.
    """
    lines = ["User-agent: *"]
    for path in ("/app", "/admin", "/api/", "/connect", "/chart/", "/login",
                 "/signup", "/logout", "/kite/"):
        lines.append(f"Disallow: {path}")
    lines.append("Allow: /")
    if base:
        lines.append(f"Sitemap: {base}/sitemap.xml")
    return "\n".join(lines) + "\n"


def sitemap_xml(base):
    urls = "".join(f"<url><loc>{base}{'' if path == '/' else path}</loc></url>"
                   for path in PAGES)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + urls + "</urlset>")


LOGO = ('<svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<rect x="1" y="1" width="22" height="22" rx="6" fill="#1b1b20" stroke="#2a2a31"/>'
        '<path d="M5 16.5l3.6-4.2 2.9 2.6 3-4.4 4.5 3.4" stroke="#4d94e8" '
        'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="19" cy="13.9" r="2" fill="#4caf50"/></svg>')


# ===========================================================================
# SHELL
# ===========================================================================
def shell(title, body, user=None, active="", description="", noindex=False):
    """Every page on the site, wrapped in the same chrome."""
    links = "".join(
        f'<a class="hide{" on" if href == active else ""}" href="{href}">{_esc(label)}</a>'
        for href, label in NAV)
    if user:
        links += '<a class="hide" href="/connect">Zerodha</a><a class="hide" href="/logout">Log out</a>'
        cta = '<a class="btn" href="/app">Open the tool</a>'
    else:
        cta = '<a class="btn" href="/login">Log in</a>'

    foot_nav = "".join(f'<li><a href="{href}">{_esc(label)}</a></li>'
                       for href, label in NAV)
    sub = list(NAV) + ([("/connect", "Zerodha"), ("/logout", "Log out")] if user
                       else [("/login", "Log in")])
    sublinks = "".join(
        f'<li><a class="{"on" if href == active else ""}" href="{href}">'
        f'{_esc(label)}</a></li>' for href, label in sub)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
{'<meta name="robots" content="noindex">' if noindex else ''}
<meta name="description" content="{_esc(description or (BRAND + ' — a rule-based decision-support screen for Nifty, Bank Nifty and Sensex index options. Not advice, not SEBI-registered.'))}">
<title>{_esc(title) if title.startswith(BRAND) else _esc(title) + ' — ' + _esc(BRAND)}</title>
<style>{CSS}</style>
</head><body>

<header><div class="hd">
 <a class="brand" href="/">{LOGO}<div>{_esc(BRAND)}<small>{_esc(TAGLINE)}</small></div></a>
 <nav>{links}{cta}</nav>
</div></header>
<div class="subnav"><ul>{sublinks}</ul></div>

{body}

<footer><div class="wrap">
 <div class="legal">
  <b>Not investment advice.</b> {_esc(BRAND)} is a decision-support screen that
  applies a fixed, published rule set to index data. It is not the output of a
  SEBI-registered Research Analyst or Investment Adviser, no recommendation is
  being made to any person, and no order is ever placed by this software.
  Trading index options can lose you your entire capital. The rule set was
  measured across three years of history and returned a negative expectancy
  after costs — <a href="/results">the figures are here</a>. You are
  responsible for anything you do with what you see.
 </div>
 <div class="fcols">
  <div>
   <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    {LOGO}<b style="color:var(--ink);font-size:15px">{_esc(BRAND)}</b></div>
   <p style="margin:0;max-width:320px">A rule-based second opinion on Nifty,
    Bank Nifty and Sensex index options. It shows its working, it says WAIT most
    of the time, and it places no orders.</p>
  </div>
  <div><h5>The tool</h5><ul>{foot_nav}</ul></div>
  <div><h5>Account</h5><ul>
   <li><a href="/login">Log in</a></li>
   <li><a href="/access">How access works</a></li>
   <li><a href="/disclaimer">Disclaimer</a></li>
  </ul></div>
 </div>
 <div class="foot">
  <div>{_esc(BRAND)} · {_esc(TAGLINE)}</div>
  <div>No orders are ever placed by this software.</div>
 </div>
</div></footer>

<script>
// Reveal-on-scroll. An IntersectionObserver rather than a scroll handler,
// because the browser can do this off the main thread and a scroll listener
// firing on every pixel cannot. Elements are revealed once and unobserved —
// content that re-animates when you scroll back up is a distraction.
//
// THE IMPORTANT PART IS THE FALLBACK. A .reveal element starts at opacity 0,
// so anything that stops the observer firing does not merely skip an
// animation, it leaves the page BLANK. That has to be impossible, so:
// no observer support, reduced motion, a hidden or background tab, or simply
// three seconds passing — any of them shows everything. The animation is
// allowed to fail; the content is not.
(function(){{
  var els = [].slice.call(document.querySelectorAll(".reveal"));
  if(!els.length) return;

  function showAll(){{
    for(var i=0;i<els.length;i++) els[i].classList.add("in");
  }}

  var reduce = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // A tab that is not being looked at may never lay out, and an observer that
  // never fires would leave this reader with an empty page when they return.
  if(reduce || document.hidden || !("IntersectionObserver" in window)){{
    showAll();
    return;
  }}

  try{{
    var io = new IntersectionObserver(function(entries){{
      entries.forEach(function(e){{
        if(e.isIntersecting){{
          e.target.classList.add("in");
          io.unobserve(e.target);
        }}
      }});
    }}, {{rootMargin: "0px 0px -8% 0px", threshold: 0.08}});
    els.forEach(function(el){{ io.observe(el); }});
  }}catch(err){{
    showAll();
    return;
  }}

  // The backstop. Whatever happened above, nothing stays invisible for long.
  setTimeout(showAll, 3000);
}})();
</script>
</body></html>"""


def _phead(title, sub):
    return (f'<div class="wrap"><div class="phead"><h1>{title}</h1>'
            f'<p>{sub}</p></div></div>')


def _next(*pairs):
    links = "".join(f'<a href="{href}"><b>{_esc(kicker)}</b>{_esc(label)}</a>'
                    for href, kicker, label in pairs)
    return f'<div class="next">{links}</div>'


def pipeline_svg():
    """The five steps as a diagram, with the two gates drawn as gates.

    Worth the space because the shape of the thing is the argument: price data
    goes in, most of it is stopped, and only what clears both gates becomes a
    ticket. A list of five bullet points says the same words and none of that.

    Drawn as inline SVG rather than an image so it inherits the page's colours,
    stays sharp at any size, and the labels are real text a screen reader can
    read out in order.
    """
    return """
<figure class="diagram reveal">
 <svg viewBox="0 0 880 220" role="img"
      aria-label="The pipeline: 15-minute candles feed trend, momentum and the
      option chain. A trend-strength gate and a reward-to-risk gate sit in
      front of the output, and most readings are stopped by one of them,
      leaving WAIT. Only what clears both becomes a ticket.">

  <!-- the wire everything sits on -->
  <path class="dg-wire" d="M84 110 H796"/>
  <path class="dg-wire flow" d="M84 110 H796" stroke="#4d94e8" stroke-width="2"/>

  <!-- 1. candles -->
  <g class="popin" style="animation-delay:.1s">
   <rect class="dg-box" x="20" y="76" width="128" height="68" rx="3"/>
   <text class="dg-label" x="84" y="100" text-anchor="middle">CANDLES</text>
   <text class="dg-sub" x="84" y="118" text-anchor="middle">15-minute bars</text>
   <text class="dg-sub" x="84" y="132" text-anchor="middle">pre-open dropped</text>
  </g>

  <!-- 2. the three readings, stacked -->
  <g class="popin" style="animation-delay:.25s">
   <rect class="dg-box" x="186" y="34" width="132" height="46" rx="3"/>
   <text class="dg-label" x="252" y="54" text-anchor="middle">TREND</text>
   <text class="dg-sub" x="252" y="70" text-anchor="middle">EMA 20 / 50</text>

   <rect class="dg-box" x="186" y="88" width="132" height="46" rx="3"/>
   <text class="dg-label" x="252" y="108" text-anchor="middle">MOMENTUM</text>
   <text class="dg-sub" x="252" y="124" text-anchor="middle">MACD · RSI · VWAP</text>

   <rect class="dg-box" x="186" y="142" width="132" height="46" rx="3"/>
   <text class="dg-label" x="252" y="162" text-anchor="middle">OPTION CHAIN</text>
   <text class="dg-sub" x="252" y="178" text-anchor="middle">PCR · open interest</text>

   <path class="dg-wire" d="M148 110 H170 M170 57 V163 M170 57 H186
                            M170 110 H186 M170 163 H186"/>
  </g>

  <!-- 3. the ADX gate -->
  <g class="popin" style="animation-delay:.4s">
   <path class="dg-wire" d="M318 110 H356"/>
   <rect class="dg-gate" x="356" y="76" width="118" height="68" rx="3"/>
   <text class="dg-label" x="415" y="100" text-anchor="middle"
         style="fill:#f0bf55">ADX GATE</text>
   <text class="dg-sub" x="415" y="118" text-anchor="middle">strength floor</text>
   <text class="dg-sub" x="415" y="132" text-anchor="middle">blocks, not weights</text>
  </g>

  <!-- 4. the reward:risk gate -->
  <g class="popin" style="animation-delay:.55s">
   <path class="dg-wire" d="M474 110 H512"/>
   <rect class="dg-gate" x="512" y="76" width="130" height="68" rx="3"/>
   <text class="dg-label" x="577" y="100" text-anchor="middle"
         style="fill:#f0bf55">REWARD : RISK</text>
   <text class="dg-sub" x="577" y="118" text-anchor="middle">reach from ATR</text>
   <text class="dg-sub" x="577" y="132" text-anchor="middle">below the floor = no</text>
  </g>

  <!-- 5. the two outcomes -->
  <g class="popin" style="animation-delay:.7s">
   <path class="dg-wire" d="M642 110 H690"/>
   <rect class="dg-out" x="690" y="52" width="170" height="46" rx="3"/>
   <text class="dg-label" x="775" y="72" text-anchor="middle"
         style="fill:#3d8b40">TICKET</text>
   <text class="dg-sub" x="775" y="88" text-anchor="middle">strike · T1 T2 T3 · stop</text>

   <rect class="dg-wait" x="690" y="122" width="170" height="46" rx="3"/>
   <text class="dg-label" x="775" y="142" text-anchor="middle">WAIT</text>
   <text class="dg-sub" x="775" y="158" text-anchor="middle">most of most days</text>

   <path class="dg-wire" d="M690 75 H668 V145 H690 M668 110 H642"/>
  </g>
 </svg>
 <figcaption>Two gates sit in front of the output, and they are the point:
  most readings are stopped by one of them.</figcaption>
</figure>
"""


def hero_svg():
    """A small chart that draws itself once, then marks a level and stops.

    It is the only ornament on the page that exists purely to be looked at, so
    it is deliberately small, runs once rather than looping, and is drawn from
    the same shapes the real chart uses — candles, a moving average, a target
    line — rather than an abstract swoosh that could belong to any product.
    """
    return """
<div class="heroart" aria-hidden="true">
 <svg viewBox="0 0 560 150">
  <defs>
   <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#4d94e8" stop-opacity=".16"/>
    <stop offset="1" stop-color="#4d94e8" stop-opacity="0"/>
   </linearGradient>
  </defs>

  <!-- the target line the price is heading for -->
  <line class="fadein" x1="0" y1="38" x2="560" y2="38" stroke="#4caf50"
        stroke-width="1" stroke-dasharray="5 5" style="animation-delay:2s"/>
  <text class="fadein" x="556" y="32" text-anchor="end" font-size="10"
        fill="#4caf50" font-weight="700" style="animation-delay:2.1s">T1</text>

  <path d="M0 116 L60 108 L120 118 L180 96 L240 102 L300 78 L360 84 L420 60
           L480 66 L540 44 L560 40 V150 H0 Z" fill="url(#hg)"
        class="fadein" style="animation-delay:1.4s"/>
  <path class="drawline"
        d="M0 116 L60 108 L120 118 L180 96 L240 102 L300 78 L360 84 L420 60
           L480 66 L540 44 L560 40"
        fill="none" stroke="#4d94e8" stroke-width="2.4"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle class="fadein" cx="540" cy="44" r="4" fill="#4d94e8"
          style="animation-delay:2.2s"/>
 </svg>
</div>
"""


def _figure(slug):
    entry = SHOTS.get(slug)
    if not entry or not shot_path(slug):
        return ""
    _, head, caption = entry
    return (f'<figure class="reveal"><img src="/shot/{slug}.png" alt="{_esc(head)}" loading="lazy">'
            f'<figcaption><b>{_esc(head)}</b>{_esc(caption)}</figcaption></figure>')


# ===========================================================================
# HOME
# ===========================================================================
def _cfg(name, fallback=""):
    """Render a live config value.

    The strategy pages quote real numbers, so they read them out of config
    rather than repeating them in prose. A page that says ADX 20 while the
    server runs ADX 25 is worse than a page that says nothing.
    """
    return _esc(getattr(config, name, fallback))


def _record_figs(record):
    """The measured result — the fixed backtest, plus this server's own closed
    trades when it has any.

    The two are shown separately rather than combined. The backtest is a
    finished measurement over a fixed window; the live count is a running
    sample, and averaging them would quietly let a good week improve a
    three-year result.
    """
    figs = [
        ("8,837", "signals measured, on 15-minute candles"),
        ("3 years", "of history the rule set was run across"),
        ("~1 in 3", "of its targets were actually reached"),
        ("negative", "expectancy once costs are counted"),
    ]
    html = "".join(f'<div class="fig"><div class="n">{_esc(n)}</div>'
                   f'<div class="l">{_esc(l)}</div></div>' for n, l in figs)
    live = ""
    if record and record.get("n"):
        r = record
        live = (f'<p style="font-size:14px">This server has logged '
                f'<b>{r["n"]}</b> closed trades of its own '
                f'({_esc(r.get("first") or "?")} to {_esc(r.get("last") or "?")}): '
                f'T1 reached on {r.get("t1")}%, T2 on {r.get("t2")}%, '
                f'T3 on {r.get("t3")}%, the stop hit on {r.get("sl")}%. '
                f'A few dozen trades is a sample, not a verdict — the '
                f'three-year figures above are the measurement.</p>')
    return f'<div class="figs">{html}</div>{live}'


def home_page(user=None, record=None):
    cta = ('<a class="btn" href="/app">Open the tool</a>' if user
           else '<a class="btn" href="/login">Log in</a>')
    body = f"""
<div class="wrap">
 <div class="hero">
  <div class="eyebrow"><span class="dot"></span>Reads the market · places no orders</div>
  <h1>A second opinion you can argue with
   <span class="thin">{_esc(BRAND)} applies one fixed rule set to Nifty, Bank
   Nifty and Sensex, and shows you every reason it reached its answer.</span></h1>
  <p class="lede">Trend, momentum and option-chain open interest go in. What
   comes out is BUY CE, BUY PE or WAIT — with a strike, three targets, a stop,
   and the working shown in sentences rather than hidden behind a score.</p>
  <div class="cta">{cta}
   <a class="btn ghost" href="/how-it-works">See how it decides</a></div>
  {hero_svg()}
  <div class="shotwrap reveal"><img src="/shot/board.png"
   alt="The {_esc(BRAND)} signal board" width="1570" height="1030"></div>
 </div>

 <section class="first" style="padding-top:70px">
  <p class="kicker">What it is</p>
  <h2>Three indices, one rule set, no discretion</h2>
  <p class="sub">The same checks run on all three. Nothing is tuned per index
   after the fact, because a rule that needed different settings on Sensex than
   on Nifty to look good is a rule that was fitted to the past rather than
   found in it.</p>
  <div class="grid g3">
   <div class="card reveal"><div class="idx">Nifty 50</div>
    <h3>15-minute candles</h3>
    <p>Trend from stacked EMAs, momentum from MACD and RSI, position from VWAP,
     and the strike taken from the nearest expiry's chain.</p></div>
   <div class="card reveal" data-d="1"><div class="idx">Bank Nifty</div>
    <h3>Same checks, wider range</h3>
    <p>Bigger points per move, so targets and stop come out further apart —
     they are derived from ATR, not from a fixed number of points.</p></div>
   <div class="card reveal" data-d="2"><div class="idx">Sensex</div>
    <h3>Same checks again</h3>
    <p>Run identically, which is the only way the three results can be
     compared to each other at a glance.</p></div>
  </div>
 </section>

 <section>
  <p class="kicker">How it works</p>
  <h2>Five steps, in order, every time</h2>
  <p class="sub">There is no model here and nothing is learned. It is a
   sequence of checks with a gate at the end, which is why it can always tell
   you why it said what it said.</p>
  {pipeline_svg()}
  <ol class="steps reveal">
   <li><h3>Fetch the candles</h3><p>15-minute bars from your own Zerodha
    session, with the pre-open auction bar dropped.</p></li>
   <li><h3>Read the trend</h3><p>The {_cfg("EMA_FAST", 20)}- and
    {_cfg("EMA_SLOW", 50)}-EMA have to be stacked the same way, and ADX has to
    clear {_cfg("ADX_TREND_THRESHOLD", 20)}.</p></li>
   <li><h3>Read the momentum</h3><p>MACD histogram, RSI, and VWAP each vote —
    and the votes that disagreed are shown too.</p></li>
   <li><h3>Read the option chain</h3><p>Put-call ratio and where open interest
    sits, which turns a view on the index into a specific strike.</p></li>
   <li><h3>Issue, or refuse to</h3><p>Reward-to-risk is computed from ATR
    first. A setup that does not clear {_cfg("MIN_REACH_TO_RISK", 0.6)} is
    marked <b>not worth it</b> and never becomes a ticket. Most of the day
    ends here.</p></li>
  </ol>
  <p style="margin-top:22px"><a href="/how-it-works">The whole rule set, with
   the numbers it actually runs &rarr;</a></p>
 </section>

 <section>
  <p class="kicker">The screen</p>
  <h2>It shows its working</h2>
  <p class="sub">Every image on this site is rendered straight from the app's
   own canvas, not drawn as a mockup.</p>
  {_figure("chart")}
  <p><a href="/screen">Every screen in the app &rarr;</a></p>
 </section>

 <section>
  <div class="callout warm reveal">
   <p class="kicker">What it measured</p>
   <h2>It has been tested, and the result was not good</h2>
   <p>This is the part a site like this normally leaves out, so it is on the
    home page instead. The rule set was run across three years of 15-minute
    candles — 8,837 signals — and came out roughly <b>break-even before costs
    and negative after</b> brokerage, the option bid-ask and time decay. Its
    targets are reached about <b>a third</b> of the time.</p>
   {_record_figs(record)}
   <p>It is published so it can be checked, not because it is known to work.
    <a href="/results">The full measurement, and what the test did and did not
    include &rarr;</a></p>
  </div>
 </section>

 <section>
  <p class="kicker">Boundaries</p>
  <h2>What it will not do</h2>
  <div class="grid g2 reveal">
   <div><ul class="plain">
    <li><span class="x">&times;</span><span>Place an order. Not now, not with a
     confirmation, not on a schedule.</span></li>
    <li><span class="x">&times;</span><span>Give advice. It is not from a
     SEBI-registered research analyst or investment adviser.</span></li>
    <li><span class="x">&times;</span><span>Hide a losing signal. Closed trades
     go into the log whichever way they went.</span></li>
    <li><span class="x">&times;</span><span>Tune itself to whatever just
     happened. The rules are fixed in config.</span></li>
   </ul></div>
   <div><ul class="plain">
    <li><span class="t">&check;</span><span>Show its working — the votes, the
     dissent, the blocker that stopped a ticket.</span></li>
    <li><span class="t">&check;</span><span>Freeze the levels at entry, so they
     cannot drift while a trade is open.</span></li>
    <li><span class="t">&check;</span><span>Say WAIT, which is what it says for
     most of most days.</span></li>
    <li><span class="t">&check;</span><span>Use your own broker session, so your
     data and your rate limit stay yours.</span></li>
   </ul></div>
  </div>
 </section>

 <section>
  <p class="kicker">Getting in</p>
  <h2>Accounts are created by the owner</h2>
  <p class="sub">There is no public sign-up form. You are given an email and a
   password, you log in, and then you connect your own Zerodha account — which
   Zerodha makes a once-a-morning step rather than a once-ever one.</p>
  <div class="cta" style="justify-content:flex-start">{cta}
   <a class="btn ghost" href="/access">How access works</a></div>
 </section>

 {_next(("/how-it-works", "Next", "How it works"),
        ("/results", "The honest bit", "What it measured"),
        ("/security", "Your data", "Security and privacy"))}
</div>"""
    return shell(f"{BRAND} — {TAGLINE}", body, user=user, active="/",
                 description=(f"{BRAND} — a rule-based decision-support screen for "
                              "Nifty, Bank Nifty and Sensex index options. Shows its "
                              "working, places no orders, and was measured negative "
                              "after costs."))


# ===========================================================================
# HOW IT WORKS
# ===========================================================================
def how_page(user=None, record=None):
    body = _phead("How it works",
        "No model, nothing learned, nothing fitted after the fact. A fixed "
        "sequence of checks with a gate at the end — which is the only reason "
        "it can always tell you why it said what it said.") + f"""
<div class="wrap"><div class="narrow prose">

 <section class="first">
  {pipeline_svg()}
  <h2>1. The candles</h2>
  <p>15-minute bars for the index, pulled under your own Zerodha session. Two
   things happen to them before any indicator sees them.</p>
  <p>The <b>pre-open auction bar is dropped</b>. It is priced on almost no
   volume, and because it is the first bar of the day it stays the newest
   completed bar for the whole of 09:15 to 09:30 — which is exactly when the
   worst signals were firing. An EMA or an ATR fed that bar is measuring an
   auction rather than a market.</p>
  <p>Bars with no usable timestamp are left alone. Dropping data because the
   index arrived as an unexpected type would be a worse failure than the one
   being fixed.</p>
 </section>

 <section>
  <h2>2. Trend, and the gate in front of it</h2>
  <p>Direction comes from two exponential moving averages that have to be
   stacked the same way — price above both, and the fast one above the slow one
   for a long, the mirror for a short. One of the two agreeing is not a
   trend.</p>
  <table class="tbl">
   <tr><th>Setting</th><th>Value</th><th>What it is for</th></tr>
   <tr><td>Fast EMA</td><td><code>{_cfg("EMA_FAST", 20)}</code></td>
    <td>the near-term average price</td></tr>
   <tr><td>Slow EMA</td><td><code>{_cfg("EMA_SLOW", 50)}</code></td>
    <td>the one the fast average has to be on the right side of</td></tr>
   <tr><td>ADX length</td><td><code>{_cfg("ADX_LENGTH", 14)}</code></td>
    <td>how many bars the strength reading covers</td></tr>
   <tr><td>ADX floor</td><td><code>{_cfg("ADX_TREND_THRESHOLD", 20)}</code></td>
    <td>below this the signal is blocked outright, not merely marked
     low-confidence</td></tr>
  </table>
  <p>The ADX floor is a <b>gate, not a weight</b>. A direction with no strength
   behind it is how a range chops you, so a weak reading stops the signal
   instead of quietly lowering a number that still lets it through.</p>
 </section>

 <section>
  <h2>3. Momentum, as votes</h2>
  <p>Three readings vote, and all three are shown individually — including the
   ones that disagreed with the answer. A single blended score would hide
   exactly the situation you most want to see, which is two inputs agreeing
   strongly and one shouting the other way.</p>
  <table class="tbl">
   <tr><th>Input</th><th>Settings</th><th>The question it answers</th></tr>
   <tr><td>MACD</td>
    <td><code>{_cfg("MACD_FAST", 12)}/{_cfg("MACD_SLOW", 26)}/{_cfg("MACD_SIGNAL", 9)}</code></td>
    <td>is force still being <em>added</em>, or is the move merely still
     present?</td></tr>
   <tr><td>RSI</td><td><code>{_cfg("RSI_LENGTH", 14)}</code>, bull above
    <code>{_cfg("RSI_BULL_MIN", 50)}</code>, bear below
    <code>{_cfg("RSI_BEAR_MAX", 50)}</code></td>
    <td>where in its band price sits — with
     <code>{_cfg("RSI_OVERBOUGHT", 75)}</code> and
     <code>{_cfg("RSI_OVERSOLD", 25)}</code> as the extremes where a bounce is
     due</td></tr>
   <tr><td>VWAP</td><td>session</td>
    <td>who has actually had the day, buyers or sellers</td></tr>
  </table>
 </section>

 <section>
  <h2>4. The option chain</h2>
  <p>Everything above is a view on the <em>index</em>. The chain is what turns
   it into an instrument: put-call ratio for the balance of positioning, and
   where open interest actually sits for the strike worth looking at. Without
   this step the output would be a direction with nothing to trade.</p>
  <p>When live chain data is available the tool also tracks the real premium
   (LTP) of that strike and gives it its own three premium-based targets, so
   the number you watch is the one you would actually pay.</p>
 </section>

 <section>
  <h2>5. Targets, stop, and the reward-to-risk gate</h2>
  <p>Nothing here is a round number of points. Targets and the stop are
   multiples of ATR, so they widen on Bank Nifty and tighten on a quiet Nifty
   without anything being re-tuned per index.</p>
  <table class="tbl">
   <tr><th>Level</th><th>Derived from</th><th></th></tr>
   <tr><td>T1 / T2 / T3</td>
    <td><code>{_cfg("TARGET_ATR_MULTS", "[0.75, 1.5, 2.5]")}</code> &times; ATR</td>
    <td>three tiers, not one, because partial exits are how most people
     actually use a signal</td></tr>
   <tr><td>Stop</td>
    <td>clamped to <code>{_cfg("MIN_RISK_ATR_MULT", 0.5)}</code>&ndash;<code>{_cfg("MAX_RISK_ATR_MULT", 2.0)}</code> &times; ATR</td>
    <td>a floor so it is not inside the noise, a ceiling so one trade cannot
     be arbitrarily expensive</td></tr>
   <tr><td>The gate</td>
    <td>reach &divide; risk must clear <code>{_cfg("MIN_REACH_TO_RISK", 0.6)}</code></td>
    <td>below it the setup is marked <b>not worth it</b> and never becomes a
     ticket</td></tr>
  </table>
  <p>ATR length is <code>{_cfg("ATR_LENGTH", 14)}</code>. Reach is estimated
   before anything is issued, which is the point — a gate applied afterwards
   would be a report, not a brake.</p>
 </section>

 <section>
  <h2>6. A signal is not a ticket</h2>
  <p>The screen can show a direction at full confidence and still issue
   nothing, and it names the rule that is holding it rather than asserting a
   generic reason. Five things sit in the gap.</p>
  <table class="tbl">
   <tr><th>Badge</th><th>What is actually happening</th></tr>
   <tr><td>CONFIRMING</td><td>the direction has to hold for
    <code>{_cfg("SIGNAL_CONFIRM_SECONDS", 120)}</code> seconds
    (<code>{_cfg("SIGNAL_CONFIRM_TICKS", 4)}</code> ticks) before anything is
    issued</td></tr>
   <tr><td>COOLDOWN</td><td>the gap since the last ticket has not elapsed —
    currently <code>{_cfg("MIN_MINUTES_BETWEEN_TICKETS", 0)}</code>
    minutes</td></tr>
   <tr><td>POSITION OPEN</td><td>a ticket is already running on this index and
    is left alone</td></tr>
   <tr><td>ALREADY TAKEN</td><td>this index was ticketed in this direction
    today and re-entry is off</td></tr>
   <tr><td>DAY LIMIT</td><td>the daily brake — at most
    <code>{_cfg("MAX_TRADES_PER_DAY", 4)}</code> trades, at most
    <code>{_cfg("MAX_LOTS", 5)}</code> lots</td></tr>
  </table>
  <p>This distinction is worth the space. The screen used to print "a ticket is
   issued when the direction changes" whichever rule was holding, and that
   sentence is true for exactly one of those five rows — the least common one.
   A 100%-confidence signal waiting out a cooldown read as though the tool
   disagreed with a screen full of agreement.</p>
 </section>

 <section>
  <h2>7. Once a ticket exists</h2>
  <p><b>The levels are frozen at entry.</b> Targets and the stop are computed
   once, from the ATR at that moment, and never recomputed while the trade is
   open. A stop that drifts with a moving average is a stop you cannot plan
   around and cannot honestly measure afterwards.</p>
  <p>Auto re-arm has its own floor of
   <code>{_cfg("REARM_MIN_SECONDS", 60)}</code> seconds. It is not a churn
   brake and is not meant to be tuned — it exists because a ticket whose stop
   is already breached would otherwise close and re-arm on the next evaluation,
   several times a second, until the chain refreshed.</p>
 </section>

 <section>
  <div class="callout warm reveal">
   <h3>Reading this page is not the same as it working</h3>
   <p>Every rule above is defensible on its own terms, and the whole of it was
    still measured negative after costs across three years. Coherent and
    profitable are different properties.
    <a href="/results">The measurement &rarr;</a></p>
  </div>
 </section>

 {_next(("/screen", "Next", "The screen, part by part"),
        ("/results", "Then", "What it measured"))}
</div></div>"""
    return shell("How it works", body, user=user, active="/how-it-works",
                 description=("The full rule set behind NBS Signal Tool: EMA stacking, "
                              "the ADX gate, MACD/RSI/VWAP votes, the option chain, "
                              "ATR-derived targets and the reward-to-risk gate."))


# ===========================================================================
# THE SCREEN
# ===========================================================================
def screen_page(user=None, record=None):
    body = _phead("The screen",
        "Every image below is a PNG rendered straight from the app's own "
        "canvas — the same code path that draws the window on a Mac. Nothing "
        "here is a mockup of a screen that does not exist.") + f"""
<div class="wrap">
 <section class="first">
  <div class="narrow prose" style="margin-bottom:44px">
   <p>The whole window is painted on one canvas. There is not a single native
    button, tab or checkbox in the main view, and that is deliberate: the
    operating system's own widgets look different on macOS, Windows and Linux
    and cannot do gradients or rounded corners, so the layout would have
    drifted apart on each machine. Painting it means one appearance
    everywhere — and it means the screen can be rendered to a PNG with no
    display attached, which is how the layout is checked.</p>
  </div>
  {_figure("board")}
  {_figure("chart")}
  {_figure("targets")}
  {_figure("waiting")}
  {_figure("dark")}
  {_figure("wide")}
 </section>

 <section>
  <div class="narrow prose">
   <h2>The left rail</h2>
   <p>Five icons, each a page rather than a dialog. The Market Map used to open
    in its own window; it is a page now, which gives it the whole area instead
    of a third of it.</p>
   <table class="tbl">
    <tr><th>Icon</th><th>Page</th></tr>
    <tr><td>Pulse</td><td>the signal board — the Signal and Chart tabs</td></tr>
    <tr><td>Grid</td><td>Market Map, the sector heat map, full width</td></tr>
    <tr><td>Bars</td><td>Your trades — the summary of everything closed</td></tr>
    <tr><td>Bell</td><td>a switch, not a destination: popup alerts on or off</td></tr>
    <tr><td>Gear</td><td>Settings — token state, where credentials live, which
     files get written</td></tr>
   </table>
   <p>The icons are spaced by solving for the room between the logo and the
    theme toggle rather than by a fixed pitch, so the rail stays clear of the
    bottom controls at every window size the app allows.</p>

   <h2 style="margin-top:44px">Reading the signal board</h2>
   <table class="tbl">
    <tr><th>Part</th><th>What it is telling you</th></tr>
    <tr><td>Signal ticket</td><td>the call itself — BUY CE, BUY PE, or nothing.
     <code>OPEN</code> means it is live and tracked; <code>PREVIEW</code> means
     a direction exists but no ticket was issued; <code>DEMO</code> means the
     figures are not live data.</td></tr>
    <tr><td>Entry / Now</td><td>the premium at issue, and the premium now. The
     rupee figure beside them is that difference across your chosen lots.</td></tr>
    <tr><td>Reward : risk</td><td>the ratio computed at entry from ATR. Below
     the floor it never became a ticket at all.</td></tr>
    <tr><td>T1 / T2 / T3</td><td>the three targets, each with how far price has
     travelled toward it — a check mark and a timestamp once reached.</td></tr>
    <tr><td>Stop</td><td>with the same progress bar, which is the one you
     actually want to be watching.</td></tr>
    <tr><td>Confidence</td><td>the dial, with every input that fed it listed
     underneath. A dash means that input abstained and was ignored rather than
     counted as neutral.</td></tr>
    <tr><td>Market trend</td><td>the plain-English label — RANGE-BOUND /
     CHOPPY, MODERATE DOWNTREND — with the ADX reading and how far price has
     actually displaced in ATR terms.</td></tr>
    <tr><td>Session</td><td>everything closed today, in order, with the running
     total split into booked and open.</td></tr>
   </table>
  </div>
 </section>

 {_next(("/results", "Next", "What it measured"),
        ("/how-it-works", "Back", "How it works"))}
</div>"""
    return shell("The screen", body, user=user, active="/screen",
                 description=("Every screen in NBS Signal Tool, rendered from the "
                              "app's own canvas: the signal board, the chart tab, "
                              "targets, the quiet days, and both themes."))


# ===========================================================================
# RESULTS
# ===========================================================================
def results_page(user=None, record=None):
    body = _phead("What it measured",
        "The rule set on this site has been backtested. The result was not "
        "good, and it is on its own page in the main navigation rather than "
        "in small type at the bottom of another one.") + f"""
<div class="wrap"><div class="narrow prose">

 <section class="first">
  <div class="callout warm reveal">
   <h2 style="margin-top:0">Roughly break-even before costs. Negative after.</h2>
   <p>Across three years of 15-minute candles and 8,837 signals, the rule set
    described on this site measured approximately break-even <em>before</em>
    trading costs, and <b>negative once brokerage, the option bid-ask spread
    and time decay are counted</b>. Its targets are reached about a third of
    the time.</p>
   {_record_figs(record)}
  </div>
 </section>

 <section>
  <h2>What the test did</h2>
  <ul>
   <li>Ran the same <code>signal_engine</code> the app runs — not a
    reimplementation of it, which is the usual way a backtest and a live tool
    quietly stop agreeing.</li>
   <li>Used 15-minute candles across all three indices, over three years.</li>
   <li>Applied the same gates: the ADX floor, the reward-to-risk floor, the
    confirmation window, the daily limits.</li>
   <li>Counted a trade as closed when a target or the stop was reached, exactly
    as the live tool logs it.</li>
  </ul>

  <h2>What the test could not do</h2>
  <ul>
   <li><b>Fill you at the price on the screen.</b> Index options move in ticks
    and spreads, and the spread is where a marginal edge goes to die. This is
    the single biggest reason the before-costs and after-costs numbers differ
    so much.</li>
   <li><b>Know your slippage.</b> Lot size, time of day and how far out of the
    money the strike sits all change it, and none of them are constant.</li>
   <li><b>Model your own behaviour.</b> A backtest takes every signal. Nobody
    does. Whether that helps or hurts is not something the test can say.</li>
   <li><b>Predict a regime it never saw.</b> Three years is three years of
    particular markets, not of all markets.</li>
  </ul>
 </section>

 <section>
  <h2>Why publish it anyway</h2>
  <p>Because a rule set you can inspect and measure is worth more than a tip
   you cannot. Everything on <a href="/how-it-works">the how-it-works page</a>
   is checkable line by line, the code is in one place with the reasoning
   written beside it, and this page exists so that the measurement is as easy
   to find as the screenshots.</p>
  <p>The useful version of this tool is as a <b>second opinion you can
   interrogate</b> — a fast, consistent read of what the indicators currently
   say, and an explicit statement when a setup does not clear its own bar. It
   is good at that. It is not good at making money, and it has been measured
   saying so.</p>
  <p>Treat everything here as something to <b>examine</b>, never as something
   to act on. Options can lose their entire value.</p>
 </section>

 <section>
  <h2>How the live record is kept</h2>
  <p>Every ticket this server issues is written to a trade file when it closes,
   whichever way it went, with the targets and stop that were frozen at entry.
   The summary above is computed from that file — there is no separate
   curated list, and nothing is excluded for having been a bad day.</p>
  <p>A live record of a few dozen trades is a sample. It is shown because
   hiding it would be worse, not because it settles anything; the three-year
   figures are the measurement.</p>
 </section>

 {_next(("/security", "Next", "Security and privacy"),
        ("/disclaimer", "Also", "The full disclaimer"))}
</div></div>"""
    return shell("What it measured", body, user=user, active="/results",
                 description=("The backtest behind NBS Signal Tool: 8,837 signals over "
                              "three years, roughly break-even before costs and "
                              "negative after them."))


# ===========================================================================
# SECURITY
# ===========================================================================
def security_page(user=None, record=None):
    https = (config.WEB_PUBLIC_URL or "").startswith("https://")
    https_note = ("" if https else """
   <div class="callout warm" style="margin:26px 0">
    <h3 style="margin-top:0">This server is not currently on HTTPS</h3>
    <p>Everything below about hashing and tokens is still true, but without
     TLS the password you type travels in the clear on the way here. Do not
     use a password you use anywhere else until that is fixed, and do not use
     this over public wifi.</p></div>""")
    body = _phead("Security and privacy",
        "A password file is the one part of a project like this that can hurt "
        "people who are not its author. People reuse passwords, and a broker "
        "token can do more than read. So this page says exactly what is "
        "stored and what never leaves the server.") + f"""
<div class="wrap"><div class="narrow prose">

 <section class="first">
  {https_note}
  <h2>Your password</h2>
  <ul>
   <li>Stored as <b>PBKDF2-HMAC-SHA256 at 600,000 iterations</b> with a
    16-byte random salt per account. Slow on purpose — that is the entire
    point of a password hash.</li>
   <li>Compared with a constant-time comparison, so a timing difference cannot
    be used to guess it a byte at a time.</li>
   <li>Login says <em>the same thing</em> for "no such account" and "wrong
    password", and takes the same time either way. Different messages let
    anyone harvest a list of the real users of a site.</li>
   <li>Failed attempts are rate-limited per email <em>and</em> per IP address.</li>
   <li>Minimum length {accounts.MIN_PASSWORD} characters, because length
    beats complexity rules. A short phrase is stronger than a short
    scramble.</li>
  </ul>
  <p>There is <b>no email-based password reset</b>. That needs a mail service,
   and a half-built reset flow is a way in rather than a feature. Until one
   exists, a locked-out account is reset by the owner, by hand — which also
   signs out every session that account had.</p>
 </section>

 <section>
  <h2>Your session</h2>
  <ul>
   <li>The session token is 32 random bytes, and only its <b>hash</b> is
    stored. Someone who reads the user file still cannot impersonate a
    logged-in user with it.</li>
   <li>The cookie is <code>HttpOnly</code>, so no script can read it, and
    <code>SameSite=Lax</code>, so it is not sent from another site. Over HTTPS
    it is also marked <code>Secure</code>.</li>
   <li>Sessions expire after two weeks, and expired ones are pruned rather
    than left lying in the file.</li>
   <li>Changing a password or disabling an account kills every session that
    account had, immediately.</li>
  </ul>
 </section>

 <section>
  <h2>Your Zerodha connection</h2>
  <p>This is the part that deserves the most care, because a Kite access token
   can read positions and place orders.</p>
  <ul>
   <li><b>This tool places no orders.</b> There is no order code in it at all.
    The token could, which is exactly why it is treated as a credential rather
    than as a setting.</li>
   <li>It is stored only in the server's user file — the same file as the
    password hashes, written with owner-only permissions.</li>
   <li>It is <b>never rendered into a page, never logged, and never sent to a
    browser.</b> The connect screen shows your Zerodha user id and the time you
    connected; it does not show the token.</li>
   <li>It is deleted with your account. Token and user live in the same record
    specifically so that one cannot outlive the other.</li>
   <li>You can disconnect at any time from <a href="/connect">the connect
    page</a>, and revoke this app from Zerodha's own side independently.</li>
   <li>It dies every morning regardless. Zerodha clears every access token
    around 07:30 IST, whenever it was issued.</li>
  </ul>
  <p>Your session is used only for your own data. Signals are computed under
   your token, on a feed that starts when you open the page and stops a few
   minutes after you close it — so nobody else's activity touches your rate
   limit, and yours does not touch theirs.</p>
 </section>

 <section>
  <h2>What is stored, in full</h2>
  <table class="tbl">
   <tr><th>Item</th><th>Where</th><th>Note</th></tr>
   <tr><td>Email</td><td>user file</td><td>your identifier, nothing else</td></tr>
   <tr><td>Password hash</td><td>user file</td><td>PBKDF2, salted, never
    reversible</td></tr>
   <tr><td>Session hashes</td><td>user file</td><td>expire after two
    weeks</td></tr>
   <tr><td>Kite access token</td><td>user file</td><td>dies each morning;
    deleted with the account</td></tr>
   <tr><td>Kite user id</td><td>user file</td><td>so the connect page can say
    which Zerodha login this is</td></tr>
   <tr><td>Created / last login</td><td>user file</td><td>so the owner can see
    a dormant account</td></tr>
   <tr><td>Closed trades</td><td>trade file</td><td>the tickets this server
    issued, not tied to a person</td></tr>
  </table>
  <p>There is no analytics, no third-party script, and nothing is loaded from
   another domain — the pages you are reading declare a content security policy
   that forbids it. No payment details are collected anywhere, because nothing
   here is sold.</p>
 </section>

 <section>
  <h2>What the owner can do</h2>
  <p>Being honest about this matters more than sounding reassuring. Whoever
   runs this server can create accounts, disable them, delete them, and set a
   new password on one — that last one being how a locked-out user gets back
   in, since there is no email reset. They can also read the file those things
   live in.</p>
  <p>They cannot read your password, because it is not stored. They can, in
   principle, read your stored Kite token off the server, which is the honest
   reason to treat "who runs this server" as a question worth asking before
   connecting a broker account to it — here or anywhere else.</p>
 </section>

 {_next(("/access", "Next", "How access works"),
        ("/faq", "Then", "Questions"))}
</div></div>"""
    return shell("Security and privacy", body, user=user, active="/security",
                 description=("How NBS Signal Tool stores passwords, sessions and "
                              "Zerodha tokens — and what never leaves the server."))


# ===========================================================================
# ACCESS
# ===========================================================================
def access_page(user=None, record=None):
    cta = ('<a class="btn" href="/app">Open the tool</a>' if user
           else '<a class="btn" href="/login">Log in</a>')
    open_signup = bool(config.WEB_ALLOW_SIGNUP)
    body = _phead("How access works",
        "Accounts here are created by the owner, not by signing up. It is a "
        "smaller, slower door on purpose." if not open_signup else
        "This server currently allows visitors to create their own "
        "accounts.") + f"""
<div class="wrap"><div class="narrow prose">

 <section class="first">
  <ol class="steps reveal">
   <li><h3>The owner creates your account</h3>
    <p>You are given an email and a password.
     {"There is no public sign-up form on this server — if you should have access and do not, ask the person running it."
      if not open_signup else
      "This server also has open registration, so you can create one yourself."}</p>
    <p>There is no email-based reset either, so a forgotten password is reset
     by the owner by hand — which signs out every session that account
     had.</p></li>
   <li><h3>You log in</h3>
    <p>Your password becomes a PBKDF2-SHA256 hash at 600,000 iterations with
     its own salt, and your session becomes a random token of which only the
     hash is kept. <a href="/security">The details are on the security
     page.</a></p></li>
   <li><h3>You connect your own Zerodha account</h3>
    <p>One click, Zerodha's own login page, and the session that comes back is
     filed against your account and used only for your data. This site never
     sees your Zerodha password — you type it on Zerodha's page, not on
     this one.</p>
    <p>Zerodha clears every access token each morning around 07:30 IST
     regardless of when it was issued. That makes this a once-a-morning step
     rather than a once-ever one. Their rule, not this tool's.</p></li>
   <li><h3>The signals compute under your session</h3>
    <p>Your feed starts when you open the tool and stops a few minutes after
     you close it. Nobody else's activity touches your rate limit, and yours
     does not touch theirs.</p></li>
  </ol>
 </section>

 <section>
  <h2>What you need before you start</h2>
  <table class="tbl">
   <tr><th>You need</th><th>Why</th></tr>
   <tr><td>An account here</td><td>created by the owner; the login gates
    everything except these public pages</td></tr>
   <tr><td>A Zerodha account</td><td>the candles and the option chain are read
    under your own session</td></tr>
   <tr><td>Kite Connect access</td><td>the API side of Zerodha, which they
    charge for separately — the historical-candle permission is what this tool
    reads</td></tr>
   <tr><td>A browser</td><td>that is all; there is nothing to install to use
    the website</td></tr>
  </table>
  <p class="mut" style="font-size:14px">Zerodha's own pricing and permissions
   are between you and Zerodha, and they change. Check their current terms
   rather than taking a number from this page.</p>
 </section>

 <section>
  <h2>Losing access</h2>
  <ul>
   <li><b>Disconnect Zerodha</b> at any time from the connect page, or revoke
    this app from Zerodha's side. The account here survives; the feed
    stops.</li>
   <li><b>A disabled account</b> is signed out immediately and cannot log back
    in.</li>
   <li><b>A deleted account</b> takes its stored Kite token with it, in the
    same write. That is the whole reason they live in one record.</li>
  </ul>
 </section>

 <section>
  <div class="cta" style="justify-content:flex-start">{cta}
   <a class="btn ghost" href="/security">Security and privacy</a></div>
 </section>

 {_next(("/faq", "Next", "Questions"),
        ("/disclaimer", "Then", "The full disclaimer"))}
</div></div>"""
    return shell("How access works", body, user=user, active="/access",
                 description=("How accounts and the per-user Zerodha connection work "
                              "on NBS Signal Tool."))


# ===========================================================================
# FAQ
# ===========================================================================
FAQ = [
    ("Does it place trades?",
     "No. There is no order-placing code in this tool at all. The Zerodha token "
     "it holds could place one, which is exactly why that token is treated as a "
     "credential and never leaves the server. What the tool does is read candles "
     "and the option chain, and draw a screen."),
    ("Is this SEBI-registered investment advice?",
     "No, on both counts — it is not advice, and it is not registered. India "
     "regulates investment advice, and publishing buy/sell calls can fall under "
     "SEBI's Research Analyst and Investment Adviser rules. A disclaimer is not "
     "a licence, which is one more reason nothing here should be acted on."),
    ("Why do I have to connect Zerodha every morning?",
     "Zerodha's rule, not this tool's. They clear every Kite access token each "
     "morning around 07:30 IST regardless of when it was issued. Connect once "
     "after that and the feed stays up for the rest of the session."),
    ("Why my own Zerodha account, and not the site owner's?",
     "Because your broker relationship is yours. Serving everyone from one "
     "session would make one person's rate limit everybody's ceiling, would put "
     "your data behind somebody else's credentials, and would mean your access "
     "outliving your account here."),
    ("Does this site ever see my Zerodha password?",
     "No. You type it on Zerodha's own login page. What comes back to this "
     "server is an access token, which is stored against your account and "
     "expires the next morning."),
    ("You said it tested negative. Why would I use it?",
     "As a second opinion you can interrogate, not as a source of trades. It is "
     "good at giving a fast, consistent read of what the indicators currently "
     "say, and at stating plainly when a setup does not clear its own bar. It is "
     "not good at making money, and it has been measured saying so."),
    ("Why does it say WAIT so much?",
     "Because most of most days does not contain a setup that clears the ADX "
     "floor and the reward-to-risk floor at the same time. A tool that found a "
     "trade every hour would be telling you about its own eagerness rather than "
     "about the market."),
    ("Can I see the rules?",
     "Yes. They are in the code, in one file, with the reasoning written beside "
     "them, and the numbers on the how-it-works page are read live out of that "
     "config rather than retyped. Nothing about the strategy is hidden in a "
     "binary or behind a subscription."),
    ("Why are the targets three levels instead of one?",
     "Because partial exits are how most people actually use a signal, and "
     "because a single target hides the shape of the move. T1, T2 and T3 are "
     "0.75, 1.5 and 2.5 times ATR — so they widen on Bank Nifty and tighten on "
     "a quiet Nifty without anything being re-tuned."),
    ("Do the levels move once a trade is open?",
     "No, and that is deliberate. They are frozen at entry from the ATR at that "
     "moment. A stop that drifts with a moving average is one you cannot plan "
     "around and cannot honestly measure afterwards."),
    ("Is there a mobile app?",
     "No. This website works on a phone, and the desktop app is a separate "
     "program you run on a Mac, Windows or Linux machine. Both run the same "
     "signal engine — there is no second implementation of the strategy to "
     "drift out of sync with the first."),
    ("What happens if I forget my password?",
     "The owner resets it by hand. There is no email-based reset, because that "
     "needs a mail service and a half-built reset flow is a way in rather than "
     "a feature. A reset also signs out every session that account had."),
    ("Can I get an account?",
     "Accounts are created by whoever runs this server. There is no public "
     "sign-up form here, so ask them."),
]


def faq_page(user=None, record=None):
    items = "".join(f'<details><summary>{_esc(q)}</summary><p>{_esc(a)}</p></details>'
                    for q, a in FAQ)
    body = _phead("Questions",
        "The ones worth asking, including the uncomfortable one.") + f"""
<div class="wrap"><div class="narrow">
 <section class="first">{items}</section>
 <div class="prose" style="margin-top:36px">
  <p class="mut" style="font-size:14.5px">If your question is not here, it is
   probably answered on <a href="/how-it-works">how it works</a>,
   <a href="/results">what it measured</a> or
   <a href="/security">security</a>.</p>
 </div>
 {_next(("/disclaimer", "Next", "The full disclaimer"),
        ("/access", "Back", "How access works"))}
</div></div>"""
    return shell("Questions", body, user=user, active="/faq",
                 description="Common questions about NBS Signal Tool.")


# ===========================================================================
# DISCLAIMER
# ===========================================================================
def disclaimer_page(user=None, record=None):
    body = _phead("Disclaimer",
        "In full, on its own page, in the same type size as everything "
        "else.") + f"""
<div class="wrap"><div class="narrow prose">
 <section class="first">
  <div class="callout warm reveal">
   <h2 style="margin-top:0">This is not investment advice</h2>
   <p>{_esc(BRAND)} is a decision-support screen. It applies a fixed,
    published rule set to index data and displays the result. It is <b>not</b>
    the output of a SEBI-registered Research Analyst or Investment Adviser,
    <b>no</b> recommendation is being made to any person, and <b>no order is
    ever placed</b> by this software.</p>
  </div>

  <h2>The regulatory position</h2>
  <p>India regulates investment advice. Publishing buy and sell calls can fall
   under SEBI's Research Analyst Regulations or Investment Adviser Regulations,
   and that becomes considerably more likely once money changes hands anywhere
   in the picture. Nothing here is offered for sale, no fee is charged for
   access, and no personalised recommendation is made to anyone.</p>
  <p>A disclaimer is not a licence. This paragraph does not make the tool
   compliant with anything; it states what the tool is, so that nobody mistakes
   it for something regulated and supervised.</p>

  <h2>The risk</h2>
  <ul>
   <li>Index options are leveraged instruments and <b>can lose their entire
    value</b>, including on a day when your view of the direction was
    correct.</li>
   <li>Time decay works against a buyer every single day, including the days
    nothing happens.</li>
   <li>The bid-ask spread is a real cost on entry and again on exit, and it is
    widest in exactly the strikes that look cheapest.</li>
   <li>Past behaviour of any rule set, measured or not, does not indicate its
    future behaviour.</li>
  </ul>

  <h2>What was measured</h2>
  <p>The rule set on this site was backtested across three years of 15-minute
   candles and 8,837 signals. It came out roughly break-even before trading
   costs and <b>negative after</b> brokerage, the option bid-ask and time
   decay. Its targets are reached about a third of the time.
   <a href="/results">The full account is here.</a> It is published so that it
   can be checked, not because it is known to work.</p>

  <h2>Data</h2>
  <p>Market data is read under your own broker session and is subject to your
   broker's terms, accuracy and availability. Feeds are delayed, gapped or
   simply wrong sometimes. The tool marks a stale or missing feed rather than
   quietly showing you the last thing it knew, but no display can be more
   correct than the data behind it.</p>

  <h2>Your responsibility</h2>
  <p>You are responsible for anything you do with what you see here. If you are
   unsure whether a financial decision is suitable for you, consult a
   SEBI-registered investment adviser — which this is not.</p>
 </section>
 {_next(("/results", "See also", "What it measured"),
        ("/security", "See also", "Security and privacy"))}
</div></div>"""
    return shell("Disclaimer", body, user=user, active="",
                 description=("Risk, the SEBI position, and what was measured — the "
                              "full disclaimer for NBS Signal Tool."))


# ===========================================================================
# LOGIN AND CONNECT
# ===========================================================================
def login_page(error=None, email="", notice=None):
    """The login form.

    No sign-up link unless this server actually allows sign-up. A "create an
    account" link that leads to a 404 is worse than no link at all: it tells a
    stranger there is a door, and then makes them rattle it.
    """
    insecure = ""
    if not (config.WEB_PUBLIC_URL or "").startswith("https://"):
        insecure = ('<div class="warnbox"><b>This page is not using HTTPS.</b> '
                    'Do not use a password here that you use anywhere else.</div>')
    alt = ('<div class="alt"><a href="/signup">Create an account</a></div>'
           if config.WEB_ALLOW_SIGNUP else
           '<div class="alt">Accounts are created by the site owner. There is no '
           'self-service sign-up and no email reset. <a href="/access">How access '
           'works</a></div>')
    body = f"""<div class="wrap"><div class="mid">
 <div class="panel">
  <h1>Log in</h1>
  <p class="sub">To {_esc(BRAND)} — {_esc(TAGLINE)}.</p>
  {f'<div class="ok">{_esc(notice)}</div>' if notice else ''}
  {f'<div class="err">{_esc(error)}</div>' if error else ''}
  {insecure}
  <form method="post" action="/login">
   <label class="f" for="email">Email</label>
   <input id="email" type="email" name="email" value="{_esc(email)}" required
          autocomplete="email" autofocus>
   <label class="f" for="password">Password</label>
   <input id="password" type="password" name="password" required
          autocomplete="current-password">
   <button class="btn wide" type="submit">Log in</button>
  </form>
  {alt}
 </div>
 <p class="hint" style="text-align:center;max-width:430px;margin:20px auto 0">
  This site shows the output of a mechanical rule set. It is not advice, it is
  not SEBI-registered, and it was measured negative after costs —
  <a href="/results">the numbers are here</a>.</p>
</div></div>"""
    return shell("Log in", body, active="", noindex=True)


def signup_page(error=None, email=""):
    """Open registration, when a server chooses to run with it.

    The measured result is stated above the fields rather than linked from
    them. Somebody signing up cannot run the backtest — this form is the only
    place they will ever be made to read it, and leaving it to a link means
    strangers acting on numbers whose expectancy has been measured by someone
    else and not by them.
    """
    insecure = ""
    if not (config.WEB_PUBLIC_URL or "").startswith("https://"):
        insecure = ('<div class="warnbox"><b>This page is not using HTTPS.</b> '
                    'Do not use a password here that you use anywhere else.</div>')
    body = f"""<div class="wrap"><div class="mid">
 <div class="panel">
  <h1>Create an account</h1>
  <p class="sub">For {_esc(BRAND)} — {_esc(TAGLINE)}.</p>
  {f'<div class="err">{_esc(error)}</div>' if error else ''}
  <div class="callout warm" style="padding:18px 20px 15px;margin-bottom:18px">
   <h3 style="font-size:15px">Before you create an account, please read this</h3>
   <p style="font-size:14px">This site shows the output of a mechanical rule
    set applied to Nifty, Bank Nifty and Sensex. It is <b>not advice</b>, and it
    does not come from a SEBI-registered research analyst or investment
    adviser.</p>
   <p style="font-size:14px"><b>It has been tested, and the result was not
    good.</b> Across three years and 8,837 signals on 15-minute candles it
    measured roughly break-even before costs and <b>negative after</b>
    brokerage, the option bid-ask and time decay. Its targets are reached about
    a third of the time.</p>
   <p style="font-size:14px">It is published so it can be checked, not because
    it is known to work. Options can lose their entire value.
    <a href="/results">The full measurement.</a></p>
  </div>
  {insecure}
  <form method="post" action="/signup">
   <label class="f" for="email">Email</label>
   <input id="email" type="email" name="email" value="{_esc(email)}" required
          autocomplete="email">
   <label class="f" for="password">Password</label>
   <input id="password" type="password" name="password" required
          autocomplete="new-password" minlength="{accounts.MIN_PASSWORD}">
   <p class="hint">At least {accounts.MIN_PASSWORD} characters. A short phrase
    beats a short scramble.</p>
   <label style="display:flex;gap:10px;font-size:13.5px;color:var(--ink-2);
                 align-items:flex-start;margin-top:16px">
    <input type="checkbox" name="understood" value="1" required
           style="width:16px;height:16px;flex:none;margin-top:3px;
                  accent-color:var(--accent)">
    <span>I have read the above and understand this rule set tested negative
     after costs.</span></label>
   <button class="btn wide" type="submit">Create account</button>
  </form>
  <div class="alt"><a href="/login">Already have an account? Log in</a></div>
 </div>
</div></div>"""
    return shell("Create an account", body, active="", noindex=True)


def connect_page(user, state, detail, user_id="", since="", app_ok=True,
                 app_why="", error=None, notice=None):
    """The Zerodha connection screen — one button and an honest status line."""
    good = state == "ok"
    label = {"ok": "Connected", "missing": "Not connected",
             "stale": "Needs reconnecting today", "expired": "Session expired",
             "unknown": "Unverified"}.get(state, state)
    pill = (f'<span class="state {"on" if good else "off"}"><span class="d"></span>'
            f'{_esc(label)}</span>')

    rows = ""
    if user_id or since:
        rows = ('<div class="rows">'
                + (f'<div class="row"><b>Zerodha user</b><span>{_esc(user_id)}</span></div>'
                   if user_id else "")
                + (f'<div class="row"><b>Connected at</b><span>{_esc(since)}</span></div>'
                   if since else "")
                + f'<div class="row"><b>Account</b><span>{_esc(user)}</span></div>'
                + "</div>")

    if not app_ok:
        action = f'<div class="warnbox">{_esc(app_why)}</div>'
    else:
        action = (f'<form method="post" action="/connect">'
                  f'<button class="btn wide" name="action" value="start">'
                  f'{"Reconnect to Zerodha" if state != "missing" else "Connect to Zerodha"}'
                  f'</button></form>')
        if state != "missing":
            action += ('<form method="post" action="/connect" '
                       'style="text-align:center;margin-top:14px">'
                       '<button class="link" name="action" value="disconnect">'
                       'Disconnect this account</button></form>')

    body = f"""<div class="wrap"><div class="mid">
 <div class="panel">
  <h1>Your Zerodha connection</h1>
  <p class="sub">{pill}</p>
  {f'<div class="err">{_esc(error)}</div>' if error else ''}
  {f'<div class="ok">{_esc(notice)}</div>' if notice else ''}
  <p style="font-size:14.5px;color:var(--ink-2);margin:0">{_esc(detail)}</p>
  {rows}
  {action}
 </div>

 <div class="panel" style="margin-top:16px;box-shadow:none">
  <h1 style="font-size:16px">What this allows, and what it does not</h1>
  <p style="font-size:14px;color:var(--ink-2);margin:10px 0 0">
   Connecting lets this server read index candles and the option chain under
   your own Kite session, which is what the signals are computed from.
   <b>No order is ever placed.</b> The token is stored only on this server, is
   never shown in a page or written to a log, and is deleted with your
   account.</p>
  <p style="font-size:14px;color:var(--ink-2);margin:12px 0 0">
   You type your password on Zerodha's own login page — this site never sees
   it. Zerodha clears every access token each morning around 07:30 IST,
   whenever it was issued, so this is a step you repeat each morning rather
   than one you do once. <a href="/security">More on what is stored.</a></p>
 </div>
 <div class="alt" style="margin-top:20px"><a href="/app">Back to the tool</a></div>
</div></div>"""
    return shell("Zerodha connection", body, user=user, active="", noindex=True)


def result_page(title, message, ok=True, user=None, back="/connect",
                back_label="Back to the connect page"):
    """A plain outcome screen — used when Zerodha sends the browser back."""
    body = f"""<div class="wrap"><div class="mid">
 <div class="panel">
  <h1>{_esc(title)}</h1>
  <div class="{'ok' if ok else 'err'}" style="margin-top:14px">{_esc(message)}</div>
  <a class="btn wide" href="{back}" style="text-align:center">{_esc(back_label)}</a>
 </div>
</div></div>"""
    return shell(title, body, user=user, active="", noindex=True)


# ===========================================================================
# ROUTING TABLE
# ===========================================================================
# web_server.py looks a path up here rather than carrying a chain of ifs for
# pages that are all the same shape: public, GET-only, and rendered from
# nothing but the logged-in user and the track record.
PAGES = {
    "/":             home_page,
    "/how-it-works": how_page,
    "/screen":       screen_page,
    "/results":      results_page,
    "/security":     security_page,
    "/access":       access_page,
    "/faq":          faq_page,
    "/disclaimer":   disclaimer_page,
}
