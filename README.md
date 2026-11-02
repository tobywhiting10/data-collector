# Bussage central stats service

Stores song plays and card payments sent by Home Assistant, reads the Cancer Research UK page total
(including Gift Aid) on a schedule, and publishes a read-only JSON feed for the website (see `API.md`).
HA stays in charge of lights, songs and the card reader.

## Deploy with Portainer
The compose file builds a custom image from `Dockerfile` (song catalogue `songs.yaml` is baked in).
Portainer's web editor cannot build from a pasted compose file alone, so use one of:

1. **Repository stack (easiest):** put this `central/` folder in a git repo (private is fine), then
   Stacks > Add stack > *Repository*, set the compose path to `docker-compose.yml` (and the build context
   is the repo folder containing it, so keep `central/` contents at the repo root or set the path to
   `central/docker-compose.yml`). Enable *Build* if offered.
2. **Prebuilt image:** on any machine with Docker: `docker build -t YOURHUB/bussage-stats:1 .` then
   `docker push ...`, and in the compose file replace `build: .` with `image: YOURHUB/bussage-stats:1`.
   Paste the compose into Portainer's *Web editor*.

Set the stack's **Environment variables** from `.env.example` (at minimum `API_KEY`, a long random string:
`openssl rand -hex 24`). Put your SSL reverse proxy in front of port 8000 and publish at least
`/api/v1/stats`, `/api/v1/songs` and `/health`. The POST endpoints need the key (HA sends it).
Check: `curl https://YOUR-DOMAIN/health`.

Back up the `bussage-data` volume (SQLite database). To change songs, edit `songs.yaml` and redeploy
(rebuild), or bind-mount your own file over `/config/songs.yaml` and call `POST /api/v1/reload`.

## What it counts
* A play = a catalogue song (matched on `file`) playing > 10 s; idle/other playlist items are ignored.
  Listening time is real seconds played (measured by HA). Event IDs make HA retries idempotent.
* Card payments are recorded for information (estimated fees at `FEE_PERCENT`, 90/10 split of the
  amount after fees). They are **not** added to the headline total, because you transfer the money to the CRUK
  page by hand and the page then counts it.
* Headline total = CRUK page "Total raised" + Gift Aid, read hourly from `CRUK_URL`.

## Honest status
* Tested locally: API, auth, idempotency, money maths, catalogue filter, CRUK text parsing on sample text.
* **Not tested:** the Docker/Portainer build, HA package on a live HA, and the CRUK read against the real
  page. My tools could only see the page as text ("Total raised £0.00 + £0.00 Gift Aid"), not its raw
  HTML, and the page might change. If the log shows `did not match CRUK_REGEX`, either set `CRUK_REGEX`
  (applied to the page text; group 1 = raised, optional group 2 = Gift Aid) or enter the total by hand:
  `curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"total":"281.62"}' https://.../api/v1/external/cruk_page`
  (the total including Gift Aid). Scraping a charity page may breach its terms; check them.
* Check CRUK / Fundraising Regulator rules on keeping 10% and on holding donations before passing them on;
  write the policy page accordingly. Not legal advice.

## Adding a song (the only two places)
1. Add one line to `songs.yaml`, redeploy the container.
2. Open `https://stats.YOUR-DOMAIN/api/v1/songs/buttons.yaml`, copy the block for the song's category into the
   dashboard column. (Everything else follows by itself: the stats catalogue, the website feed, and HA's
   song picker `input_select.christmas_songs`, which `ha/bussage_stats.yaml` extends within ~15 minutes
   or on HA restart. It only adds options; remove old ones by hand.)

## Admin page `/dash`
`https://stats.YOUR-DOMAIN/dash`: KPIs, plus forms to enter **money moved from the card reader to CRUK**,
**Buy Me a Coffee** amounts (dated; the "last 2 months" figure sums the last ~2 months) and a manual CRUK total override.
It has **no authentication in the app**: protect the whole `/dash` prefix at your reverse proxy (it covers the
page and `/dash/api/*`). The public feed endpoints (`/api/v1/...`) must stay reachable by your website and HA.
**Settings** (on `/dash`): card split (CRUK % vs project %, applied to card money after fees), card fee % + fixed pence,
Buy Me a Coffee fee % + fixed pence. Saved settings override the environment defaults (`CHARITY_SHARE`, `FEE_PERCENT`,
`FEE_FIXED_PENCE`, `BMC_FEE_PERCENT` default 5, `BMC_FEE_FIXED_PENCE`). Saving re-reads the CRUK page immediately; all totals are
calculated on demand, so they change retroactively for every past payment. The BMC fee is an estimate (the 5% default is a guess:
set it to your real rate). The HA dashboard's "90% / 10%" wording is static text: edit it if you change the split.
Definitions (also in `GET /api/v1/summary` and `donations.summary` in `/api/v1/stats`):
* `total` = CRUK page + (card_charity - moved_to_cruk): everything raised for CRUK, whether or not it has reached the page yet. `goal` (setting, default £1000, env `CRUK_GOAL`) and `goal_percent` are published beside it
* `cruk_only`, `card_total` (gross), `card_charity` (90% after estimated fees), `card_project`
* `amount_to_pay_cruk` = card_charity - moved_to_cruk (what you still owe the CRUK page); `moved_to_cruk`
* `bmc_last_2_months`, `bmc_total` (gross) and `_net` versions after the estimated BMC fee; `project_total` = card_project + bmc_total_net (assumes Buy Me a Coffee money goes to the project)
Buy Me a Coffee arrives by webhook (below); the form on `/dash` is for corrections or anything the webhook missed.

## Buy Me a Coffee webhook
1. Set `BMC_WEBHOOK_SECRET` (stack environment variable). In BMC: Integrations > New webhook,
   URL `https://stats.YOUR-DOMAIN/api/v1/webhooks/bmc`, events: `donation.created`, `donation.refunded`
   (add `extra_purchase.*`, `commission_order.*`, `wishlist_payment.*` if you use those). Copy the webhook's
   signing secret into `BMC_WEBHOOK_SECRET` and redeploy.
2. This path must be reachable by the internet and NOT behind your `/dash` protection; it is secured by the
   HMAC-SHA256 signature (`x-signature-sha256`) instead.
3. Use "Send test event" in BMC: a signed test returns "test event (signature verified)" and is not stored.
Notes: payments in a currency other than `BMC_CURRENCY` (GBP) are ignored (logged); refunds remove the entry;
recurring donations/memberships only send start/update/cancel events, so their renewals are NOT counted
(enter those by hand on `/dash`). Amounts are assumed to be in major units (pounds), per the BMC spec's example;
the spec does not say so outright, so check your first real payment. The gross `amount` is used, not net of BMC fees.

## Admin page tabs
* **Overview** - figures, transfers, Buy Me a Coffee entries, settings.
* **Transaction log** - every card payment (Home Assistant sends each one individually) and every coffee (webhook or manual), with estimated fee/net.
* **Song buttons YAML** - the generated dashboard button YAML with a Copy button.
Settings, transfers, coffees and payments are all stored in the SQLite file on the `/data` volume, so they survive restarts and redeploys as long as that volume/bind mount is kept.

## Hiding, deleting and manual payments
* Transaction log: **hide** keeps a row but excludes it from every total (use for refunds/duplicates); **unhide** restores it.
  **delete** is permanent. Prefer hide for Home Assistant payments: a retry of the same payment would re-add a deleted one
  (a hidden one stays hidden). Deleting a webhook coffee can likewise be re-added if Buy Me a Coffee redelivers it.
* Manual payments (cash in hand): Overview > "Add a manual payment". Counted in `card_total` (and `manual_total`), same CRUK/project split;
  the "transaction fees apply" box decides whether the card fee is deducted for that entry (off by default).
* `generated_at` in the API is the moment the figures were calculated, which happens on every request (not only when songs play).
  `/api/v1/stats` is sent with `Cache-Control: max-age=60`, so a browser or proxy may show a copy up to a minute old.
