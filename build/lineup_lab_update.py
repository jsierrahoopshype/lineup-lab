"""Download step for Lineup Lab. The only part of the build that uses the network.

Source: Ramiro Bentes' public NBA data (https://github.com/ramirobentes/nba_data,
loaded by the MIT-licensed https://github.com/ramirobentes/nbadatar). Files are
GitHub release assets; no account, key or token is involved.

What it keeps in this repo:
  data/raw/lineup_stats_regular_<YYYY>.parquet
      Every FINISHED season, downloaded once and never re-fetched. This is our
      own copy, so the section keeps working if the source ever disappears.
  data/current/lineup_games_<YYYY>.csv
      The season in progress, one row per game, team and five-man unit, with
      the game date. Rewritten each run, sorted, so git stores small diffs.

Safety: a fresh current-season file is used only if it parses and has at least
as many games as the copy we already have. Otherwise the run keeps the old
copy and exits non-zero, so the pages are never rebuilt from a broken file.

Season numbers follow the source: 2027 means 2026-27.
"""
import datetime as dt
import io
import os
import sys
import urllib.error
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'data', 'raw')
CUR = os.path.join(ROOT, 'data', 'current')
RELEASE = 'https://github.com/ramirobentes/nba_data/releases/download'
FIRST_SEASON = 1997
UA = {'User-Agent': 'hoopsmatic-lineup-lab (+https://hoopsmatic.com/lineups/)'}


def fetch(url):
    """Return the bytes at url, or None on a 404. Other errors raise."""
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def lineup_url(season):
    return f'{RELEASE}/lineup_stats/lineup_stats_regular_{season}.parquet'


def pbp_url(season):
    return f'{RELEASE}/pbp/pbp_regular_{season}.parquet'


def raw_path(season):
    return os.path.join(RAW, f'lineup_stats_regular_{season}.parquet')


def current_path(season):
    return os.path.join(CUR, f'lineup_games_{season}.csv')


def lineup_key(s):
    """'203500 Steven Adams, 1628983 Shai ...' -> sorted ids joined by '-'."""
    ids = sorted((p.strip().split(' ', 1)[0] for p in s.split(',')), key=int)
    return '-'.join(ids)


def per_game(df, dates):
    """Stint rows -> one row per game, team and lineup, with the game date."""
    from lineup_lab_build import repair  # same fix for 'NA <name>' rows as the build
    d = df.copy()
    d['lineup_team'] = repair(d['lineup_team'])
    dropped = int(d['lineup_team'].isna().sum())
    if dropped:
        print(f'dropped {dropped} stints without five player ids')
    d = d[d['lineup_team'].notna()]
    d['lineup'] = d['lineup_team'].map(lineup_key)
    g = (d.groupby(['game_id', 'team', 'lineup'], as_index=False)
           .agg(poss=('poss_team', 'sum'), pts=('pts_team', 'sum'),
                opp_poss=('poss_opp', 'sum'), opp_pts=('pts_opp', 'sum'),
                secs=('secs_played', 'sum')))
    g['game_date'] = g['game_id'].map(dates)
    # Names travel with the ids so the build never needs the raw file.
    names = {}
    for s in d['lineup_team'].unique():
        for p in s.split(','):
            pid, nm = p.strip().split(' ', 1)
            names[pid] = nm
    g['names'] = g['lineup'].map(lambda k: '|'.join(names[i] for i in k.split('-')))
    cols = ['game_date', 'game_id', 'team', 'lineup', 'names', 'poss', 'pts', 'opp_poss', 'opp_pts', 'secs']
    return g[cols].sort_values(['game_date', 'game_id', 'team', 'lineup']).reset_index(drop=True)


def latest_season(today):
    """The newest season the source has a file for (it starts in October)."""
    cand = today.year + 1 if today.month >= 10 else today.year
    for s in (cand, cand - 1):
        req = urllib.request.Request(lineup_url(s), headers=UA, method='HEAD')
        try:
            urllib.request.urlopen(req, timeout=60)
            return s
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    raise SystemExit('No lineup file found for %s or %s' % (cand, cand - 1))


def ensure_finished(season):
    p = raw_path(season)
    if os.path.exists(p):
        return False
    b = fetch(lineup_url(season))
    if b is None:
        raise SystemExit(f'Missing finished season {season} at the source')
    df = pd.read_parquet(io.BytesIO(b))
    if len(df) < 10000:
        raise SystemExit(f'Season {season}: only {len(df)} rows, refusing to save')
    with open(p, 'wb') as f:
        f.write(b)
    print(f'saved finished season {season} ({len(df)} stints)')
    return True


def refresh_current(season):
    lb = fetch(lineup_url(season))
    pb = fetch(pbp_url(season))
    if lb is None or pb is None:
        raise SystemExit(f'Season {season}: lineup or pbp file missing at the source')
    lu = pd.read_parquet(io.BytesIO(lb))
    pbp = pd.read_parquet(io.BytesIO(pb), columns=['game_id', 'game_date'])
    dates = (pbp.drop_duplicates('game_id').set_index('game_id')['game_date']
                .astype(str).str.slice(0, 10).to_dict())
    missing = sorted(set(lu['game_id']) - set(dates))
    if missing:
        raise SystemExit(f'Season {season}: no date for games {missing[:5]}')
    new = per_game(lu, dates)
    p = current_path(season)
    if os.path.exists(p):
        old = pd.read_csv(p, dtype={'game_id': str, 'lineup': str})
        if new['game_id'].nunique() < old['game_id'].nunique():
            raise SystemExit(f'Season {season}: source now has fewer games '
                             f'({new["game_id"].nunique()}) than our copy '
                             f'({old["game_id"].nunique()}); keeping our copy')
    new.to_csv(p, index=False)
    print(f'season {season}: {new["game_id"].nunique()} games through {new["game_date"].max()}')


HEADSHOTS_TREE = 'https://api.github.com/repos/jsierrahoopshype/nba-headshots/git/trees/main?recursive=1'
HEADSHOTS_DIR = 'players/headshots/face2-160/'


def refresh_headshots():
    """data/headshots.json: NBA player id -> file stem in nba-headshots' face2-160
    folder (uniform framing, 160px WebP). Read from the public repo's file list.
    Uses the workflow's automatic GITHUB_TOKEN when present (higher rate limit),
    nothing otherwise. On any failure the previous file is kept."""
    import json
    hdr = dict(UA, Accept='application/vnd.github+json')
    tok = os.environ.get('GITHUB_TOKEN')
    if tok:
        hdr['Authorization'] = 'Bearer ' + tok
    try:
        with urllib.request.urlopen(urllib.request.Request(HEADSHOTS_TREE, headers=hdr), timeout=60) as r:
            tree = json.load(r)
    except Exception as e:  # noqa: BLE001 - keep the old map on any error
        print('headshots: kept previous map (%s)' % e)
        return
    if tree.get('truncated'):
        print('headshots: tree truncated, kept previous map')
        return
    out = {}
    for t in tree.get('tree', []):
        p = t.get('path', '')
        if p.startswith(HEADSHOTS_DIR) and p.endswith('.webp'):
            stem = p[len(HEADSHOTS_DIR):-5]
            pid = stem.split('-', 1)[0]
            if pid.isdigit():
                out[pid] = stem
    if len(out) < 1000:
        print('headshots: only %d files listed, kept previous map' % len(out))
        return
    with open(os.path.join(ROOT, 'data', 'headshots.json'), 'w') as f:
        json.dump(out, f, indent=0, sort_keys=True)
        f.write('\n')
    print('headshots: %d players' % len(out))


def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(CUR, exist_ok=True)
    today = dt.datetime.now(dt.timezone.utc).date()
    cur = latest_season(today)
    for s in range(FIRST_SEASON, cur):
        ensure_finished(s)
    refresh_current(cur)
    refresh_headshots()
    # A season that has just finished keeps its per-game file (harmless) but is
    # read from data/raw from now on; see lineup_lab_build.load_seasons().
    with open(os.path.join(CUR, 'season.txt'), 'w') as f:
        f.write(str(cur) + '\n')


if __name__ == '__main__':
    sys.exit(main())
