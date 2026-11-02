# Bussage stats feed - for the website

Public, read-only, no key needed, cached for 60 s, CORS limited to your site (`CORS_ORIGINS`).

    GET https://YOUR-DOMAIN/api/v1/stats      everything below in one object
    GET https://YOUR-DOMAIN/api/v1/songs      the catalogue only: {"songs":[...], "files":[...]}

Give a chat the URL and this page. Rules to follow when building counters/graphs:

* Money is in **pounds as numbers** (`281.62`), currency GBP. Format with 2 decimals in the page.
* **Headline "raised" counter = `donations.total_raised_for_charity`.** It is read from the Cancer Research UK
  fundraising page and **includes Gift Aid** (`cancer_research_uk_page.raised` + `.gift_aid` = `.total`).
  Card donations are transferred to that page by hand about weekly, so they appear in it once transferred.
* `donations.card` is information about card payments taken (gross, estimated fees, 90/10 split). Do NOT add
  it to the headline figure (that would double count). Fees are an estimate.
* `cancer_research_uk_page.updated_at` is when the number was last read (`null` = never). If it is old, say
  "as of <date>" rather than presenting it as live.
* `songs` is every catalogue song (including 0 plays); `top_songs` is the top 10 with at least one play.
  Use `title` + `artist` for labels (not `file`). `seconds_listened` is the total for that song.
* `plays_by_day` is the last 30 days, oldest first, zero-filled: use it for a line/bar chart.
* `listening.human` is a ready-made "12h 40m" string; `hours` and `minutes` are the parts.
* A "play" only counts if a song played for more than 10 seconds.

## Summary block (`donations.summary`, also at `GET /api/v1/summary`)
`total` (CRUK + card gross - moved to CRUK), `cruk_only`, `card_total`, `card_charity`, `card_project`,
`card_charity_pending`, `moved_to_cruk`, `bmc_last_2_months`, `bmc_total`, `project_total` (card_project + bmc_total).
These are entered/derived via the admin page; if the user wants a single headline "raised" number, use `summary.total`.

## Example

```json
{
  "generated_at": "2026-11-01T11:25:51+00:00",
  "listening": {
    "total_plays": 3,
    "total_seconds": 810,
    "hours": 0,
    "minutes": 13,
    "human": "0h 13m"
  },
  "songs": [
    {
      "title": "Christmas Wrapping",
      "artist": "The Waitresses",
      "category": "Christmas Classics",
      "file": "Christmas Wrapping - The Waitresses.fseq",
      "plays": 2,
      "visitor_plays": 2,
      "seconds_listened": 580,
      "last_played": "2026-11-01T18:01:00+00:00"
    },
    {
      "title": "Last Christmas",
      "artist": "Wham!",
      "category": "Christmas Classics",
      "file": "wham-Last-Christmas.fseq",
      "plays": 1,
      "visitor_plays": 1,
      "seconds_listened": 230,
      "last_played": "2026-11-01T18:02:00+00:00"
    },
    "...one object per catalogue song..."
  ],
  "top_songs": [
    {
      "title": "Christmas Wrapping",
      "artist": "The Waitresses",
      "category": "Christmas Classics",
      "file": "Christmas Wrapping - The Waitresses.fseq",
      "plays": 2,
      "visitor_plays": 2,
      "seconds_listened": 580,
      "last_played": "2026-11-01T18:01:00+00:00"
    },
    {
      "title": "Last Christmas",
      "artist": "Wham!",
      "category": "Christmas Classics",
      "file": "wham-Last-Christmas.fseq",
      "plays": 1,
      "visitor_plays": 1,
      "seconds_listened": 230,
      "last_played": "2026-11-01T18:02:00+00:00"
    }
  ],
  "categories": [
    {
      "category": "Christmas Classics",
      "plays": 3,
      "seconds_listened": 810
    },
    {
      "category": "Other / Comedy",
      "plays": 0,
      "seconds_listened": 0
    },
    {
      "category": "Christmas Modern",
      "plays": 0,
      "seconds_listened": 0
    }
  ],
  "plays_by_day": [
    {
      "date": "2026-10-30",
      "plays": 0
    },
    {
      "date": "2026-10-31",
      "plays": 0
    },
    {
      "date": "2026-11-01",
      "plays": 3
    },
    "...30 days, oldest first..."
  ],
  "donations": {
    "currency": "GBP",
    "card": {
      "payments": 3,
      "gross": 23.0,
      "estimated_fees": 0.39,
      "net_after_fees": 22.61,
      "to_charity": 20.35,
      "to_project": 2.26,
      "charity_share": 0.9,
      "fee_percent_assumed": 1.69
    },
    "cancer_research_uk_page": {
      "total": 281.62,
      "raised": 225.3,
      "gift_aid": 56.32,
      "updated_at": "2026-11-01T11:25:51+00:00",
      "origin": "scrape"
    },
    "total_raised_for_charity": 281.62
  }
}
```
