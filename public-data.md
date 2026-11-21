# Lineups public data

HoopsMatic Lineups is the one source of five-man and two-man numbers for hoopsmatic.com. Other sections (NBA Rotations first) read these files instead of working the numbers out again. Lineups says how good a group is; Rotations says how and when the coach uses it.

This file is outside `docs/`, so it is not published. The data files are, under `docs/data/public/`:

| File | URL |
|---|---|
| Units of one team-season | `https://hoopsmatic.com/lineups/data/public/units/<1999-00>/<TEAM>.json` |
| Player pages | `https://hoopsmatic.com/lineups/data/public/players.json` |
| Leaderboards | `https://hoopsmatic.com/lineups/data/public/leaderboards.json` |

They are rebuilt by `build/lineup_lab_build.py` in the nightly workflow (21:35 and 09:35 UTC), so they always match the pages built in the same run. Not pages, not in the sitemap. Compact JSON, UTF-8.

## units/&lt;season&gt;/&lt;TEAM&gt;.json

One file per team-season Lineups has. `<season>` is the label (`2026-27`, `2015-16`); `<TEAM>` is the team code of that season, upper case, as in Ramiro Bentes' data and NBA Rotations (`GSW`, `SEA` for the 2005-06 SuperSonics, `NJN`, `CHH`...).

```json
{
  "season": "2015-16",
  "team": "GSW",
  "team_name": "Golden State Warriors",
  "page": "https://hoopsmatic.com/lineups/warriors/2015-16/",
  "through": "2026-11-19",
  "units": [
    {"ids": [101106, 201939, 202691, 203084, 203110], "min": 552, "poss": 1158, "opp_poss": 1151,
     "pts": 1284, "opp_pts": 1128, "games": 49, "net": 12.9,
     "url": "https://hoopsmatic.com/lineups/warriors/2015-16/#l-101106-201939-202691-203084-203110"}
  ]
}
```

- `through`: only in the season in progress, the last game date in the data.
- `units`: every five that Lineups shows on any page for that team and season (best, worst and most-used lists of the team-season page, the season leaders, all-time and team pages, player pages), most minutes first. A five that no page shows is not in the file.
- `ids`: the five NBA player IDs, sorted numerically. Joined with `-` they are the unit key both sections use.
- `min`, `poss`, `opp_poss`, `pts`, `opp_pts`, `games`: season totals for that five, regular season only, rounded exactly as the pages show them (minutes and possessions to the nearest whole number). `games` counts the games in which the five played at least one stretch together.
- `net`: net rating, points scored per 100 possessions minus points allowed per 100 opponent possessions, rounded to one decimal as the pages show it (`0.0` covers -0.05 to +0.05). `null` if a side had no possessions.
- `url`: where the page shows those same numbers: the five's row on its team-season page when it is there, otherwise its row on the first other page that shows it (season leaders, all-time, team, overview, player page). The 30-day list on the overview shows different (window) numbers and is never used.

The page rules are unchanged: no list ranks a five with fewer than 100 possessions; the per-list minimums are on each page.

## players.json

`{"<NBA player ID>": "<full URL of his Lineups player page>"}` for every player with a Lineups page.

```json
{"201939": "https://hoopsmatic.com/lineups/player/stephen-curry-201939/"}
```

## leaderboards.json

`{"<season>": "<URL of that season's five-man net-rating leaderboard>", "all-time": "<URL>"}`. The season page opens on the "Best" tab, ranked by net rating; `all-time` is the best single-season fives since 1996-97 (minimum 1,000 possessions).

```json
{"2026-27": "https://hoopsmatic.com/lineups/season/2026-27/", "all-time": "https://hoopsmatic.com/lineups/all-time/"}
```

## Links the other way

Lineups links to NBA Rotations only when the page exists there, checked against Rotations' own public data by `build/lineup_lab_rotations.py` (`data/rotations/`):

- every player page: "How his coach uses him", `https://hoopsmatic.com/rotations/p/<id>-<slug>/` (the exact path from `https://hoopsmatic.com/rotations/data/search.json`);
- every five shown: "How the coach uses this five", `https://hoopsmatic.com/rotations/u/<ids>-<team lowercase>-<season>/`, when that key is in `https://hoopsmatic.com/rotations/data/units/<YYYY>/<team>.json`.

Rotations unit pages are one JavaScript template served by Rotations' 404 page, so their HTTP status is 404 while the unit renders normally (see the nba-rotations README).
