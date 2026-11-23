# lineup-lab

NBA Lineups, served at https://hoopsmatic.com/lineups/ (the hoopsmatic-worker proxies this repo's GitHub Pages site, `docs/` on `main`).

Every NBA five-man lineup and duo since 1996-97, ranked by net rating, with the hottest lineups of the last seven days on the front page.

## Pages

- `/lineups/`: hottest lineups this week (refreshed daily in season), all 30 teams, every season, how the numbers work
- `/lineups/all-time/`: best and worst single-season lineups (min 1,000 possessions), most minutes together
- `/lineups/duos/`: best duos of the current season, best single-season duos ever (min 2,500), most minutes together
- `/lineups/season/<1999-00>/`: one season's league leaders
- `/lineups/<team>/`: one franchise, its best lineup every season (team slugs as in Salary Season Finder and Real Standings)
- `/lineups/<team>/<1999-00>/`: one team-season: best, worst and most-used lineups, best duos, record
  - Two-man pairs: the team's 12 most-used players; each cell is the net rating with both on the floor ("-" under 50 minutes together), the diagonal empty, a final "On court" column with each player's own on-court net (on phones, one "On court" line above his partner list), and a "Together vs. apart" view (net together minus the minutes-weighted net of each one without the other). A grid from 768px, a player picker and partner list on phones. Its CSS and JS live inside the block, so no other page changes.

## Data

Ramiro Bentes' public NBA data ([nba_data](https://github.com/ramirobentes/nba_data) release files, loaded by the MIT-licensed [nbadatar](https://github.com/ramirobentes/nbadatar)), built from NBA play-by-play. Regular season only. Credited on every page.

- `data/raw/lineup_stats_regular_<YYYY>.parquet`: every finished season, our own copy, downloaded once.
- `data/current/lineup_games_<YYYY>.csv`: the season in progress, one row per game, team and lineup, with the game date (from the same source's play-by-play file).
- `data/headshots.json`: NBA id to file in nba-headshots' `players/headshots/face/` (transparent cutouts).
- `docs/faces/<id>.webp`: portraits, copied from nba-headshots' `players/headshots/face-fill/` (the one headshot look of every HoopsMatic section: the face fills the circle) by `build/lineup_lab_faces.py`; `data/faces_state.json` records which version of each was copied. Players with no NBA photo get initials.
- `data/heights.json`: NBA id to height in centimetres, from [Wikidata](https://www.wikidata.org) (public domain), refreshed nightly; every row of faces goes shortest to tallest, roughly point guard to center. Players with no height yet (mostly rookies) sit in the middle.
- `data/teams.json`: the 30 franchises (slugs, NBA ids for logos).
- `data/page_hashes.json`: each page's content hash and `<lastmod>`; a date moves only when that page's bytes change.
- `docs/data/ratings-<YYYY>.json`: Global Rating+ and Real Value for every player (names as on those pages), worked out every 3 hours by `build/lineup_lab_ratings.mjs` (`.github/workflows/lineup-lab-ratings.yml`), which runs Alberto's current page code on the same public data files his pages read; the Trade Machine reads it at `/lineups/data/ratings-2027.json`. About 22 KB. `updated` moves only when a number changes; `source` holds a short hash of each page's code.
- `docs/data/minutes-<YYYY>.json`: this season's minutes per game, per team and NBA id (plus each player's total across teams and each team's games), built from `data/current`. The Trade Machine (`hoopsmatic.com/`) reads it at `/lineups/data/minutes-2027.json` for its depth charts. Not a page, not in the sitemap; it changes only when a new game is in.

- `docs/data/public/`: the public unit index other HoopsMatic sections read (every five shown with its numbers and where it is shown, player page URLs, leaderboard URLs), at `https://hoopsmatic.com/lineups/data/public/`. Format in [public-data.md](public-data.md).
- `data/rotations/`: which NBA Rotations player and unit pages exist, read from Rotations' public data on hoopsmatic.com by `build/lineup_lab_rotations.py`. Lineups links a player or a five to Rotations only when it is listed there.

Season numbers follow the source: `2027` is 2026-27.

## How it updates

`.github/workflows/lineup-lab-nightly.yml` runs at 21:35 and 09:35 UTC, on "Run workflow" and on pushes that change the build:

1. Unit tests.
2. `build/lineup_lab_update.py`: saves any finished season not yet in `data/raw`, downloads the current season, refuses a file with fewer games than our copy (the run fails, nothing changes), refreshes the headshot map.
3. `build/lineup_lab_heights.py`: refreshes the heights (keeps the old file if Wikidata is unreachable).
4. `build/lineup_lab_faces.py`: copies new or changed portraits from nba-headshots' face-fill folder.
5. `build/lineup_lab_rotations.py`: refreshes which NBA Rotations pages exist (this season every run, finished seasons weekly; keeps the old lists on any error, never fails the run).
6. `build/lineup_lab_build.py`: aggregates and writes `docs/`, `docs/data/public/` and `docs/sitemap.xml`.
7. Site tests (canonicals on hoopsmatic.com, footer, sitemap, public data).
8. Commits `data/` and `docs/` if anything changed.

No keys, tokens or secrets: the source is public and the workflow uses only the automatic `GITHUB_TOKEN`.

## Tests

```
python -m unittest discover -s tests -v
```
