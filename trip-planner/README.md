# Sun vs. Snow — New Year's Family Trip Planner

A single-file web app for comparing a **beach all-inclusive** (Punta Cana, Cancun, Montego Bay)
against a **snow trip** (NC, TN, WV, PA Poconos, Québec) for a 4-family, ~11-person New Year's
2026–27 vacation departing Boca Raton, FL, on a $3,000–4,000 per-family budget.

## Use it

Open `index.html` in any browser — no build, no server, no dependencies. Everything runs
client-side and your edits persist in `localStorage` (per browser).

## What it does

- **The board** — every destination as a cost meter showing the *highest single-family cost*
  (the number that has to clear budget) against the shaded $3–4k comfort band, sorted
  cheapest-first. Hover any bar for all four families' totals.
- **Trip setup** — dates, nights (4–5), family roster (adults / kids / skiers per family),
  budget ceiling, and how shared costs (house, cars, gas) get split: evenly by family or
  by headcount.
- **Per-destination detail** — researched notes (what works, what to watch out for), a
  per-family cost table, and every assumption (airfare, nightly rates, lift tickets,
  rental vans, food…) editable so the meters track real quotes as you collect them.
  All destinations price as fly-in (the family's preference); mountain trips include
  rental vans from the gateway airport (CLT for Banner Elk/Snowshoe, TYS for Gatlinburg,
  PHL for Camelback, YUL for Tremblant).
- **Deal links** — one-tap links into Expedia, Kayak, Priceline, Google Flights, Southwest,
  Airbnb, and Vrbo **pre-filled with your dates and party size**, plus Costco Travel,
  CheapCaribbean, and book-direct resort sites.

## Why it links out instead of scraping

Expedia, Priceline, and the other OTAs don't offer public pricing APIs, and their terms
prohibit scraping — so no self-hosted app can honestly show their live prices. This planner
does the workable version: baseline prices researched August 2026 (holiday premium included),
live pre-filled searches one tap away, and editable assumptions so real quotes replace
estimates as you shop. Sources and the full research summary are in [RESEARCH.md](RESEARCH.md).

## Notes

- Prices are planning estimates, not quotes. Holiday-week (Dec 30 – Jan 3/4) rates carry a
  30–60% premium over normal winter rates, which is baked into the defaults.
- "Reset all" (top right) restores the researched defaults; each destination also has its own
  "restore defaults" for edited assumptions.
