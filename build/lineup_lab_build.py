"""Build Lineup Lab: every NBA five-man lineup and duo since 1996-97.

Reads only what is in this repo (data/raw, data/current, data/teams.json,
docs/faces) and writes the static site into docs/, which GitHub Pages serves
and the hoopsmatic.com Worker proxies at https://hoopsmatic.com/lineups/.
No network access here; the download step is lineup_lab_update.py.

Pages (all canonical on hoopsmatic.com):
  /lineups/                       best lineups of the last 30 days and the season, teams, seasons, method
  /lineups/all-time/              best and worst single-season lineups, most minutes together
  /lineups/duos/                  this season's duos, best single-season duos, most minutes together
  /lineups/season/<1999-00>/      league leaders for one season
  /lineups/<team>/                one franchise: best ever, most minutes, every season
  /lineups/<team>/<1999-00>/      one team-season: summary, tabs, "one player changes"
  /lineups/players/               every player with 2,000+ minutes in the data
  /lineups/player/<name>-<id>/    one player: best teammates, season by season, lineups
  /lineups/sitemap.xml            per-URL <lastmod> that moves only when that page changes

Player pages: every player has one so every name is a link, but only players
with PLAYER_INDEX_MIN career minutes AND within the top PLAYER_INDEX_LIMIT by
minutes are indexable and in the sitemap; the rest are noindex. Raise
PLAYER_INDEX_LIMIT to open more once Search Console shows the first batch
indexed.

Season numbers inside the code follow the source: 2027 means 2026-27.
"""
import datetime as dt
import hashlib
import html
import itertools
import json
import os
import re
import sys
import unicodedata

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'docs')
SITE = 'https://hoopsmatic.com/lineups'
HS = 'https://jsierrahoopshype.github.io/nba-headshots'
FIRST_SEASON = 1997
FORGE = 'https://forge.hoopsmatic.com/'

# ── thresholds (possessions) ──────────────────────────────────────────────
# A finished season uses the full number; the season in progress scales it by
# how much of an 82-game schedule the team (or the league) has played, with a
# floor, so the lists fill up from opening week and tighten as the year goes on.
T_TEAM_LINEUP = 150      # team-season page, best and worst lineups
T_TEAM_DUO = 400         # team-season page, best duos
T_SEASON_LINEUP = 300    # season page, league-wide lineups
T_SEASON_DUO = 800       # season page, league-wide duos
T_FRANCHISE = 500        # team hub, highest-rated (finished seasons only)
T_ALLTIME_LINEUP = 1000  # all-time single-season lineups
T_ALLTIME_DUO = 2500     # all-time single-season duos
T_PLAYER_DUO = 1000      # player page, best teammates across his career
T_PLAYER_SEASON_DUO = 300    # player page, best teammate in one season
T_PLAYER_LINEUP = 300    # player page, best lineups he was in (one season)
RECENT_DAYS = 30         # "last 30 days", rolling
MIN_POSS = 100  # no list ever ranks a lineup or duo with fewer possessions, early season included
T_SWAP = MIN_POSS        # "one player changes": each lineup needs this many possessions
SMALL_SAMPLE = 200       # fewer possessions than this get the "Small sample" tag
PLAYER_INDEX_MIN = 2000  # career minutes in the data for an indexable player page
PLAYER_INDEX_LIMIT = 500  # stage 1: only the top 500 of those by minutes

HISTORIC = {'CHH': 'CHA', 'NOH': 'NOP', 'NOK': 'NOP', 'NJN': 'BKN', 'SEA': 'OKC', 'VAN': 'MEM'}
MONTHS = ['Jan.', 'Feb.', 'March', 'April', 'May', 'June', 'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.']
SUFFIXES = {'Jr.', 'Jr', 'Sr.', 'Sr', 'II', 'III', 'IV', 'V'}

esc = html.escape


# ── data ──────────────────────────────────────────────────────────────────

def load_teams():
    with open(os.path.join(DATA, 'teams.json')) as f:
        teams = json.load(f)
    return {t['abbr']: t for t in teams}


TEAMS = load_teams()
SLUG_TEAM = {t['slug']: t for t in TEAMS.values()}


def franchise(code):
    return TEAMS[HISTORIC.get(code, code)]


def team_name(code, season):
    """The team's name in that season."""
    if code == 'CHH':
        return 'Charlotte Hornets'
    if code == 'CHA':
        return 'Charlotte Bobcats' if season <= 2014 else 'Charlotte Hornets'
    if code == 'NOH':
        return 'New Orleans Hornets'
    if code == 'NOK':
        return 'New Orleans/Oklahoma City Hornets'
    if code == 'NJN':
        return 'New Jersey Nets'
    if code == 'SEA':
        return 'Seattle SuperSonics'
    if code == 'VAN':
        return 'Vancouver Grizzlies'
    if code == 'WAS' and season == 1997:
        return 'Washington Bullets'
    return TEAMS[code]['name']


HIST_NICK = {'CHH': 'Hornets', 'NOH': 'Hornets', 'NOK': 'Hornets', 'NJN': 'Nets',
             'SEA': 'SuperSonics', 'VAN': 'Grizzlies'}


def nickname(code, season):
    if code in HIST_NICK:
        return HIST_NICK[code]
    if code == 'CHA' and season <= 2014:
        return 'Bobcats'
    if code == 'WAS' and season == 1997:
        return 'Bullets'
    t = TEAMS[code]
    return t['name'].replace(t['short'], '', 1).strip() or t['name']


def season_label(y):
    return f'{y - 1}-{str(y)[2:]}'


def ap_date(iso):
    d = dt.date.fromisoformat(iso)
    return f'{MONTHS[d.month - 1]} {d.day}, {d.year}'


def ap_day(iso):
    d = dt.date.fromisoformat(iso)
    return f'{MONTHS[d.month - 1]} {d.day}'


def _valid(s):
    ps = [p.strip() for p in s.split(',')]
    return len(ps) == 5 and all(p.split(' ', 1)[0].isdigit() and ' ' in p for p in ps)


def repair(col):
    """A handful of source rows carry 'NA <name>' instead of a player id (two
    stints of the 2014-15 Nets). Fill the id from the same name elsewhere in the
    season; a row that still is not five ids and names becomes None (dropped)."""
    uniq = col.unique()
    bad = [s for s in uniq if not _valid(s)]
    if not bad:
        return col
    by_name = {}
    for s in uniq:
        if _valid(s):
            for p in s.split(','):
                pid, nm = p.strip().split(' ', 1)
                by_name[nm] = pid
    fix = {}
    for s in bad:
        out = []
        for p in s.split(','):
            p = p.strip()
            pid, _, nm = p.partition(' ')
            if not pid.isdigit():
                pid = by_name.get(nm, pid)
            out.append(f'{pid} {nm}')
        t = ', '.join(out)
        fix[s] = t if _valid(t) else None
    return col.map(lambda s: fix.get(s, s))


def key_of(s):
    return '-'.join(sorted((p.strip().split(' ', 1)[0] for p in s.split(',')), key=int))


def add_ratings(df):
    df = df.copy()
    df['ortg'] = df['pts'] / df['poss'].where(df['poss'] > 0) * 100
    df['drtg'] = df['opp_pts'] / df['opp_poss'].where(df['opp_poss'] > 0) * 100
    df['net'] = df['ortg'] - df['drtg']
    return df


def read_raw(season, names):
    cols = ['game_id', 'team', 'lineup_team', 'poss_team', 'pts_team', 'poss_opp', 'pts_opp', 'secs_played']
    d = pd.read_parquet(os.path.join(DATA, 'raw', f'lineup_stats_regular_{season}.parquet'), columns=cols)
    d['lineup_team'] = repair(d['lineup_team'])
    d = d[d['lineup_team'].notna()]
    uniq = d['lineup_team'].unique()
    for s in uniq:
        for p in s.split(','):
            pid, nm = p.strip().split(' ', 1)
            names[pid] = nm
    km = {s: key_of(s) for s in uniq}
    d['lineup'] = d['lineup_team'].map(km)
    d = d.rename(columns={'poss_team': 'poss', 'pts_team': 'pts', 'poss_opp': 'opp_poss',
                          'pts_opp': 'opp_pts', 'secs_played': 'secs'})
    return d[['game_id', 'team', 'lineup', 'poss', 'pts', 'opp_poss', 'opp_pts', 'secs']]


def read_current(season, names):
    d = pd.read_csv(os.path.join(DATA, 'current', f'lineup_games_{season}.csv'),
                    dtype={'game_id': str, 'lineup': str, 'names': str})
    for k, ns in zip(d['lineup'], d['names']):
        for pid, nm in zip(k.split('-'), ns.split('|')):
            names[pid] = nm
    return d


def load_seasons():
    """Returns (lineups, records, current_games, cur, names)."""
    with open(os.path.join(DATA, 'current', 'season.txt')) as f:
        cur = int(f.read().strip())
    names = {}
    frames, recs = [], []
    cur_games = None
    for s in range(FIRST_SEASON, cur + 1):
        d = read_current(s, names) if s == cur else read_raw(s, names)
        if s == cur:
            cur_games = d.copy()
        g = (d.groupby(['team', 'lineup'], as_index=False)
               .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                    opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), games=('game_id', 'nunique')))
        g['season'] = s
        frames.append(g)
        sc = d.groupby(['team', 'game_id'], as_index=False)[['pts', 'opp_pts']].sum()
        sc['w'] = (sc['pts'] > sc['opp_pts']).astype(int)
        r = sc.groupby('team', as_index=False).agg(games=('game_id', 'nunique'), wins=('w', 'sum'))
        r['losses'] = r['games'] - r['wins']
        r['season'] = s
        recs.append(r)
    lineups = add_ratings(pd.concat(frames, ignore_index=True))
    lineups['slug'] = lineups['team'].map(lambda c: franchise(c)['slug'])
    records = pd.concat(recs, ignore_index=True)
    return lineups, records, cur_games, cur, names


PAIRS = list(itertools.combinations(range(5), 2))


def duos_of(lineups):
    """Lineup totals -> duo totals (every pair inside each five, summed)."""
    out = []
    for s, part in lineups.groupby('season'):
        ids = np.array([k.split('-') for k in part['lineup']])
        base = part[['team', 'poss', 'pts', 'opp_poss', 'opp_pts', 'secs']].to_numpy()
        rows = []
        for i, j in PAIRS:
            a, b = ids[:, i], ids[:, j]
            lo = np.where(a.astype(int) < b.astype(int), a, b)
            hi = np.where(a.astype(int) < b.astype(int), b, a)
            rows.append(pd.DataFrame({'team': base[:, 0], 'p1': lo, 'p2': hi,
                                      'poss': base[:, 1], 'pts': base[:, 2], 'opp_poss': base[:, 3],
                                      'opp_pts': base[:, 4], 'secs': base[:, 5]}))
        d = pd.concat(rows, ignore_index=True)
        for c in ['poss', 'pts', 'opp_poss', 'opp_pts', 'secs']:
            d[c] = pd.to_numeric(d[c])
        g = d.groupby(['team', 'p1', 'p2'], as_index=False)[['poss', 'pts', 'opp_poss', 'opp_pts', 'secs']].sum()
        g['season'] = s
        out.append(g)
    duos = add_ratings(pd.concat(out, ignore_index=True))
    duos['slug'] = duos['team'].map(lambda c: franchise(c)['slug'])
    return duos


def rank(df, n, asc=False):
    """Best (or worst) by net rating; ties go to more possessions, then the key."""
    keycol = 'lineup' if 'lineup' in df.columns else 'p1'
    d = df[df['net'].notna()].sort_values(['net', 'poss', keycol], ascending=[asc, False, True])
    return d.head(n)


def by_minutes(df, n):
    keycol = 'lineup' if 'lineup' in df.columns else 'p1'
    return df.sort_values(['secs', keycol], ascending=[False, True]).head(n)


def scaled(full, played, floor):
    """Threshold for the season in progress: full * share of 82 games, floored."""
    return max(floor, int(round(full * min(1.0, played / 82.0))))


def window(cur_games, cur, start):
    w = cur_games[cur_games['game_date'] >= start]
    g = (w.groupby(['team', 'lineup'], as_index=False)
           .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), games=('game_id', 'nunique')))
    g = add_ratings(g)
    g['season'] = cur
    g['slug'] = g['team'].map(lambda c: franchise(c)['slug'])
    return g


def recent(cur_games, cur):
    """Best lineups of the last RECENT_DAYS days (rolling, ending on the latest
    game date), positive net ratings only, at least MIN_POSS possessions (the
    list can be empty in the first days of a season).
    Returns dict(rows, start, end, min, whole_season) or None."""
    if cur_games is None or cur_games.empty:
        return None
    end = cur_games['game_date'].max()
    first = cur_games['game_date'].min()
    start = max(first, (dt.date.fromisoformat(end) - dt.timedelta(days=RECENT_DAYS - 1)).isoformat())
    g = window(cur_games, cur, start)
    g = g[g['net'] > 0]
    q = g[g['poss'] >= MIN_POSS]
    return dict(rows=rank(q, 10), start=start, end=end, min=MIN_POSS, whole_season=(start == first))


# ── players ───────────────────────────────────────────────────────────────

def ascii_slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def player_url(pid):
    return f'{SITE}/player/{PSLUG[pid]}/'


def last_name(n):
    w = n.split()
    if len(w) >= 3 and w[-1] in SUFFIXES:
        return ' '.join(w[-2:])
    return w[-1] if w else n


def build_players(lineups, duos):
    """Per-player minutes by season and team, career minutes, slugs and which
    pages are indexable."""
    ex = lineups[['season', 'team', 'lineup', 'secs']].copy()
    ex['pid'] = ex['lineup'].str.split('-')
    ex = ex.explode('pid')
    ps = ex.groupby(['pid', 'season', 'team'], as_index=False)['secs'].sum()
    car = ps.groupby('pid')['secs'].sum() / 60
    slugs = {}
    for pid in car.index:
        slugs[pid] = f'{ascii_slug(NAMES.get(pid, pid)) or "player"}-{pid}'
    eligible = car[car >= PLAYER_INDEX_MIN].sort_values(ascending=False)
    indexed = set(eligible.index[:PLAYER_INDEX_LIMIT])
    lin_idx = {k: v.to_numpy() for k, v in ex.groupby('pid').groups.items()}
    d1 = duos.groupby('p1').groups
    d2 = duos.groupby('p2').groups
    duo_idx = {}
    for k, v in itertools.chain(d1.items(), d2.items()):
        duo_idx.setdefault(k, []).append(v.to_numpy())
    duo_idx = {k: np.unique(np.concatenate(v)) for k, v in duo_idx.items()}
    return ps, car, slugs, indexed, set(eligible.index), lin_idx, duo_idx


# ── one player changes ────────────────────────────────────────────────────

def swap_index(lineups):
    """(season, team, four ids) -> list of (fifth id, row) for lineups with at
    least T_SWAP possessions."""
    idx = {}
    q = lineups[lineups['poss'] >= T_SWAP]
    for r in q.itertuples(index=False):
        ids = r.lineup.split('-')
        for i in range(5):
            four = tuple(ids[:i] + ids[i + 1:])
            idx.setdefault((r.season, r.team, four), []).append((ids[i], r))
    return idx


def swaps_for(r):
    """Other lineups of the same team-season sharing four of r's players."""
    ids = r['lineup'].split('-')
    out = []
    for i in range(5):
        four = tuple(ids[:i] + ids[i + 1:])
        for fifth, o in SWAPS.get((r['season'], r['team'], four), []):
            if fifth != ids[i]:
                out.append((ids[i], fifth, o))
    out.sort(key=lambda t: -t[2].poss)
    return out[:6]


# ── html pieces ───────────────────────────────────────────────────────────

def load_heights():
    """data/heights.json (build/lineup_lab_heights.py): NBA id -> centimetres."""
    p = os.path.join(DATA, 'heights.json')
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


HEIGHTS = load_heights()
UNKNOWN_HEIGHT = 200.0  # about 6-7, the middle of a lineup, for a player with no height in the source


def by_height(ids):
    """Shortest to tallest, which reads roughly point guard to center.
    Ties keep their order."""
    return sorted(ids, key=lambda p: HEIGHTS.get(p, UNKNOWN_HEIGHT))


def load_faces():
    d = os.path.join(OUT, 'faces')
    return {f[:-5] for f in os.listdir(d) if f.endswith('.webp')} if os.path.isdir(d) else set()


FACES = load_faces()


def initials(n):
    w = [x for x in n.replace('.', ' ').split() if x not in SUFFIXES]
    return (w[0][0] + (w[-1][0] if len(w) > 1 else '')).upper() if w else '?'


def face(pid, size):
    n = NAMES.get(pid, pid)
    if pid in FACES:
        inner = (f'<img src="{SITE}/faces/{pid}.webp?v={FACE_V}" alt="" width="{size}" height="{size}" '
                 f'loading="lazy" decoding="async">')
    else:
        inner = f'<span class="ini">{esc(initials(n))}</span>'
    return f'<span class="ph" style="--s:{size}px">{inner}</span>'


def split_name(full):
    """'Shai Gilgeous-Alexander' -> ('Shai', 'Gilgeous-Alexander');
    'Gary Trent Jr.' -> ('Gary', 'Trent Jr.'); one-word names -> ('', name)."""
    last = last_name(full)
    first = full[:len(full) - len(last)].strip() if full.endswith(last) else ''
    return first, last


def people(ids, size=40, cls='who'):
    """Faces with the full name under each (first name on a small line, last
    name below it), shortest player first."""
    items = []
    for p in by_height(ids):
        full = NAMES.get(p, p)
        first, last = split_name(full)
        # --n (name length) lets the CSS shrink a long name to fit its column
        items.append(f'<li><a href="{player_url(p)}" title="{esc(full)}">'
                     f'{face(p, size)}<span class="fn" style="--n:{max(len(first), 4)}">{esc(first) or "&nbsp;"}</span>'
                     f'<span class="ln" style="--n:{max(len(last), 4)}">{esc(last)}</span></a></li>')
    return f'<ul class="{cls}">' + ''.join(items) + '</ul>'


def logo(code, size=22):
    return (f'<img class="lg" src="{HS}/teams/logos/current/svg/{franchise(code)["nba_id"]}.svg" alt="" '
            f'width="{size}" height="{size}" loading="lazy">')


def fmt_net(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'n/a', 'dash'
    if v > 0.05:
        return f'+{v:.1f}', 'pos'
    if v < -0.05:
        return f'−{abs(v):.1f}', 'neg'
    return '0.0', 'dash'


def n0(v):
    return f'{int(round(v)):,}'


def r1(v):
    return 'n/a' if v is None or np.isnan(v) else f'{v:.1f}'


def team_season_url(code, season):
    return f'{SITE}/{franchise(code)["slug"]}/{season_label(season)}/'


def num_block(r, sample=False):
    """Net rating, top right of a card. sample=True adds possessions and
    minutes under it, for the compact season-by-season rows that have no
    meta line."""
    t, cls = fmt_net(r['net'])
    smp = (f'<span class="smp">{n0(r["poss"])} poss · {n0(r["secs"] / 60)} min</span>' if sample else '')
    return f'<div class="num"><span class="net {cls}">{t}</span><span class="lbl">net rating</span>{smp}</div>'


def meta(r, extra=''):
    """The line under the faces: sample, Off/Def, then any extra, and a
    Small sample warning when the sample is thin."""
    parts = [f'{n0(r["poss"])} poss', f'{n0(r["secs"] / 60)} min',
             f'Off {r1(r["ortg"])}', f'Def {r1(r["drtg"])}']
    if extra:
        parts.append(extra)
    tag = ' <span class="ss">Small sample</span>' if r['poss'] < SMALL_SAMPLE else ''
    return '<p class="sub">' + ' · '.join(f'<span class="nw">{x}</span>' for x in parts) + tag + '</p>'


_SEASON_RE = re.compile(r'(\d{4}-\d\d)')


def nobr(text):
    """Escape, and keep each season label (2022-23) on one line."""
    return _SEASON_RE.sub(r'<span class="nw">\1</span>', esc(text))


def team_line(code, season, label=None):
    return (f'<a class="tm" href="{team_season_url(code, season)}">{logo(code)}'
            f'<span>{nobr(label or (season_label(season) + " " + team_name(code, season)))}</span></a>')


def lineup_row(r, i, show_team=True, extra_cls='', swaps=True):
    ids = r['lineup'].split('-')
    rid = 'l-' + '-'.join(ids) + (f'-{r["season"]}' if show_team else '')
    head = f'<span class="rk">{i}</span>'
    g = int(r['games']) if 'games' in r and not pd.isna(r.get('games')) else None
    sub = meta(r, f'{g} game{"" if g == 1 else "s"}' if g else '')
    if show_team:
        head += team_line(r['team'], r['season'])
    det = ''
    if swaps and 'team' in r and 'season' in r:
        sw = swaps_for(r)
        if sw:
            items = []
            for out_p, in_p, o in sw:
                t, cls = fmt_net(o.net)
                items.append(f'<li><span><a href="{player_url(in_p)}">{esc(NAMES.get(in_p, in_p))}</a> '
                             f'instead of <a href="{player_url(out_p)}">{esc(NAMES.get(out_p, out_p))}</a></span>'
                             f'<span class="sv"><b class="{cls}">{t}</b> {n0(o.poss)} poss</span></li>')
            det = (f'<details class="det"><summary>One player changes ({len(sw)})</summary>'
                   f'<p class="note">The same team and season, four of these five on the floor and one different.</p>'
                   f'<ul class="swaps">{"".join(items)}</ul></details>')
    return (f'<li class="row {extra_cls}" id="{rid}"><div class="rh">{head}{num_block(r)}</div>'
            f'{people(ids)}{sub}{det}</li>')


def duo_row(r, i, show_team=True, extra_cls='', label=None):
    """label: a plain heading (with the franchise logo) instead of the
    team-season link, for rows that span several seasons."""
    ids = [r['p1'], r['p2']]
    head = f'<span class="rk">{i}</span>'
    if label is not None:
        head += f'<span class="tm">{logo(SLUG_TEAM[r["slug"]]["abbr"])}<span>{nobr(label)}</span></span>'
    elif show_team:
        head += team_line(r['team'], r['season'])
    sub = meta(r)
    return (f'<li class="row duo {extra_cls}"><div class="rh">{head}{num_block(r)}</div>'
            f'{people(ids, 52, "who two")}{sub}</li>')


def career_row(r, i, abbr):
    ids = r['lineup'].split('-')
    t = TEAMS[abbr]
    seasons = (season_label(r['first']) if r['first'] == r['last']
               else f'{season_label(r["first"])} to {season_label(r["last"])}')
    head = (f'<span class="rk">{i}</span><a class="tm" href="{SITE}/{t["slug"]}/">{logo(abbr)}'
            f'<span>{esc(t["name"])}, {seasons}</span></a>')
    sub = meta(r, 'all seasons combined')
    return f'<li class="row"><div class="rh">{head}{num_block(r)}</div>{people(ids)}{sub}</li>'


_list_n = [0]


def rows(df, fn, show=None, **kw):
    """An <ol> of rows. With show=N, rows past N get class x and a "Show all"
    button reveals them (all rows are in the HTML for readers without JS and
    for search engines)."""
    if df is None or len(df) == 0:
        return '<p class="note empty">Not enough possessions yet.</p>'
    _list_n[0] += 1
    lid = f'list{_list_n[0]}'
    items = []
    for i, (_, r) in enumerate(df.iterrows()):
        cls = 'x' if show and i >= show else ''
        items.append(fn(r, i + 1, extra_cls=cls, **kw))
    btn = (f'<button type="button" class="showall" data-more="{lid}" hidden>Show all {len(df)}</button>'
           if show and len(df) > show else '')
    return f'<ol class="rows" id="{lid}">' + ''.join(items) + '</ol>' + btn


def block(title, note, inner, anchor=None):
    a = f' id="{anchor}"' if anchor else ''
    n = f'<p class="note">{note}</p>' if note else ''
    return f'<section class="block"{a}><h2>{title}</h2>{n}{inner}</section>'


_tab_n = [0]


def tabs(panels):
    """panels: list of (label, note, html). Without JS every panel shows, each
    under its own heading; with JS they become tabs."""
    _tab_n[0] += 1
    tid = f't{_tab_n[0]}'
    btns = ''.join(f'<button type="button" role="tab" id="{tid}b{i}" aria-controls="{tid}p{i}" '
                   f'aria-selected="{"true" if i == 0 else "false"}">{esc(lbl)}</button>'
                   for i, (lbl, _, _) in enumerate(panels))
    body = ''.join(f'<div role="tabpanel" id="{tid}p{i}" aria-labelledby="{tid}b{i}">'
                   f'<h2 class="ph2">{esc(lbl)}</h2>'
                   + (f'<p class="note">{note}</p>' if note else '') + html_ + '</div>'
                   for i, (lbl, note, html_) in enumerate(panels))
    return f'<div class="tabs" data-tabs><div role="tablist" class="tablist">{btns}</div>{body}</div>'


def chips(items):
    return '<div class="chips">' + ''.join(
        f'<a class="chip{" active" if act else ""}" href="{u}">{esc(t)}</a>' for t, u, act in items) + '</div>'


def local_nav(active):
    cur = CTX['cur']
    items = [('Overview', f'{SITE}/', 'overview'),
             ('This season', f'{SITE}/season/{season_label(cur)}/', 'season'),
             ('Teams', f'{SITE}/#teams', 'teams'),
             ('Players', f'{SITE}/players/', 'players'),
             ('All-time', f'{SITE}/all-time/', 'alltime'),
             ('Duos', f'{SITE}/duos/', 'duos')]
    cur_attr = ' aria-current="page"'
    return ('<nav class="lnav" aria-label="Lineups sections">' + ''.join(
        f'<a href="{u}"{cur_attr if k == active else ""}>{t}</a>' for t, u, k in items) + '</nav>')


NET_NOTE = 'Net rating: points scored minus points allowed per 100 possessions. Off: points scored per 100 (higher is better). Def: points allowed per 100 (lower is better).'
SMALL_NOTE = f'"Small sample" marks fewer than {SMALL_SAMPLE} possessions.'


def font_v():
    h = hashlib.sha256()
    for fn in ('dm-sans-latin.woff2', 'jetbrains-mono-latin.woff2'):
        with open(os.path.join(OUT, 'fonts', fn), 'rb') as f:
            h.update(f.read())
    return h.hexdigest()[:10]


def faces_v():
    p = os.path.join(DATA, 'faces_state.json')
    if not os.path.exists(p):
        return '0'
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:10]


CSS = """
:root{--bg:#f5f5f7;--surface:#fff;--surface-hover:#f0f0f2;--border:#d1d1d6;--text:#1d1d1f;
--text-secondary:#5f5f66;--accent:#3b82f6;--green:#1d8a40;--red:#d12c2c;--ph:#e8e8ed}
*{margin:0;padding:0;box-sizing:border-box}
html{font-size:115%;-webkit-text-size-adjust:100%}
body{font-family:'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);
min-height:100vh;line-height:1.5;-webkit-font-smoothing:antialiased;overflow-x:hidden}
.container{max-width:980px;margin:0 auto;padding:1rem 16px 3rem}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.hdr h1{font-size:1.2rem;font-weight:700;letter-spacing:-.03em;margin-bottom:.3rem}
.subtitle{color:var(--text-secondary);font-size:.84rem;margin-bottom:.7rem;max-width:60rem}
.stamp{font-variant-numeric:tabular-nums;font-size:.74rem}
.lnav{display:flex;gap:.3rem;overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 0 1.1rem;padding-bottom:.1rem}
.lnav a{flex:0 0 auto;font-size:.8rem;font-weight:600;color:var(--text);padding:.45rem .75rem;border-radius:999px;
border:1px solid var(--border);background:var(--surface);min-height:36px;display:inline-flex;align-items:center}
.lnav a[aria-current]{background:var(--text);border-color:var(--text);color:#fff}
.lnav a:hover{text-decoration:none;border-color:var(--accent)}
.block{margin-bottom:1.6rem}
.block>h2,.ph2{font-size:1rem;font-weight:700;letter-spacing:-.01em;margin-bottom:.25rem}
.note{font-size:.78rem;color:var(--text-secondary);margin-bottom:.55rem}
.lede{font-size:.9rem;margin-bottom:1rem;max-width:62rem}
ol.rows{list-style:none;display:grid;grid-template-columns:minmax(0,1fr);gap:.5rem}
@media(min-width:760px){ol.rows{grid-template-columns:repeat(2,minmax(0,1fr))}}
ol.rows.clip>li.x{display:none}
.row{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.65rem .75rem .6rem;min-width:0}
.row:target{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent)}
.rh{display:flex;align-items:flex-start;gap:.5rem;min-width:0;flex-wrap:wrap}
.rh>h3{flex:1;padding-top:.15rem}
.tm.sub{font-weight:400;padding-top:.3rem}
.nw{white-space:nowrap}
.rk{font-variant-numeric:tabular-nums;font-size:.74rem;color:var(--text-secondary);min-width:1.4rem;padding-top:.15rem}
.tm{display:flex;align-items:center;gap:.4rem;font-weight:600;font-size:.82rem;line-height:1.25;color:var(--text);min-width:0;flex:1;padding-top:.05rem}
@media(max-width:420px){.tm{font-size:.78rem}}
.tm span{overflow-wrap:anywhere}.lg{width:22px;height:22px;flex-shrink:0}
a.tm:hover{color:var(--accent);text-decoration:none}
.num{display:flex;flex-direction:column;align-items:flex-end;text-align:right;margin-left:auto;flex-shrink:0;order:2}
.net{font-variant-numeric:tabular-nums;font-weight:700;font-size:1.3rem;line-height:1.05;letter-spacing:-.02em}
.lbl{font-size:.64rem;color:var(--text-secondary);font-weight:500}
.smp{font-variant-numeric:tabular-nums;font-size:.68rem;color:var(--text-secondary);white-space:nowrap}
.ss{color:#9a6400;font-weight:600;white-space:nowrap}
.pos{color:var(--green)}.neg{color:var(--red)}.dash{color:var(--text-secondary)}
ul.who{list-style:none;display:flex;gap:.35rem;margin:.5rem 0 .35rem}
ul.who li{flex:1 1 0;min-width:0;max-width:96px;container-type:inline-size}
ul.who.two li{max-width:120px;flex:0 1 120px}
ul.who a{display:flex;flex-direction:column;align-items:center;color:var(--text);text-align:center}
ul.who a:hover{text-decoration:none;color:var(--accent)}
/* names: a set size, or smaller when the name is too long for its column
   (100cqi = the column's width; about .62em per letter) */
.fn{--f:.62rem;font-size:min(var(--f),calc(100cqi / (var(--n) * .62)));line-height:1.15;color:var(--text-secondary);
margin-top:.25rem;max-width:100%;white-space:nowrap}
.ln{--f:.72rem;font-size:min(var(--f),calc(100cqi / (var(--n) * .64)));font-weight:600;line-height:1.15;max-width:100%;white-space:nowrap}
@media(max-width:420px){.fn{--f:.58rem}.ln{--f:.66rem}}
.two .fn{--f:.7rem}.two .ln{--f:.82rem}
.ph{width:var(--s);height:var(--s);border-radius:50%;background:var(--ph);overflow:hidden;flex-shrink:0;
display:flex;align-items:center;justify-content:center}
.ph img{width:100%;height:100%;display:block}
.ini{font-weight:700;color:#55555c;font-size:calc(var(--s) * .34)}
.sub{font-variant-numeric:tabular-nums;font-size:.72rem;color:var(--text-secondary);line-height:1.45}
details.det{margin-top:.45rem;border-top:1px solid var(--surface-hover);padding-top:.4rem}
details.det summary{cursor:pointer;font-size:.76rem;font-weight:600;color:var(--accent);min-height:28px;display:flex;align-items:center}
ul.swaps{list-style:none;display:grid;gap:.3rem}
ul.swaps li{display:flex;justify-content:space-between;gap:.6rem;font-size:.78rem;align-items:baseline}
ul.swaps .sv{font-variant-numeric:tabular-nums;font-size:.72rem;white-space:nowrap}
.showall{margin-top:.55rem;font:inherit;font-size:.8rem;font-weight:600;color:var(--accent);background:var(--surface);
border:1px solid var(--border);border-radius:999px;padding:.45rem 1rem;cursor:pointer;min-height:40px}
.tablist{display:none}
.tabs.js .tablist{display:flex;gap:.3rem;flex-wrap:wrap;margin-bottom:.6rem}
.tabs.js .ph2{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
.tabs:not(.js) [role=tabpanel]{margin-bottom:1.4rem}
.tablist button{font:inherit;font-size:.82rem;font-weight:600;padding:.45rem .85rem;border-radius:999px;border:1px solid var(--border);
background:var(--surface);color:var(--text);cursor:pointer;min-height:40px}
.tablist button[aria-selected=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.summary{display:grid;grid-template-columns:minmax(0,1fr);gap:.5rem;margin-bottom:1.4rem}
@media(min-width:760px){.summary{grid-template-columns:repeat(3,minmax(0,1fr))}}
.summary .row{padding:.55rem .65rem}
.summary h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-secondary);font-weight:700;margin-bottom:.1rem}
.chips{display:flex;flex-wrap:wrap;gap:.4rem}
.chip{display:inline-flex;align-items:center;min-height:34px;border:1px solid var(--border);background:var(--surface);border-radius:999px;
padding:.25rem .75rem;font-size:.78rem;color:var(--text);max-width:100%}
.chip.active{background:var(--accent);border-color:var(--accent);color:#fff}
a.chip:hover{text-decoration:none;border-color:var(--accent)}
.teams{list-style:none;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.4rem}
@media(min-width:560px){.teams{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:860px){.teams{grid-template-columns:repeat(5,minmax(0,1fr))}}
.teams a{display:flex;align-items:center;gap:.45rem;background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:.5rem .55rem;font-size:.8rem;font-weight:600;color:var(--text);min-width:0;min-height:44px}
.teams a span{overflow-wrap:anywhere}.teams img{width:24px;height:24px;flex-shrink:0}
.teams a:hover{text-decoration:none;border-color:var(--accent)}
.swapg{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.7rem .8rem;margin-bottom:.6rem}
.swapg .who{margin-top:.2rem}
.swapg table{width:100%;border-collapse:collapse;font-size:.8rem;margin-top:.4rem}
.swapg th{text-align:left;font-size:.64rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-secondary);font-weight:700;padding:.2rem .3rem}
.swapg td{padding:.35rem .3rem;border-top:1px solid var(--surface-hover);vertical-align:middle}
.swapg td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.swapg td.p{display:flex;align-items:center;gap:.4rem}
@media(max-width:360px){.swapg table{font-size:.72rem}.swapg th,.swapg td{padding-left:.15rem;padding-right:.15rem}}
ol.ssn{list-style:none;display:grid;gap:.4rem}
ol.ssn>li{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.5rem .65rem}
.ssh{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem;flex-wrap:wrap;font-size:.84rem}
.ssh b{font-weight:700}.ssh .meta{font-size:.74rem;color:var(--text-secondary)}
.ssb{display:flex;align-items:center;gap:.6rem;margin-top:.3rem;flex-wrap:wrap}
.ssb ul.who{margin:0;flex:1 1 220px;max-width:340px}
.ssb ul.who .ln{--f:.64rem}.ssb ul.who .fn{--f:.56rem}
.ssb .num{margin-left:auto}
.how{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.75rem .9rem}
.how dt{font-weight:700;font-size:.84rem;margin-top:.6rem}.how dt:first-child{margin-top:0}
.how dd{font-size:.82rem;color:var(--text-secondary)}
.more{display:flex;flex-wrap:wrap;gap:.4rem}
.az{columns:2 13rem;column-gap:1rem;list-style:none}
.az li{break-inside:avoid;font-size:.84rem;padding:.2rem 0}
.az .yrs{font-size:.72rem;color:var(--text-secondary)}
.find{width:100%;max-width:26rem;font:inherit;font-size:.9rem;padding:.55rem .75rem;border:1px solid var(--border);border-radius:10px;margin-bottom:.8rem;background:var(--surface)}
.foot{text-align:center;font-size:.8125rem;line-height:1.6;color:var(--text-secondary);padding:1.5rem 1rem 0}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
"""

JS = """
(function(){
  document.querySelectorAll('[data-tabs]').forEach(function(t){
    var b=t.querySelectorAll('[role=tab]'),p=t.querySelectorAll('[role=tabpanel]');
    function sel(i){b.forEach(function(x,j){x.setAttribute('aria-selected',j===i?'true':'false');x.tabIndex=j===i?0:-1;p[j].hidden=j!==i;});}
    b.forEach(function(x,i){x.addEventListener('click',function(){sel(i);});
      x.addEventListener('keydown',function(e){var k=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0;if(k){var n=(i+k+b.length)%b.length;sel(n);b[n].focus();}});});
    t.classList.add('js');sel(0);
  });
  document.querySelectorAll('[data-more]').forEach(function(btn){
    var l=document.getElementById(btn.getAttribute('data-more'));l.classList.add('clip');btn.hidden=false;
    btn.addEventListener('click',function(){l.classList.remove('clip');btn.hidden=true;});
  });
  var f=document.getElementById('find');
  if(f){var items=document.querySelectorAll('.az li');f.addEventListener('input',function(){var q=f.value.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'');
    items.forEach(function(li){li.hidden=q&&li.getAttribute('data-n').indexOf(q)<0;});});}
  var h=decodeURIComponent(location.hash.slice(1));
  if(h){var el=document.getElementById(h);if(el){
    var pan=el.closest('[role=tabpanel]');if(pan){var bt=document.getElementById(pan.getAttribute('aria-labelledby'));if(bt)bt.click();}
    var ol=el.closest('ol.clip');if(ol){ol.classList.remove('clip');var sb=document.querySelector('[data-more="'+ol.id+'"]');if(sb)sb.hidden=true;}
    var d=el.querySelector('details');if(d)d.open=true;el.scrollIntoView({block:'center'});}}
})();
"""


def shell(path, title, desc, h1, subtitle, body, active=None, noindex=False):
    v = FONT_V
    url = SITE + path
    rob = '<meta name="robots" content="noindex,follow">\n' if noindex else ''
    fonts = (f"@font-face{{font-family:'DM Sans';src:url({SITE}/fonts/dm-sans-latin.woff2?v={v}) format('woff2');"
             "font-weight:100 1000;font-style:normal;font-display:swap}")
    nav = local_nav(active) if active is not None else ''
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{rob}<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="HoopsMatic">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<style>{fonts}{CSS}</style>
</head>
<body>
<div class="container">
<div class="hdr"><h1>{esc(h1)}</h1></div>
<p class="subtitle">{subtitle}</p>
{nav}
{body}
<footer class="foot">
<p>Anything wrong? Email us at <a href="mailto:hoopsmatic@hoopsmatic.com">hoopsmatic@hoopsmatic.com</a></p>
<p>For business inquiries, contact us at <a href="mailto:ads@hoopsmatic.com">ads@hoopsmatic.com</a></p>
</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""


CREDIT = ('Lineup data from <a href="https://github.com/ramirobentes/nbadatar">Ramiro Bentes</a> '
          '(<a href="https://x.com/NbaInRstats">@NbaInRstats</a>), built from NBA play-by-play. '
          f'Regular season only. <a href="{SITE}/#how">How these numbers work</a>.')


def links_block(items):
    return block('More lineups', None, '<div class="more">' + chips([(t, u, False) for t, u in items]) + '</div>')


def names_and(ids):
    nm = [NAMES.get(p, p) for p in by_height(ids)]
    return ', '.join(nm[:-1]) + ' and ' + nm[-1] if len(nm) > 1 else nm[0]


def net_words(v):
    t, _ = fmt_net(v)
    return t


# ── pages ─────────────────────────────────────────────────────────────────

def page_index(ctx):
    cur, L, rec = ctx['cur'], ctx['lineups'], ctx['recent']
    parts = []
    C = L[L['season'] == cur]
    played = float(ctx['records'][ctx['records']['season'] == cur]['games'].mean())
    ts = scaled(T_SEASON_LINEUP, played, MIN_POSS)
    season_best = rank(C[(C['poss'] >= ts) & (C['net'] > 0)], 10)
    if rec:
        span = ap_day(rec['start']) if rec['start'] == rec['end'] else f'{ap_day(rec["start"])} to {ap_day(rec["end"])}'
        if rec['whole_season']:
            title = f'Best lineups of {season_label(cur)} so far'
            note = (f'Games from {span}: the season is less than {RECENT_DAYS} days old, so the last {RECENT_DAYS} days are the whole season. '
                    f'Minimum {rec["min"]} possessions, positive net ratings only. {SMALL_NOTE}')
        else:
            title = f'Best lineups, last {RECENT_DAYS} days'
            note = (f'Games from {span}. Minimum {rec["min"]} possessions, positive net ratings only. '
                    f'Updated every day of the season. {SMALL_NOTE}')
        body = (rows(rec['rows'], lineup_row, show=3) if len(rec['rows']) else
                f'<p class="note">No five has played {MIN_POSS} possessions together with a positive net rating yet. '
                f'Most regular starting units get there within the first two weeks.</p>')
        parts.append(block(title, note, body, 'recent'))
        if not rec['whole_season']:
            parts.append(block(f'Best lineups of {season_label(cur)}',
                               f'The whole season so far. Minimum {ts:,} possessions (it grows with games played), positive net ratings only.',
                               rows(season_best, lineup_row, show=3), 'season'))
    mu = by_minutes(C, 1)
    if len(mu):
        parts.append(block(f'Most-used lineup of {season_label(cur)}', 'The five who have played the most minutes together this season.',
                           rows(mu, lineup_row)))
    teams = sorted(TEAMS.values(), key=lambda t: t['name'])
    tiles = ''.join(
        f'<li><a href="{SITE}/{t["slug"]}/"><img src="{HS}/teams/logos/current/svg/{t["nba_id"]}.svg" alt="" '
        f'width="24" height="24" loading="lazy"><span>{esc(t["name"])}</span></a></li>' for t in teams)
    parts.append(block('Every team, every season', 'Each franchise’s best lineups since 1996-97, and every season’s full lineup page.',
                       f'<ul class="teams">{tiles}</ul>', 'teams'))
    seasons = [(season_label(s), f'{SITE}/season/{season_label(s)}/', s == cur) for s in range(cur, FIRST_SEASON - 1, -1)]
    parts.append(block('League leaders by season', None, chips(seasons)))
    parts.append(block('Explore', None, '<div class="more">' + chips([
        ('Highest-rated lineups since 1996-97', f'{SITE}/all-time/', False),
        ('Best duos', f'{SITE}/duos/', False),
        ('Players', f'{SITE}/players/', False),
        ('Build your own five in Lineup Forge (separate tool)', FORGE, False)]) + '</div>'))
    how = f"""<dl class="how">
<dt>Lineup</dt><dd>Five players on the floor together for one team. Every stretch of a game where the same five stayed on the court counts toward it.</dd>
<dt>Net rating</dt><dd>Points the lineup scored per 100 possessions (Off) minus points it allowed per 100 possessions (Def). Higher Off is better, lower Def is better. +10 means it outscored opponents by 10 points every 100 trips. Ratings are worked out from the totals, so Off minus Def can differ from the net shown by 0.1 because of rounding. Garbage time is included.</dd>
<dt>Sample size</dt><dd>A small sample can make any five look great, so each list needs a minimum number of possessions, shown above it, and anything under {SMALL_SAMPLE} possessions is marked "Small sample." For the season in progress the minimum grows with games played. A minimum is a filter, not a guarantee that a result will last.</dd>
<dt>Last {RECENT_DAYS} days</dt><dd>The {RECENT_DAYS} days ending on the latest game date in the data. The minimum is {MIN_POSS} possessions, so in the first days of a season the list can be short or empty.</dd>
<dt>Duos</dt><dd>Team performance while both players were on the floor, whoever the other three teammates were. It is not a measure of chemistry or of either player alone.</dd>
<dt>One player changes</dt><dd>Lineups of the same team and season that share four players and differ in one, each with at least {T_SWAP} possessions. They describe what happened; opponents, game situations and who else was available all differ, so the change of player is not shown to be the cause.</dd>
<dt>Games and records</dt><dd>"Games together" counts the games in which that five played at least one stretch together. Team records are added up from the same play-by-play, overtime included.</dd>
<dt>Source</dt><dd>Lineup data from <a href="https://github.com/ramirobentes/nbadatar">Ramiro Bentes</a> (<a href="https://x.com/NbaInRstats">@NbaInRstats</a>), built from NBA play-by-play. Regular season only, 1996-97 onward, updated every day of the season.</dd>
<dt>Order of the faces</dt><dd>Shortest to tallest, which reads roughly point guard to center. Heights from <a href="https://www.wikidata.org">Wikidata</a>.</dd>
</dl>"""
    parts.append(block('How the numbers work', None, how, 'how'))
    stamp = f'<span class="stamp">Games through {ap_date(ctx["end"])}.</span>' if ctx['end'] else ''
    sub = f'Every NBA five-man lineup and duo since 1996-97, ranked by net rating. {stamp}'
    return shell('/', 'NBA Lineup Stats: Best Lineups of the Last 30 Days, This Season and Since 1996-97 | HoopsMatic',
                 'The best NBA lineups of the last 30 days and this season, every team’s lineups in every season since 1996-97, '
                 'the best duos and the highest-rated five-man units ever by net rating.',
                 'NBA Lineups', sub, '\n'.join(parts), active='overview')


def swap_groups(L):
    """Groups of four players with two or more different fifths, each with at
    least T_SWAP possessions; the two groups with the most possessions."""
    q = L[L['poss'] >= T_SWAP]
    groups = {}
    for _, r in q.iterrows():
        ids = r['lineup'].split('-')
        for i in range(5):
            four = tuple(ids[:i] + ids[i + 1:])
            groups.setdefault(four, []).append((ids[i], r))
    good = [(k, v) for k, v in groups.items() if len(v) >= 2]
    good.sort(key=lambda kv: -sum(r['poss'] for _, r in kv[1]))
    out, used = [], set()
    for k, v in good:
        if any(len(set(k) & set(u)) >= 4 for u in used):
            continue
        used.add(k)
        out.append((k, sorted(v, key=lambda t: -t[1]['poss'])[:5]))
        if len(out) == 2:
            break
    return out


def swap_html(groups):
    if not groups:
        return ''
    html_ = []
    for four, variants in groups:
        trs = []
        for fifth, r in variants:
            t, cls = fmt_net(r['net'])
            trs.append(f'<tr><td class="p">{face(fifth, 28)}<a href="{player_url(fifth)}">{esc(NAMES.get(fifth, fifth))}</a></td>'
                       f'<td class="n"><b class="{cls}">{t}</b></td><td class="n">{n0(r["poss"])}</td>'
                       f'<td class="n">{n0(r["secs"] / 60)}</td></tr>')
        html_.append(f'<div class="swapg"><p class="note">These four</p>{people(list(four), 36)}'
                     f'<table><thead><tr><th>Plus</th><th class="n" style="text-align:right">Net</th>'
                     f'<th style="text-align:right">Poss</th><th style="text-align:right">Min</th></tr></thead>'
                     f'<tbody>{"".join(trs)}</tbody></table></div>')
    return block('One player changes',
                 f'The same four players with a different fifth, each lineup with at least {T_SWAP} possessions. '
                 'This shows what happened, not why: opponents and game situations differ.',
                 ''.join(html_), 'changes')


def summary_card(title, r, duo=False):
    ids = [r['p1'], r['p2']] if duo else r['lineup'].split('-')
    return (f'<div class="row"><div class="rh"><h3>{esc(title)}</h3>{num_block(r)}</div>'
            f'{people(ids, 36)}{meta(r)}</div>')


def page_team_season(ctx, code, season):
    cur, lineups, duos, records = ctx['cur'], ctx['lineups'], ctx['duos'], ctx['records']
    L = lineups[(lineups['season'] == season) & (lineups['team'] == code)]
    D = duos[(duos['season'] == season) & (duos['team'] == code)]
    rec = records[(records['season'] == season) & (records['team'] == code)].iloc[0]
    played = int(rec['games'])
    tl = T_TEAM_LINEUP if season < cur else scaled(T_TEAM_LINEUP, played, MIN_POSS)
    td = T_TEAM_DUO if season < cur else scaled(T_TEAM_DUO, played, MIN_POSS)
    name = team_name(code, season)
    sl = season_label(season)
    rec_txt = f'{int(rec["wins"])}-{int(rec["losses"])}'
    q = L[L['poss'] >= tl]
    best = rank(q, 10)
    worst = rank(q, 5, asc=True) if len(q) >= 10 else q.iloc[0:0]
    used = by_minutes(L, 10)
    bd = rank(D[D['poss'] >= td], 10)
    lede = []
    if len(best):
        b = best.iloc[0]
        lede.append(f'Their highest-rated lineup with at least {tl:,} possessions was {esc(names_and(b["lineup"].split("-")))}, '
                    f'at {net_words(b["net"])} points per 100 possessions over {n0(b["secs"] / 60)} minutes.')
    if len(used):
        u = used.iloc[0]
        if len(best) and u['lineup'] == best.iloc[0]['lineup']:
            lede.append('It was also the five they used most.')
        else:
            pos = ''
            if u['poss'] >= tl and len(q):
                rk = int((q['net'] > u['net']).sum()) + 1
                pos = f', {rk}{"st" if rk % 10 == 1 and rk != 11 else "nd" if rk % 10 == 2 and rk != 12 else "rd" if rk % 10 == 3 and rk != 13 else "th"} of {len(q)} qualifying lineups'
            lede.append(f'The five they used most played {n0(u["secs"] / 60)} minutes at {net_words(u["net"])}{pos}.')
    summary = ''
    cards_ = []
    if len(best):
        cards_.append(summary_card('Highest-rated lineup', best.iloc[0]))
    if len(used):
        cards_.append(summary_card('Most-used lineup', used.iloc[0]))
    if len(bd):
        cards_.append(summary_card('Highest-rated duo', bd.iloc[0], duo=True))
    if cards_:
        summary = '<div class="summary">' + ''.join(cards_) + '</div>'
    panels = [('Best', f'Minimum {tl:,} possessions. {NET_NOTE}', rows(best, lineup_row, show_team=False)),
              ('Most used', 'By minutes played together. No minimum.', rows(used, lineup_row, show_team=False))]
    if len(worst):
        panels.append(('Worst', f'Minimum {tl:,} possessions.', rows(worst, lineup_row, show_team=False)))
    panels.append(('Duos', f'Minimum {td:,} possessions together. Team performance while both were on the floor.',
                   rows(bd, duo_row, show_team=False)))
    parts = [f'<p class="lede">{" ".join(lede)}</p>' if lede else '', summary, tabs(panels), swap_html(swap_groups(L))]
    slug = franchise(code)['slug']
    nav = []
    seasons = sorted(lineups[lineups['slug'] == slug]['season'].unique())
    i = seasons.index(season)
    if i > 0:
        nav.append((season_label(seasons[i - 1]), f'{SITE}/{slug}/{season_label(seasons[i - 1])}/'))
    if i < len(seasons) - 1:
        nav.append((season_label(seasons[i + 1]), f'{SITE}/{slug}/{season_label(seasons[i + 1])}/'))
    nav += [(f'All {franchise(code)["name"]} seasons', f'{SITE}/{slug}/'),
            (f'{sl} league leaders', f'{SITE}/season/{sl}/'), ('Best lineups, last 30 days', f'{SITE}/')]
    parts.append(links_block(nav))
    parts.append(f'<p class="note">{CREDIT}</p>')
    stamp = f' <span class="stamp">Games through {ap_date(ctx["end"])}.</span>' if season == cur and ctx['end'] else ''
    sub = (f'Team record {rec_txt}. Lineup lists need at least {tl:,} possessions, duos {td:,}.{stamp}')
    desc = (f'The best and worst lineups of the {sl} {name} ({rec_txt}) by net rating, '
            f'their most-used five-man units and their best duos.')
    return shell(f'/{slug}/{sl}/', f'{sl} {name} Lineups: Best Five-Man Units and Duos | HoopsMatic', desc,
                 f'{sl} {name} lineups', sub, '\n'.join(parts), active='teams')


def page_team(ctx, slug):
    cur, lineups, records = ctx['cur'], ctx['lineups'], ctx['records']
    t = SLUG_TEAM[slug]
    F = lineups[lineups['slug'] == slug]
    items = []
    for season in sorted(F['season'].unique(), reverse=True):
        S = F[F['season'] == season]
        code = S['team'].iloc[0]
        rec = records[(records['season'] == season) & (records['team'] == code)].iloc[0]
        tl = T_TEAM_LINEUP if season < cur else scaled(T_TEAM_LINEUP, int(rec['games']), MIN_POSS)
        b = rank(S[S['poss'] >= tl], 1)
        sl = season_label(season)
        head = (f'<div class="ssh"><a href="{SITE}/{slug}/{sl}/"><b>{sl} {esc(team_name(code, season))}</b></a>'
                f'<span class="meta">{int(rec["wins"])}-{int(rec["losses"])} · min {tl:,} poss</span></div>')
        if len(b):
            r = b.iloc[0]
            body = f'<div class="ssb">{people(r["lineup"].split("-"), 26)}{num_block(r, True)}</div>'
        else:
            body = '<p class="note">No lineup reached the minimum yet.</p>'
        items.append(f'<li>{head}{body}</li>')
    fin = F[(F['season'] < cur) & (F['poss'] >= T_FRANCHISE)]
    best = rank(fin, 10)
    car = (F.groupby('lineup', as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max')))
    car = add_ratings(car)
    used = by_minutes(car, 10)
    former = sorted({team_name(c, s) for c, s in zip(F['team'], F['season'])} - {t['name']})
    fm = f' Includes the {", the ".join(former)}.' if former else ''
    panels = [('Highest-rated since 1996-97', f'Single seasons, minimum {T_FRANCHISE:,} possessions, finished seasons only. {NET_NOTE}',
               rows(best, lineup_row, show=5)),
              ('Most minutes together', 'The same five, every season combined.',
               rows(used, lambda r, i, extra_cls='': career_row(r, i, t['abbr']).replace('class="row"', f'class="row {extra_cls}"'), show=5))]
    parts = [tabs(panels),
             block('Season by season', 'The highest-rated lineup that reached each season’s minimum. Open a season for all its lineups.',
                   '<ol class="ssn">' + ''.join(items) + '</ol>'),
             links_block([('Best lineups, last 30 days', f'{SITE}/'), ('Highest-rated lineups since 1996-97', f'{SITE}/all-time/'),
                          ('Best duos', f'{SITE}/duos/')]),
             f'<p class="note">{CREDIT}</p>']
    sub = f'The best five-man lineups in {esc(t["name"])} history, season by season since 1996-97.{esc(fm)}'
    return shell(f'/{slug}/', f'{t["name"]} Lineups: Best Five-Man Units Every Season Since 1996-97 | HoopsMatic',
                 f'The highest-rated {t["name"]} lineups since 1996-97 by net rating, the five-man units they played most and their best lineup in every season.',
                 f'{t["name"]} lineups', sub, '\n'.join(parts), active='teams')


def page_season(ctx, season):
    cur, lineups, duos, records = ctx['cur'], ctx['lineups'], ctx['duos'], ctx['records']
    L = lineups[lineups['season'] == season]
    D = duos[duos['season'] == season]
    played = float(records[records['season'] == season]['games'].mean())
    tl = T_SEASON_LINEUP if season < cur else scaled(T_SEASON_LINEUP, played, MIN_POSS)
    td = T_SEASON_DUO if season < cur else scaled(T_SEASON_DUO, played, MIN_POSS)
    q = L[L['poss'] >= tl]
    sl = season_label(season)
    panels = [('Best', f'Minimum {tl:,} possessions. {NET_NOTE}', rows(rank(q, 25), lineup_row, show=10)),
              ('Most used', 'By minutes played together. No minimum.', rows(by_minutes(L, 10), lineup_row)),
              ('Worst', f'Minimum {tl:,} possessions.', rows(rank(q, 10, asc=True), lineup_row)),
              ('Duos', f'Minimum {td:,} possessions together. Team performance while both were on the floor.',
               rows(rank(D[D['poss'] >= td], 15), duo_row, show=10))]
    seasons = [(season_label(s), f'{SITE}/season/{season_label(s)}/', s == season) for s in range(cur, FIRST_SEASON - 1, -1)]
    parts = [tabs(panels), block('Other seasons', None, chips(seasons)),
             links_block([('Best lineups, last 30 days', f'{SITE}/'), ('Highest-rated lineups since 1996-97', f'{SITE}/all-time/'),
                          ('Best duos', f'{SITE}/duos/')]),
             f'<p class="note">{CREDIT}</p>']
    stamp = f' <span class="stamp">Games through {ap_date(ctx["end"])}.</span>' if season == cur and ctx['end'] else ''
    sub = f'The best and worst five-man lineups of the {sl} NBA regular season, the most-used units and the top duos.{stamp}'
    return shell(f'/season/{sl}/', f'Best NBA Lineups of {sl}: Net Rating Leaders and Top Duos | HoopsMatic',
                 f'The best and worst NBA lineups of {sl} by net rating, the most-used five-man units and the best duos of the season.',
                 f'Best NBA lineups of {sl}', sub, '\n'.join(parts), active='season' if season == cur else None)


def page_alltime(ctx):
    cur, lineups = ctx['cur'], ctx['lineups']
    q = lineups[lineups['poss'] >= T_ALLTIME_LINEUP]
    car = (lineups.groupby(['slug', 'lineup'], as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max')))
    car = add_ratings(car)
    car['abbr'] = car['slug'].map(lambda s: SLUG_TEAM[s]['abbr'])
    panels = [('Highest-rated', f'Single seasons since 1996-97, minimum {T_ALLTIME_LINEUP:,} possessions. {NET_NOTE} '
                                'A historical record, not a prediction of how these lineups would do against each other.',
               rows(rank(q, 50), lineup_row, show=10)),
              ('Lowest-rated', f'Single seasons, minimum {T_ALLTIME_LINEUP:,} possessions.', rows(rank(q, 25, asc=True), lineup_row, show=10)),
              ('Most minutes together', 'The same five for the same franchise, every season combined.',
               rows(by_minutes(car, 50), lambda r, i, extra_cls='': career_row(r, i, r['abbr']).replace('class="row"', f'class="row {extra_cls}"'), show=10))]
    parts = [tabs(panels),
             links_block([('Best lineups, last 30 days', f'{SITE}/'), ('Best duos', f'{SITE}/duos/'),
                          (f'{season_label(cur)} leaders', f'{SITE}/season/{season_label(cur)}/')]),
             f'<p class="note">{CREDIT}</p>']
    return shell('/all-time/', 'Highest-Rated NBA Lineups Since 1996-97: Best Five-Man Units by Net Rating | HoopsMatic',
                 'The highest- and lowest-rated NBA five-man lineups of any season since 1996-97 by net rating, and the lineups that played the most minutes together.',
                 'Highest-rated NBA lineups since 1996-97',
                 'Single-season five-man units ranked by net rating, and the fives that stayed together longest.',
                 '\n'.join(parts), active='alltime')


def page_duos(ctx):
    cur, duos = ctx['cur'], ctx['duos']
    q = duos[duos['poss'] >= T_ALLTIME_DUO]
    car = (duos.groupby(['slug', 'p1', 'p2'], as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max')))
    car = add_ratings(car)
    by = car.sort_values(['secs', 'p1'], ascending=[False, True]).head(25)

    def car_row(r, i, extra_cls=''):
        t = SLUG_TEAM[r['slug']]
        seasons = (season_label(r['first']) if r['first'] == r['last']
                   else f'{season_label(r["first"])} to {season_label(r["last"])}')
        return duo_row(r, i, extra_cls=extra_cls, label=f'{t["name"]}, {seasons}')
    played = float(ctx['records'][ctx['records']['season'] == cur]['games'].mean())
    td = scaled(T_SEASON_DUO, played, MIN_POSS)
    C = duos[(duos['season'] == cur) & (duos['poss'] >= td)]
    expl = 'Team performance while both players were on the floor, whoever the other three teammates were.'
    panels = [(f'{season_label(cur)}', f'{expl} Minimum {td:,} possessions together. {NET_NOTE}', rows(rank(C, 15), duo_row, show=10)),
              ('Best single seasons', f'{expl} Minimum {T_ALLTIME_DUO:,} possessions together in one season.', rows(rank(q, 50), duo_row, show=10)),
              ('Most minutes together', 'The same two for the same franchise, every season combined.', rows(by, car_row, show=10))]
    parts = [tabs(panels),
             links_block([('Best lineups, last 30 days', f'{SITE}/'), ('Highest-rated lineups since 1996-97', f'{SITE}/all-time/'),
                          ('Players', f'{SITE}/players/')]),
             f'<p class="note">{CREDIT}</p>']
    return shell('/duos/', 'Best NBA Duos by Net Rating Since 1996-97 | HoopsMatic',
                 'The best NBA duos by net rating: this season’s leaders, the best two-man pairings of any season since 1996-97 and the duos that played the most minutes together.',
                 'Best NBA duos', 'How teams did with both players on the floor, this season and since 1996-97.',
                 '\n'.join(parts), active='duos')


def page_players(ctx):
    car, eligible = ctx['career'], ctx['eligible']
    ps = ctx['player_seasons']
    span = ps.groupby('pid')['season'].agg(['min', 'max'])
    rows_ = []
    for pid in sorted(eligible, key=lambda p: (last_name(NAMES.get(p, p)).lower(), NAMES.get(p, p).lower())):
        n = NAMES.get(pid, pid)
        a, b = span.loc[pid, 'min'], span.loc[pid, 'max']
        yrs = season_label(a) if a == b else f'{season_label(a)} to {season_label(b)}'
        key = ascii_slug(n).replace('-', ' ')
        rows_.append(f'<li data-n="{esc(key)}"><a href="{player_url(pid)}">{esc(n)}</a> '
                     f'<span class="yrs">{yrs}</span></li>')
    inner = (f'<label for="find" class="note">Find a player</label><input id="find" class="find" type="search" '
             f'placeholder="Type a name" autocomplete="off"><ul class="az">{"".join(rows_)}</ul>')
    return shell('/players/', 'NBA Players: Lineups and Best Teammates Since 1996-97 | HoopsMatic',
                 'Every NBA player with 2,000-plus minutes since 1996-97: their best teammates by net rating, season by season, and the lineups they played in.',
                 'Players', f'Every player with at least {PLAYER_INDEX_MIN:,} minutes in lineups since 1996-97, by last name. '
                            'Every other player’s page is linked from his name in any lineup.',
                 block('All players', None, inner), active='players')


def page_player(ctx, pid):
    lineups, duos = ctx['lineups'], ctx['duos']
    ps = ctx['player_seasons']
    n = NAMES.get(pid, pid)
    mine = ctx['ps_by'].get_group(pid).sort_values(['season', 'secs'], ascending=[False, False])
    mins = mine['secs'].sum() / 60
    first, last = int(mine['season'].min()), int(mine['season'].max())
    yrs = season_label(first) if first == last else f'{season_label(first)} to {season_label(last)}'
    teams_ = []
    for code, season in zip(mine.sort_values('season')['team'], mine.sort_values('season')['season']):
        tn = nickname(code, int(season))
        if tn not in teams_:
            teams_.append(tn)
    PD = duos.loc[ctx['duo_idx'].get(pid, [])].copy()
    PD['mate'] = np.where(PD['p1'] == pid, PD['p2'], PD['p1'])
    car = (PD.groupby('mate', as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max'),
                  team=('team', 'last')))
    car = add_ratings(car)
    car['p1'] = pid
    car['p2'] = car['mate']
    best_mates = rank(car[car['poss'] >= T_PLAYER_DUO], 10)
    most_mates = by_minutes(car, 10)

    def mate_row(r, i, extra_cls=''):
        seasons = (season_label(r['first']) if r['first'] == r['last']
                   else f'{season_label(r["first"])} to {season_label(r["last"])}')
        rr = dict(r)
        rr['p1'], rr['p2'], rr['slug'] = pid, r['mate'], franchise(r['team'])['slug']
        return duo_row(rr, i, extra_cls=extra_cls, label=seasons)
    # season by season
    items = []
    for _, s in mine.iterrows():
        season, code = int(s['season']), s['team']
        sd = PD[(PD['season'] == season) & (PD['team'] == code) & (PD['poss'] >= T_PLAYER_SEASON_DUO)]
        b = rank(sd, 1)
        head = (f'<div class="ssh"><a href="{team_season_url(code, season)}"><b>{season_label(season)} '
                f'{esc(team_name(code, season))}</b></a><span class="meta">{n0(s["secs"] / 60)} min</span></div>')
        if len(b):
            r = b.iloc[0]
            body = (f'<div class="ssb"><span class="note" style="margin:0">Best with</span>'
                    f'{people([r["mate"]], 26)}{num_block(r, True)}</div>')
        else:
            body = f'<p class="note" style="margin:.2rem 0 0">No teammate reached {T_PLAYER_SEASON_DUO} possessions together.</p>'
        items.append(f'<li>{head}{body}</li>')
    ML = lineups.loc[np.unique(ctx['lin_idx'].get(pid, []))]
    best_l = rank(ML[ML['poss'] >= T_PLAYER_LINEUP], 10)
    used_l = by_minutes(ML, 5)
    lede = []
    if len(best_mates):
        b = best_mates.iloc[0]
        lede.append(f'His highest-rated teammate with at least {T_PLAYER_DUO:,} possessions together was '
                    f'{esc(NAMES.get(b["mate"], b["mate"]))}: {net_words(b["net"])} per 100 possessions over {n0(b["secs"] / 60)} minutes.')
    if len(most_mates):
        m = most_mates.iloc[0]
        lede.append(f'He played the most minutes with {esc(NAMES.get(m["mate"], m["mate"]))} ({n0(m["secs"] / 60)}).')
    panels = [('Best teammates', f'Career, minimum {T_PLAYER_DUO:,} possessions together. How his teams did with both on the floor. {NET_NOTE}',
               rows(best_mates, mate_row, show=5)),
              ('Most minutes', 'Teammates he played the most minutes with, career.', rows(most_mates, mate_row, show=5)),
              ('Best lineups', f'Single seasons, minimum {T_PLAYER_LINEUP} possessions.', rows(best_l, lineup_row, show=5)),
              ('Most used', 'His most-used lineups, single seasons, by minutes.', rows(used_l, lineup_row))]
    parts = [f'<p class="lede">{" ".join(lede)}</p>' if lede else '', tabs(panels),
             block('Season by season', f'Each team and season, with his highest-rated teammate that year (minimum {T_PLAYER_SEASON_DUO} possessions together).',
                   '<ol class="ssn">' + ''.join(items) + '</ol>'),
             links_block([('All players', f'{SITE}/players/'), ('Best duos', f'{SITE}/duos/'), ('Best lineups, last 30 days', f'{SITE}/')]),
             f'<p class="note">{CREDIT}</p>']
    sub = f'{yrs} · {", ".join(teams_)} · {n0(mins)} minutes in the data (regular season, 1996-97 onward).'
    indexed = pid in ctx['indexed']
    return shell(f'/player/{PSLUG[pid]}/', f'{n} Lineups: Best Teammates and Five-Man Units, {yrs} | HoopsMatic',
                 f'{n}’s best teammates by net rating, the lineups he played in and his best partner in every season, {yrs}.',
                 f'{n} lineups', esc(sub), '\n'.join(parts), active='players', noindex=not indexed), indexed


def page_404():
    return shell('/404.html', 'Page not found | HoopsMatic', 'This lineup page does not exist.', 'Page not found',
                 f'That page does not exist. Try the <a href="{SITE}/">best lineups of the last 30 days</a> or '
                 f'<a href="{SITE}/all-time/">the highest-rated lineups since 1996-97</a>.', '', noindex=True)


# ── output ────────────────────────────────────────────────────────────────

def write(rel, text, pages):
    p = os.path.join(OUT, rel.lstrip('/'))
    if p.endswith('/'):
        p = os.path.join(p, 'index.html')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    if pages is not None:
        pages[rel] = hashlib.sha256(text.encode('utf-8')).hexdigest()


def sitemap(pages, today):
    """lastmod moves only for a page whose bytes changed (data/page_hashes.json)."""
    p = os.path.join(DATA, 'page_hashes.json')
    old = {}
    if os.path.exists(p):
        with open(p) as f:
            old = json.load(f)
    new = {}
    for rel in sorted(pages):
        h = pages[rel]
        prev = old.get(rel)
        new[rel] = {'hash': h, 'lastmod': prev['lastmod'] if prev and prev['hash'] == h else today}
    with open(p, 'w') as f:
        json.dump(new, f, indent=0, sort_keys=True)
        f.write('\n')
    body = '\n'.join(f'<url><loc>{SITE}{rel}</loc><lastmod>{v["lastmod"]}</lastmod></url>'
                     for rel, v in new.items())
    with open(os.path.join(OUT, 'sitemap.xml'), 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + '\n</urlset>\n')


FONT_V = None
FACE_V = None
NAMES = {}
PSLUG = {}
SWAPS = {}
CTX = {}


def build(today=None):
    global FONT_V, FACE_V, NAMES, PSLUG, SWAPS, CTX
    FONT_V = font_v()
    FACE_V = faces_v()
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    lineups, records, cur_games, cur, names = load_seasons()
    NAMES = names
    duos = duos_of(lineups)
    ps, career, slugs, indexed, eligible, lin_idx, duo_idx = build_players(lineups, duos)
    PSLUG = slugs
    SWAPS = swap_index(lineups)
    end = cur_games['game_date'].max() if cur_games is not None and len(cur_games) else None
    CTX = dict(cur=cur, lineups=lineups, duos=duos, records=records, recent=recent(cur_games, cur), end=end,
               player_seasons=ps, career=career, indexed=indexed, eligible=eligible,
               lin_idx=lin_idx, duo_idx=duo_idx)
    CTX['ps_by'] = ps.groupby('pid')
    ctx = CTX
    pages = {}
    write('/', page_index(ctx), pages)
    write('/all-time/', page_alltime(ctx), pages)
    write('/duos/', page_duos(ctx), pages)
    write('/players/', page_players(ctx), pages)
    for s in sorted(lineups['season'].unique()):
        write(f'/season/{season_label(s)}/', page_season(ctx, s), pages)
    for slug in sorted(SLUG_TEAM):
        write(f'/{slug}/', page_team(ctx, slug), pages)
    for (s, code) in sorted(set(zip(lineups['season'], lineups['team']))):
        write(f'/{franchise(code)["slug"]}/{season_label(s)}/', page_team_season(ctx, code, s), pages)
    n_noindex = 0
    for pid in sorted(slugs):
        html_, idx = page_player(ctx, pid)
        write(f'/player/{slugs[pid]}/', html_, pages if idx else None)
        n_noindex += not idx
    write('/404.html', page_404(), None)
    with open(os.path.join(OUT, '.nojekyll'), 'w') as f:
        f.write('')
    sitemap(pages, today)
    print(f'{len(pages)} indexable pages, {n_noindex} noindex player pages, games through {end}')
    return ctx, pages


if __name__ == '__main__':
    build()
    sys.exit(0)
