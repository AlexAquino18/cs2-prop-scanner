# CS2 Prop Line Discrepancy Scanner

Finds CS2 player prop lines that disagree across PrizePicks and Underdog
Fantasy (and Chalkboard, once a public board API exists).

## How it works

```
sources/prizepicks.py   -> PrizePicks partner API (public, no key, league_id=265)
sources/underdog.py     -> Underdog pick'em catalog (public, no key)
sources/chalkboard.py   -> stub — no public CS2 board API yet
        |
        v
normalize.py             -> collapses "ZywOo" / "Zywoo", "Kills Maps 1-2" /
                             "Kills on Maps 1+2" into the same keys
        |
        v
matching.py               -> groups the same real-world bet across books
        |
        v
scanner.py                -> prints a table, optional --json / --csv export
```

## Setup

```bash
cd cs2_prop_scanner
pip install -r requirements.txt
```

No API keys are required.

## Website (easiest)

```bash
pip install -r requirements.txt
python webapp.py
```

Open http://127.0.0.1:8000 — it pulls PrizePicks and Underdog on startup, then every 15 minutes. Use **Refresh lines** to pull immediately. Line moves are vs each prop's opening line. Matches show Polymarket series-winner odds when a market exists.

Live site: https://cs2-prop-scanner.onrender.com/

Click a player name for recent maps vs the line, or a match title for team map pools. That uses the [BallDontLie CS2 API](https://cs.balldontlie.io/). Put `BALLDONTLIE_API_KEY` in `.env` locally and in the Render env vars. The GOAT trial is 5 requests/minute, so the player DB fills in the background.

## Host it (Render)

1. Push this repo to GitHub.
2. Go to [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**.
3. Connect the GitHub repo. `render.yaml` is already in the project.
4. After deploy, open the `*.onrender.com` URL. Add a custom domain under the service's **Settings**.

Railway works the same way: **New Project** → **Deploy from GitHub repo**. Start command is `python webapp.py`.

The host sets `PORT`; the app binds `0.0.0.0` automatically.

## CLI

```bash
python scanner.py
```

Options:
```bash
python scanner.py --threshold 1.0                    # only report gaps >= 1.0
python scanner.py --sources prizepicks,underdog
python scanner.py --json discrepancies.json --csv discrepancies.csv
```

If Underdog parsing ever comes back empty, run:
```bash
python scanner.py --dump-raw underdog
```
That prints the first 5 parsed CS2 items so you can see the shape.

## Notes

- **PrizePicks**: only standard (non-Goblin/Demon) player lines are compared.
  Team combo props are skipped. If you get zero results, PrizePicks may have
  renumbered CS2 — check `https://partner-api.prizepicks.com/leagues`.
- **Underdog**: alternate (boosted/discounted) lines are skipped so the
  comparison is main-line vs main-line.
- **Chalkboard**: disabled by default. The adapter is wired with the same
  `fetch() -> list[PropLine]` interface as the other sources.
- **Map ranges**: `Maps 1-2`, `Maps 1+2`, `Map 1`, and `M1-2` all collapse
  to the same key.
