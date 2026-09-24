# Question for Delta Exchange India support (or API Copilot)

Written 24 Sep 2026. Paste the part between the lines. It is one message, each
question can be answered yes/no/with an error code, and it asks for the exact
behaviour rather than a general description, because the docs and the user guide
do not say. Attach the file `~/delta-testnet-check-<time>.json` that
`delta_testnet_stop_check.py` writes if you have run it: it holds the exact
requests and Delta's exact replies.

**Why it matters:** since 24 Sep 2026 the tool sends a reduce-only stop-loss order to
Delta right after each BTC option buy fills, moves it up when the ticket trails, and
watches the mark itself as the backup. Delta's own order box shows Stop Limit, a Mark
trigger and Reduce Only on options, but nothing has been seen of Delta's *replies* to
these orders (placing, editing, a triggered one), so the answers below settle which
words and error codes the tool must expect. Its raw replies are kept in
`<trade log>.delta.raw.jsonl` after the first live trade.

------------------------------------------------------------------------------

Subject: Stop orders and reduce-only on BTC options - API behaviour (Delta Exchange India)

Hello,

I trade BTC options on Delta Exchange India through the REST API
(`https://api.india.delta.exchange`), buying calls and puts such as
`C-BTC-90000-250926` and holding them long. I would like to protect a long option
position with a stop that rests at Delta, so it still works if my own software is
offline. Your documentation lists stop orders, `trail_amount` and bracket orders
under `POST /v2/orders` without saying whether options are included, and the Order
Types guide describes them only for futures. Could you confirm the actual behaviour
for options (`call_options` / `put_options`)?

1. **Stop orders.** Does `POST /v2/orders` accept `stop_order_type: "stop_loss_order"`
   with `stop_price` and `stop_trigger_method: "mark_price"` on an option product?
   If not, what error code does it return?

2. **Reduce-only stop on a held long option.** With a long position of 1 contract in
   `C-BTC-90000-250926`, is a sell order with `reduce_only: true`,
   `stop_order_type: "stop_loss_order"` and `stop_price` below the mark accepted, and
   does it stay open at Delta until triggered (which order `state` does it show)? Both
   `order_type: "market_order"` and `"limit_order"` with a `limit_price`, please.

3. **Trigger method.** Which `stop_trigger_method` values are allowed on options
   (`mark_price`, `last_traded_price`, `spot_price`)? On a thin option book the last
   price can be far from the mark, so I would want `mark_price`.

4. **Trailing.** Is `trail_amount` accepted on an option stop order, and if so with or
   without a `stop_price`?

5. **Brackets.** Are bracket orders (`/v2/orders/bracket`, `bracket_stop_loss_price`)
   still available on India, or have they been replaced by reduce-only orders (I saw a
   notice saying they were deprecated)? Do they work on options?

6. **When the position is gone.** If the option position is closed by another order
   while a reduce-only stop is resting, is the stop cancelled automatically, or does it
   stay and risk opening a short?

7. **Wallet.** For an India account, what does `GET /v2/wallet/balances` return: a `USD`
   row in dollar terms (INR at the fixed rate of 85), an `INR` row, or both, and which
   one is the balance used as margin when buying options?

8. **Protection while offline.** Is there anything else Delta offers that would protect a
   long option position if my API client goes offline (for example the heartbeat / cancel
   on disconnect feature - does it cancel resting stop orders too)?

A short answer per question, with the exact error code or the fields you would see
in the order response, is all I need. Thank you.

------------------------------------------------------------------------------

## Shorter version for API Copilot

(It answers at most 5 messages per chat and does not run code, so ask 1-2 things at a time.)

> On Delta Exchange India's REST API, can `POST /v2/orders` take `stop_order_type: "stop_loss_order"`
> with `reduce_only: true` on a BTC option product like `C-BTC-90000-250926`, for a long position?
> Show me the request body, and the error code if options are not allowed.

> Does `GET /v2/wallet/balances` for an India account return a USD row, an INR row, or both?

## What we already know (so nobody repeats it)

- Delta India's contracts are quoted and settled in USD; balances are held in INR at a
  fixed 85 per dollar (guides.delta.exchange, "USD-INR Rate").
- API docs: stop orders (mark / last / index trigger), `trail_amount`, and `bracket_*` fields
  exist on `POST /v2/orders`; nothing says options are excluded or included.
- Order Types guide: stop, trailing and bracket orders are shown for futures only.
- A Delta India support article titled "Bracket orders deprecated. Reduce-only orders
  introduced." exists; its page returned 404 when we tried to read it.
- This tool's old note "Delta does not support stop orders on options" had no source and was
  wrong as far as Delta's order box shows (Stop Limit / Mark trigger / Reduce Only on options,
  screenshot 24 Sep 2026). The tool now sends the stop; what is unproven is Delta's API replies.
