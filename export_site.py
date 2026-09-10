#!/usr/bin/env python3
"""
export_site.py — render the public pages as static files, for Vercel
================================================================================
    python3 export_site.py --app-url https://app.yourdomain.com

Writes ./dist — eight HTML pages, the screenshots, a favicon, robots.txt, a
sitemap and a vercel.json. Push that folder to Vercel (or drag it into the
dashboard) and the marketing site is live on their CDN.

WHY ONLY HALF THE SITE CAN GO THERE
    Vercel runs serverless functions: they start on a request, they are killed
    shortly after it, and they keep nothing between calls. The public pages fit
    that perfectly — they are the same bytes for every visitor.

    The tool does not, and it is worth being precise about why rather than
    just asserting it:

      * It runs a background thread per watching user, polling Zerodha every
        thirty seconds. A serverless function has no thread that outlives the
        request that created it.
      * Ticket state lives in memory and must survive between polls. Freezing
        levels at entry means nothing if the process holding them is discarded
        a second later.
      * users.json and trades.csv are written to disk. A serverless
        filesystem is ephemeral, so accounts and trade history would vanish.
      * Zerodha's redirect URL has to point at one stable address.

    So: the pages here go to Vercel, and the tool runs on something that stays
    up — a small VPS, Render, Railway, Fly. Put the app on its own subdomain,
    pass that address as --app-url, and every "Log in" button on the static
    site points at it.

WHAT GETS BAKED IN
    These pages read live values out of config.py — the EMA lengths, the ADX
    floor, the reward-to-risk gate. Exporting freezes whatever the config said
    at export time. Change the strategy, re-export, or the site will describe
    a rule set the tool is no longer running.
"""

import argparse
import json
import os
import shutil

import nbs_site
from nbs_site import BRAND, _esc, shell

PAGES = {
    "/":             "index.html",
    "/how-it-works": "how-it-works.html",
    "/screen":       "screen.html",
    "/results":      "results.html",
    "/security":     "security.html",
    "/access":       "access.html",
    "/faq":          "faq.html",
    "/disclaimer":   "disclaimer.html",
}

# Everything that only exists on the live server. On the static site these
# have to point at wherever the tool actually runs, or they are dead links
# that lead a visitor to a 404 on the marketing domain.
APP_PATHS = ("/login", "/app", "/connect", "/logout", "/signup", "/admin")


def _rewrite(html, app_url):
    """Point the app links at the app, and leave the content links alone.

    With no app address yet they are left pointing at /login on this domain,
    where a placeholder page explains the situation — see `_placeholder`. A
    dead link to somebody else's domain would be worse than either.
    """
    if not app_url:
        return html
    app_url = app_url.rstrip("/")
    for path in APP_PATHS:
        html = html.replace(f'href="{path}"', f'href="{app_url}{path}"')
    return html


def _placeholder():
    """Stands in for /login until the tool has an address of its own.

    Exporting with no --app-url used to leave every Log in button pointing at
    /login on the marketing domain, where nothing served it — so the one
    action the site asks a visitor to take returned a 404. This says what is
    actually going on instead.
    """
    body = """<div class="wrap"><div class="mid">
 <div class="panel">
  <h1>The tool is not hosted here</h1>
  <p class="sub">These pages are the public half of """ + _esc(BRAND) + """.</p>
  <div class="warnbox"><b>This address serves the description, not the
   application.</b> The tool needs a server that stays running — it holds a
   live connection to Zerodha, keeps ticket state between polls and writes to
   disk, none of which a static site or a serverless function can do.</div>
  <p style="font-size:14.5px;color:var(--ink-2)">Once it has an address, this
   page is replaced by a real login and every button on the site points at it.
   If you were given an account, ask whoever runs it where to sign in.</p>
  <a class="btn wide" href="/" style="text-align:center">Back to the site</a>
 </div>
</div></div>"""
    return shell("Log in", body, active="", noindex=True)


def export(out_dir, app_url, base_url):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "shot"), exist_ok=True)

    for path, filename in PAGES.items():
        page = nbs_site.PAGES[path]
        html = _rewrite(page(user=None, record=None), app_url)
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  {filename}")

    if not app_url:
        with open(os.path.join(out_dir, "login.html"), "w", encoding="utf-8") as f:
            f.write(_placeholder())
        print("  login.html (placeholder — no --app-url given)")

    # Named by content hash, matching what shot_url() puts in the HTML. The
    # /shot/ rule in vercel.json serves these immutable for a day, which is
    # only safe while a URL's bytes never change - replacing a shot in place
    # under that header leaves every browser that saw the old one stuck with
    # it, which is exactly what happened to board.png and chart.png.
    for slug in nbs_site.SHOTS:
        src = nbs_site.shot_path(slug)
        if src:
            name = nbs_site.shot_url(slug).rsplit("/", 1)[-1]
            shutil.copyfile(src, os.path.join(out_dir, "shot", name))
    print(f"  shot/ ({len(os.listdir(os.path.join(out_dir, 'shot')))} images)")

    with open(os.path.join(out_dir, "favicon.svg"), "w", encoding="utf-8") as f:
        f.write(nbs_site.FAVICON)
    with open(os.path.join(out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(nbs_site.robots_txt(base_url))
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(nbs_site.sitemap_xml(base_url))

    # cleanUrls serves /how-it-works from how-it-works.html, so the static
    # site keeps the same addresses the live server uses and nothing that
    # links to it has to know it moved.
    # Anyone who bookmarked /login on this domain, or types it, should end up
    # at the tool rather than at a 404. The pages themselves already link
    # straight there; this only catches the addresses people arrive at by
    # other means.
    redirects = []
    if app_url:
        base = app_url.rstrip("/")
        for path in APP_PATHS:
            redirects.append({"source": path, "destination": base + path,
                              "permanent": False})

    vercel = {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "cleanUrls": True,
        "trailingSlash": False,
        "redirects": redirects,
        "headers": [
            {"source": "/shot/(.*)",
             "headers": [{"key": "Cache-Control",
                          "value": "public, max-age=86400, immutable"}]},
            {"source": "/(.*)",
             "headers": [
                 {"key": "X-Content-Type-Options", "value": "nosniff"},
                 {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
                 # The pages load nothing from anywhere else, so say so.
                 {"key": "Content-Security-Policy",
                  "value": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                           "img-src 'self' data:; script-src 'none'; "
                           "frame-ancestors 'none'"},
             ]},
        ],
    }
    with open(os.path.join(out_dir, "vercel.json"), "w", encoding="utf-8") as f:
        json.dump(vercel, f, indent=2)
    print("  favicon.svg, robots.txt, sitemap.xml, vercel.json")


def main():
    ap = argparse.ArgumentParser(
        description="Render the public pages as a static site for Vercel.")
    ap.add_argument("--out", default="dist", help="output folder (default: dist)")
    ap.add_argument("--app-url", default="",
                    help="where the tool itself runs, e.g. "
                         "https://app.yourdomain.com — every Log in button "
                         "points here")
    ap.add_argument("--base-url", default="",
                    help="the marketing site's own address, for the sitemap")
    args = ap.parse_args()

    print(f"Exporting to {args.out}/")
    export(args.out, args.app_url, args.base_url.rstrip("/"))

    print("\nDone.")
    if not args.app_url:
        print("\n  NOTE: no --app-url was given. Log in links point at /login on")
        print("  this site, which now serves a page explaining that the tool is")
        print("  hosted elsewhere. Re-export with --app-url once it has an")
        print("  address, and the links point straight at it.")
    print("\n  Deploy:  npx vercel deploy --prod " + args.out)
    print("  The tool itself needs a host that stays running — see the")
    print("  docstring at the top of this file for why Vercel cannot be it.")


if __name__ == "__main__":
    main()
