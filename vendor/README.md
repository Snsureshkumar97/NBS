# vendor/

Third-party code served as-is by the tool (not a package manager download at run
time, so the page loads nothing from another domain - see the content security
policy).

## lightweight-charts.standalone.production.js

TradingView Lightweight Charts(TM) **5.2.1**, Apache License 2.0.

- Source: https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js
  (npm package `lightweight-charts`, TradingView, Inc.; homepage
  https://www.tradingview.com/lightweight-charts/)
- SHA-256: `e21cc5caa0226ef30bd8549c50b9ef926615f2a4ee6b4e486353477a55f598cf`
- License: `LICENSE-lightweight-charts` (Apache-2.0) and `NOTICE-lightweight-charts`
  (from the project's repository, tag v5.2.1). The library's own header carries the
  same notice, and the chart keeps its default TradingView attribution link, as
  the license asks.
- Used for: the contract (strike) chart popup - candles of the option's own
  premium, the open ticket's levels as price lines, and a forming candle moved by
  the contract's streamed price every second. Served at
  `/vendor/lightweight-charts.js` and loaded only when a contract chart is opened.
- To update: download the new version, replace the file, update the version and
  SHA-256 above, and re-run `strike_chart_test.py`, `strike_live_test.py` and
  `chart_pnl_test.py`.
