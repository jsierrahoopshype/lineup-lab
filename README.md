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

## Data

Ramiro Bentes' public NBA data ([nba_data](https://github.com/ramirobentes/nba_data) release files, loaded by the MIT-licensed [nbadatar](https://github.com/ramirobentes/nbadatar)), built from NBA play-by-play. Regular season only. Credited on every page.

- `data/raw/lineup_stats_regular_<YYYY>.parquet`: every finished season, our own copy, downloaded once.
- `data/current/lineup_games_<YYYY>.csv`: the season in progress, one row per game, team and lineup, with the game date (from the same source's play-by-play file).
- `data/headshots.json`: NBA id to file in nba-headshots' `players/headshots/face/` (transparent cutouts).
- `data/faces_state.json`: per portrait, the source file's hash, how the face was found and the framing version; `docs/faces/<id>.webp` are the portraits, every one framed the same way (`build/lineup_lab_faces.py`). Players with no NBA photo get initials.
- `data/teams.json`: the 30 franchises (slugs, NBA ids for logos).
- `data/page_hashes.json`: each page's content hash and `<lastmod>`; a date moves only when that page's bytes change.

Season numbers follow the source: `2027` is 2026-27.

## How it updates

`.github/workflows/lineup-lab-nightly.yml` runs at 21:35 and 09:35 UTC, on "Run workflow" and on pushes that change the build:

1. Unit tests.
2. `build/lineup_lab_update.py`: saves any finished season not yet in `data/raw`, downloads the current season, refuses a file with fewer games than our copy (the run fails, nothing changes), refreshes the headshot map.
3. `build/lineup_lab_faces.py --fetch`: downloads and frames portraits only for players who have none yet. After changing the framing (`CROP_V`), redo them all locally with `python build/lineup_lab_faces.py <folder with nba-headshots' players/headshots/face>` and commit `docs/faces`.
4. `build/lineup_lab_build.py`: aggregates and writes `docs/` and `docs/sitemap.xml`.
5. Site tests (canonicals on hoopsmatic.com, footer, sitemap).
6. Commits `data/` and `docs/` if anything changed.

No keys, tokens or secrets: the source is public and the workflow uses only the automatic `GITHUB_TOKEN`.

## Tests

```
python -m unittest discover -s tests -v
```
