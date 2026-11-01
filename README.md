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
