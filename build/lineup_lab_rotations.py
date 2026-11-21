"""Which NBA Rotations pages exist, so Lineups links there only on a verified match.

Reads the public Rotations data on hoopsmatic.com (no key, no token):
  https://hoopsmatic.com/rotations/data/search.json
      every Rotations page; player pages are "p/<nba id>-<slug>/"
  https://hoopsmatic.com/rotations/data/units/<YYYY>/<team>.json
      every five-man unit of one team-season; "u" is keyed by the five NBA ids
      sorted numerically and joined by "-". The unit page is
      /rotations/u/<ids>-<team lowercase>-<1999-00>/ (one JS template, see the
      nba-rotations README).

Writes (read by lineup_lab_build.py, which has no network):
  data/rotations/players.json        {nba id: "p/<id>-<slug>/"}
  data/rotations/units/<YYYY>.json   {TEAM: ["id-id-id-id-id", ...]} (sorted)
  data/rotations/state.json          {YYYY: date fetched}

The season in progress is fetched every run. A finished season is fetched when
it has never been fetched, or once its copy is FULL_REFRESH_DAYS old (Rotations
can rebuild old seasons). Any failure keeps the previous files and never fails
the run: the only effect is that a new link waits for the next run.

Season numbers follow the source: 2027 means 2026-27.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
OUTD = os.path.join(DATA, 'rotations')
BASE = 'https://hoopsmatic.com/rotations/'
MIRROR = 'https://jsierrahoopshype.github.io/nba-rotations/'  # the Pages site hoopsmatic.com proxies
UA = {'User-Agent': 'hoopsmatic-lineup-lab (+https://hoopsmatic.com/lineups/)'}
FIRST_SEASON = 1997
FULL_REFRESH_DAYS = 7
MIN_PLAYERS = 2000  # fewer player pages than this in search.json = a broken file, keep ours


def fetch_json(path):
    """Parsed JSON at BASE+path, None on a 404. On any other error (the proxy can
    cache a passing GitHub Pages 503 for a few minutes) the same file is read
    from the Pages site itself; raises if both fail."""
    try:
        return _fetch(BASE + path)
    except Exception:  # noqa: BLE001
        return _fetch(MIRROR + path)


def _fetch(url):
    req = urllib.request.Request(url, headers=UA)
    for k in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(2 * (k + 1))
    raise err


def write_json(p, obj):
    """Write only when the content changes (keeps git quiet)."""
    text = json.dumps(obj, indent=0, sort_keys=True) + '\n'
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            if f.read() == text:
                return False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


def read_json(p, default):
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def lineups_teams(season, cur):
    """Team codes Lineups has for a season (the same codes Rotations uses)."""
    if season == cur:
        p = os.path.join(DATA, 'current', f'lineup_games_{season}.csv')
        return sorted(pd.read_csv(p, usecols=['team'])['team'].unique()) if os.path.exists(p) else []
    p = os.path.join(DATA, 'raw', f'lineup_stats_regular_{season}.parquet')
    return sorted(pd.read_parquet(p, columns=['team'])['team'].unique()) if os.path.exists(p) else []


def players():
    s = fetch_json('data/search.json')
    out = {}
    for row in s or []:
        path = row[1] if len(row) > 1 else ''
        if path.startswith('p/') and path.endswith('/'):
            pid = path[2:].split('-', 1)[0]
            if pid.isdigit() and re.fullmatch(r'p/\d+-[a-z0-9-]+/', path):
                out[pid] = path
    if len(out) < MIN_PLAYERS:
        raise RuntimeError(f'only {len(out)} player pages in search.json')
    return out


def season_units(season, teams):
    out = {}
    for code in teams:
        d = fetch_json(f'data/units/{season}/{code.lower()}.json')
        if d and isinstance(d.get('u'), dict):
            out[code] = sorted(k for k in d['u'] if re.fullmatch(r'\d+(-\d+){4}', k))
    return out


def main():
    with open(os.path.join(DATA, 'current', 'season.txt')) as f:
        cur = int(f.read().strip())
    today = dt.datetime.now(dt.timezone.utc).date()
    try:
        pl = players()
        write_json(os.path.join(OUTD, 'players.json'), pl)
        print(f'rotations: {len(pl)} player pages')
    except Exception as e:  # noqa: BLE001
        print(f'rotations: kept previous player list ({e})')
    sp = os.path.join(OUTD, 'state.json')
    state = read_json(sp, {})
    for s in range(cur, FIRST_SEASON - 1, -1):
        last = state.get(str(s))
        due = (s == cur or last is None or not os.path.exists(os.path.join(OUTD, 'units', f'{s}.json'))
               or (today - dt.date.fromisoformat(last)).days >= FULL_REFRESH_DAYS)
        if not due:
            continue
        teams = lineups_teams(s, cur)
        try:
            u = season_units(s, teams)
        except Exception as e:  # noqa: BLE001
            print(f'rotations: kept previous units for {s} ({e})')
            continue
        if teams and not u:
            print(f'rotations: no unit files for {s}, kept previous')
            continue
        write_json(os.path.join(OUTD, 'units', f'{s}.json'), u)
        # a finished season only records the day it was fetched; the current one
        # is fetched every run and needs no date (keeps state.json quiet)
        if s != cur:
            state[str(s)] = today.isoformat()
        print(f'rotations: {s} {sum(len(v) for v in u.values())} units, {len(u)} teams')
    write_json(sp, state)
    return 0


if __name__ == '__main__':
    sys.exit(main())
