"""Build Lineup Lab: every NBA five-man lineup and duo since 1996-97.

Reads only what is in this repo (data/raw, data/current, data/teams.json,
data/headshots.json) and writes the static site into docs/, which GitHub Pages
serves and the hoopsmatic.com Worker proxies at https://hoopsmatic.com/lineups/.
No network access here; the download step is lineup_lab_update.py.

Pages (all canonical on hoopsmatic.com):
  /lineups/                       hottest lineups this week, teams, seasons, method
  /lineups/all-time/              best and worst single-season lineups, most minutes ever
  /lineups/duos/                  best single-season duos, most minutes ever, this season
  /lineups/season/<1999-00>/      league leaders for one season
  /lineups/<team>/                one franchise, best lineup every season
  /lineups/<team>/<1999-00>/      one team-season
  /lineups/sitemap.xml            per-URL <lastmod> that moves only when that page changes

Season numbers inside the code follow the source: 2027 means 2026-27.
"""
import datetime as dt
import hashlib
import html
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'docs')
SITE = 'https://hoopsmatic.com/lineups'
HS = 'https://jsierrahoopshype.github.io/nba-headshots'
SILHOUETTE = HS + '/fallbacks/player_silhouette.svg'
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
T_FRANCHISE = 500        # team hub, franchise best ever (finished seasons only)
T_ALLTIME_LINEUP = 1000  # all-time single-season lineups
T_ALLTIME_DUO = 2500     # all-time single-season duos
HOT_MIN_POSS = 30        # hottest lineups, in the window
HOT_MIN_COUNT = 5        # fewer qualifiers than this widens the window

# Codes the source uses for seasons before a move or rename, mapped to the
# franchise's current abbreviation (NBA franchise history: the 1988-2002
# Charlotte Hornets belong to today's Hornets; New Orleans 2002-2013 is the
# Pelicans).
HISTORIC = {'CHH': 'CHA', 'NOH': 'NOP', 'NOK': 'NOP', 'NJN': 'BKN', 'SEA': 'OKC', 'VAN': 'MEM'}
MONTHS = ['Jan.', 'Feb.', 'March', 'April', 'May', 'June', 'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.']

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
    """Returns (lineups, records, current_games, cur, names).

    lineups: one row per season, team code and lineup with totals and games.
    records: one row per season and team code, with wins, losses and games.
    current_games: the season in progress, one row per game, team and lineup.
    """
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
        # A game's score is the sum of its stints, overtime included.
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
    d = df.sort_values(['net', 'poss', keycol], ascending=[asc, False, True])
    return d.head(n)


def by_minutes(df, n):
    keycol = 'lineup' if 'lineup' in df.columns else 'p1'
    return df.sort_values(['secs', keycol], ascending=[False, True]).head(n)


def scaled(full, played, floor):
    """Threshold for the season in progress: full * share of 82 games, floored."""
    return max(floor, int(round(full * min(1.0, played / 82.0))))


def hottest(cur_games, cur):
    """The best lineups over the last 7 days of games (14, then the season, if
    too few qualify). Returns (rows, start_iso, end_iso, min_poss, widened)."""
    if cur_games is None or cur_games.empty:
        return None
    end = cur_games['game_date'].max()
    end_d = dt.date.fromisoformat(end)
    first = cur_games['game_date'].min()
    for days in (7, 14, None):
        start = first if days is None else max(first, (end_d - dt.timedelta(days=days - 1)).isoformat())
        w = cur_games[(cur_games['game_date'] >= start) & (cur_games['game_date'] <= end)]
        g = (w.groupby(['team', 'lineup'], as_index=False)
               .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                    opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), games=('game_id', 'nunique')))
        g = add_ratings(g)
        g['season'] = cur
        g['slug'] = g['team'].map(lambda c: franchise(c)['slug'])
        q = g[g['poss'] >= HOT_MIN_POSS]
        if len(q) >= HOT_MIN_COUNT or days is None:
            return rank(q, 10), start, end, HOT_MIN_POSS, days


# ── html ──────────────────────────────────────────────────────────────────

def load_headshots():
    p = os.path.join(DATA, 'headshots.json')
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


HEADSHOTS = load_headshots()


def face(pid, name):
    stem = HEADSHOTS.get(pid)
    src = f'{HS}/players/headshots/face2-160/{stem}.webp' if stem else SILHOUETTE
    return (f'<img src="{src}" alt="" title="{esc(name)}" width="40" height="40" loading="lazy" '
            f'decoding="async" onerror="this.onerror=null;this.src=\'{SILHOUETTE}\'">')


def logo(code, size=22):
    return (f'<img src="{HS}/teams/logos/current/svg/{franchise(code)["nba_id"]}.svg" alt="" '
            f'width="{size}" height="{size}" loading="lazy">')


def net_html(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return '<span class="net dash">n/a</span>'
    cls = 'pos' if v > 0.05 else 'neg' if v < -0.05 else 'dash'
    sign = '+' if v > 0.05 else '−' if v < -0.05 else ''
    return f'<span class="net {cls}" title="Net rating">{sign}{abs(v):.1f}</span>'


def n0(v):
    return f'{int(round(v)):,}'


def r1(v):
    return 'n/a' if v is None or np.isnan(v) else f'{v:.1f}'


def team_season_url(code, season):
    return f'{SITE}/{franchise(code)["slug"]}/{season_label(season)}/'


def stat_line(r, games=True):
    parts = [f'Off {r1(r["ortg"])}', f'Def {r1(r["drtg"])}',
             f'{n0(r["secs"] / 60)} min', f'{n0(r["poss"])} poss']
    if games and 'games' in r and not pd.isna(r['games']):
        g = int(r['games'])
        parts.append(f'{g} game' + ('' if g == 1 else 's'))
    return ' · '.join(parts)


def lineup_card(r, names, i, show_team=True):
    ids = r['lineup'].split('-')
    nm = [names.get(p, p) for p in ids]
    top = f'<span class="rk">{i}</span>'
    if show_team:
        top += (f'<a class="tm" href="{team_season_url(r["team"], r["season"])}">{logo(r["team"])}'
                f'<span>{season_label(r["season"])} {esc(team_name(r["team"], r["season"]))}</span></a>')
    else:
        top += '<span class="tm sp"></span>'
    return ('<li class="lu"><div class="lu-top">' + top + net_html(r['net']) + '</div>'
            '<div class="faces">' + ''.join(face(p, n) for p, n in zip(ids, nm)) + '</div>'
            f'<p class="nm">{esc(", ".join(nm))}</p>'
            f'<p class="st">{stat_line(r)}</p></li>')


def duo_card(r, names, i, show_team=True):
    ids = [r['p1'], r['p2']]
    nm = [names.get(p, p) for p in ids]
    top = f'<span class="rk">{i}</span>'
    if show_team:
        top += (f'<a class="tm" href="{team_season_url(r["team"], r["season"])}">{logo(r["team"])}'
                f'<span>{season_label(r["season"])} {esc(team_name(r["team"], r["season"]))}</span></a>')
    else:
        top += '<span class="tm sp"></span>'
    return ('<li class="lu"><div class="lu-top">' + top + net_html(r['net']) + '</div>'
            '<div class="faces">' + ''.join(face(p, n) for p, n in zip(ids, nm)) + '</div>'
            f'<p class="nm">{esc(" and ".join(nm))}</p>'
            f'<p class="st">{stat_line(r, games=False)}</p></li>')


def career_card(r, names, i):
    """A lineup across every season one franchise used it."""
    ids = r['lineup'].split('-')
    nm = [names.get(p, p) for p in ids]
    t = TEAMS[r['abbr']]
    seasons = (season_label(r['first']) if r['first'] == r['last']
               else f'{season_label(r["first"])} to {season_label(r["last"])}')
    return ('<li class="lu"><div class="lu-top">'
            f'<span class="rk">{i}</span><a class="tm" href="{SITE}/{t["slug"]}/">{logo(r["abbr"])}'
            f'<span>{esc(t["name"])}, {seasons}</span></a>' + net_html(r['net']) + '</div>'
            '<div class="faces">' + ''.join(face(p, n) for p, n in zip(ids, nm)) + '</div>'
            f'<p class="nm">{esc(", ".join(nm))}</p>'
            f'<p class="st">{stat_line(r, games=False)}</p></li>')


def cards(rows, fn, names, **kw):
    if rows is None or len(rows) == 0:
        return '<p class="note">Not enough possessions yet.</p>'
    return ('<ol class="cards">' +
            ''.join(fn(r, names, i + 1, **kw) for i, (_, r) in enumerate(rows.iterrows())) + '</ol>')


def block(title, note, inner, anchor=None):
    a = f' id="{anchor}"' if anchor else ''
    n = f'<p class="note">{note}</p>' if note else ''
    return f'<section class="block"{a}><div class="block-head"><h2>{title}</h2></div>{n}{inner}</section>'


def chips(items):
    return '<div class="chips">' + ''.join(
        f'<a class="chip{" active" if act else ""}" href="{u}">{esc(t)}</a>' for t, u, act in items) + '</div>'


NET_NOTE = 'Net rating: points scored minus points allowed per 100 possessions.'


def font_v():
    h = hashlib.sha256()
    for fn in ('dm-sans-latin.woff2', 'jetbrains-mono-latin.woff2'):
        with open(os.path.join(OUT, 'fonts', fn), 'rb') as f:
            h.update(f.read())
    return h.hexdigest()[:10]


CSS = """
:root{--bg:#f5f5f7;--surface:#fff;--surface-hover:#f0f0f2;--border:#d1d1d6;--text:#1d1d1f;
--text-secondary:#6e6e73;--accent:#3b82f6;--green:#1d8a40;--red:#d12c2c}
*{margin:0;padding:0;box-sizing:border-box}
html{font-size:115%;-webkit-text-size-adjust:100%}
body{font-family:'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);
min-height:100vh;line-height:1.5;-webkit-font-smoothing:antialiased;overflow-x:hidden}
.container{max-width:980px;margin:0 auto;padding:1rem 16px 3rem}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.hdr h1{font-size:1.15rem;font-weight:700;letter-spacing:-.03em;margin-bottom:.35rem}
.subtitle{color:var(--text-secondary);font-size:.82rem;margin-bottom:1.1rem;max-width:60rem}
.stamp{font-family:'JetBrains Mono',monospace;font-size:.74rem}
.block{margin-bottom:1.5rem}
.block-head h2{font-size:.95rem;font-weight:700;letter-spacing:-.01em;margin-bottom:.3rem}
.note{font-size:.76rem;color:var(--text-secondary);margin-bottom:.5rem}
.lede{font-size:.86rem;margin-bottom:.9rem;max-width:60rem}
ol.cards{list-style:none;display:grid;grid-template-columns:minmax(0,1fr);gap:.5rem}
@media(min-width:720px){ol.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.lu{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.6rem .75rem;min-width:0}
.lu-top{display:flex;align-items:center;gap:.5rem;min-width:0}
.rk{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--text-secondary);min-width:1.3rem}
.tm{display:flex;align-items:center;gap:.4rem;font-weight:600;font-size:.8rem;color:var(--text);min-width:0;flex:1}
.tm span{overflow-wrap:anywhere}.tm img{width:22px;height:22px;flex-shrink:0}
a.tm:hover{color:var(--accent);text-decoration:none}
.net{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:1rem;margin-left:auto;white-space:nowrap}
.pos{color:var(--green)}.neg{color:var(--red)}.dash{color:var(--text-secondary)}
.faces{display:flex;flex-wrap:wrap;gap:.3rem;margin:.5rem 0 .35rem}
.faces img{width:40px;height:40px;border-radius:50%;background:#e5e5ea;object-fit:cover;flex-shrink:0}
.nm{font-size:.84rem;line-height:1.35;overflow-wrap:anywhere}
.st{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--text-secondary);margin-top:.25rem;overflow-wrap:anywhere}
.chips{display:flex;flex-wrap:wrap;gap:.4rem}
.chip{display:inline-block;border:1px solid var(--border);background:var(--surface);border-radius:999px;
padding:.28rem .7rem;font-size:.76rem;color:var(--text);white-space:nowrap}
.chip.active{background:var(--accent);border-color:var(--accent);color:#fff}
a.chip:hover{text-decoration:none;border-color:var(--accent)}
.teams{list-style:none;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.4rem}
@media(min-width:560px){.teams{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:860px){.teams{grid-template-columns:repeat(5,minmax(0,1fr))}}
.teams a{display:flex;align-items:center;gap:.45rem;background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:.45rem .55rem;font-size:.8rem;font-weight:600;color:var(--text);min-width:0}
.teams a span{overflow-wrap:anywhere}.teams img{width:24px;height:24px;flex-shrink:0}
.teams a:hover{text-decoration:none;border-color:var(--accent)}
.how{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.7rem .85rem}
.how dt{font-weight:700;font-size:.82rem;margin-top:.55rem}.how dt:first-child{margin-top:0}
.how dd{font-size:.8rem;color:var(--text-secondary)}
.more{display:flex;flex-wrap:wrap;gap:.4rem}
.ssn{font-size:.86rem;font-weight:700;margin:.9rem 0 .35rem}.ssn:first-child{margin-top:0}
.ssn .note{display:inline;font-weight:400;margin:0}
.foot{text-align:center;font-size:.8125rem;line-height:1.6;color:var(--text-secondary);padding:1.5rem 1rem 0}
"""


def shell(path, title, desc, h1, subtitle, body, noindex=False):
    v = FONT_V
    url = SITE + path
    rob = '<meta name="robots" content="noindex">\n' if noindex else ''
    fonts = (f"@font-face{{font-family:'DM Sans';src:url({SITE}/fonts/dm-sans-latin.woff2?v={v}) format('woff2');"
             "font-weight:100 1000;font-style:normal;font-display:swap}"
             f"@font-face{{font-family:'JetBrains Mono';src:url({SITE}/fonts/jetbrains-mono-latin.woff2?v={v}) format('woff2');"
             "font-weight:100 800;font-style:normal;font-display:swap}")
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
{body}
<footer class="foot">
<p>Anything wrong? Email us at <a href="mailto:hoopsmatic@hoopsmatic.com">hoopsmatic@hoopsmatic.com</a></p>
<p>For business inquiries, contact us at <a href="mailto:ads@hoopsmatic.com">ads@hoopsmatic.com</a></p>
</footer>
</div>
</body>
</html>
"""


CREDIT = ('Lineup data from <a href="https://github.com/ramirobentes/nbadatar">Ramiro Bentes</a> '
          '(<a href="https://x.com/NbaInRstats">@NbaInRstats</a>), built from NBA play-by-play. '
          f'Regular season only. <a href="{SITE}/#how">How these numbers work</a>.')


def links_block(items):
    return block('More lineups', None, '<div class="more">' + chips([(t, u, False) for t, u in items]) + '</div>')


# ── pages ─────────────────────────────────────────────────────────────────

def page_index(ctx):
    names, cur, lineups, hot, end = ctx['names'], ctx['cur'], ctx['lineups'], ctx['hot'], ctx['end']
    parts = []
    if hot:
        rows, start, stop, mp, days = hot
        if days == 7:
            title = 'Hottest lineups this week'
        elif days == 14:
            title = 'Hottest lineups of the last two weeks'
        else:
            title = f'Hottest lineups of {season_label(cur)} so far'
        span = ap_day(start) if start == stop else f'{ap_day(start)} to {ap_day(stop)}'
        note = (f'The best five-man units in games from {span}, minimum {mp} possessions. '
                f'Refreshed every day of the season. {NET_NOTE}')
        parts.append(block(title, note, cards(rows, lineup_card, names), 'hottest'))
    teams = sorted(TEAMS.values(), key=lambda t: t['name'])
    tiles = ''.join(
        f'<li><a href="{SITE}/{t["slug"]}/"><img src="{HS}/teams/logos/current/svg/{t["nba_id"]}.svg" alt="" '
        f'width="24" height="24" loading="lazy"><span>{esc(t["name"])}</span></a></li>' for t in teams)
    parts.append(block('Every team, every season', 'The best lineup each team had in every season since 1996-97.',
                       f'<ul class="teams">{tiles}</ul>'))
    seasons = [(season_label(s), f'{SITE}/season/{season_label(s)}/', s == cur) for s in range(cur, FIRST_SEASON - 1, -1)]
    parts.append(block('League leaders by season', None, chips(seasons)))
    parts.append(block('All-time', None, '<div class="more">' + chips([
        ('Best lineups ever', f'{SITE}/all-time/', False),
        ('Best duos ever', f'{SITE}/duos/', False),
        (f'{season_label(cur)} leaders', f'{SITE}/season/{season_label(cur)}/', False),
        ('Build your own five in Lineup Forge', FORGE, False)]) + '</div>'))
    how = f"""<dl class="how">
<dt>Lineup</dt><dd>Five players on the floor together for one team. Every stretch of a game where the same five stayed on the court counts toward it.</dd>
<dt>Net rating</dt><dd>Points the lineup scored per 100 possessions (Off) minus points it allowed per 100 possessions (Def). +10 means it outscored opponents by 10 points every 100 trips. Garbage time is included.</dd>
<dt>Minimums</dt><dd>A small sample can make any five look great, so each list needs a number of possessions, shown above it. For the season in progress the minimum grows with the games played, so early lists fill up and later ones tighten.</dd>
<dt>Duos</dt><dd>Two players, counted whenever both were in the same five, whoever the other three were.</dd>
<dt>Hottest lineups</dt><dd>The last seven days with games. If fewer than {HOT_MIN_COUNT} lineups reach {HOT_MIN_POSS} possessions, the window widens to 14 days, then to the season.</dd>
<dt>Records</dt><dd>Win-loss records are added up from the same play-by-play, overtime included.</dd>
<dt>Source</dt><dd>{CREDIT.replace(f' <a href="{SITE}/#how">How these numbers work</a>.', '')} Season {season_label(FIRST_SEASON)} onward. Updated every day of the season.</dd>
</dl>"""
    parts.append(block('How the numbers work', None, how, 'how'))
    stamp = f'<span class="stamp">Games through {ap_date(end)}.</span>' if end else ''
    sub = f'Every NBA five-man lineup and duo since 1996-97, ranked by net rating. {stamp}'
    return shell('/', 'NBA Lineup Stats: Hottest Lineups This Week and Every Five-Man Unit Since 1996-97 | HoopsMatic',
                 'The best NBA lineups this week, every team’s best five-man unit in every season since 1996-97, '
                 'the best lineups and duos ever by net rating, updated every day of the season.',
                 'NBA Lineups', sub, '\n'.join(parts))


def page_team_season(ctx, code, season):
    names, cur, lineups, duos, records = ctx['names'], ctx['cur'], ctx['lineups'], ctx['duos'], ctx['records']
    L = lineups[(lineups['season'] == season) & (lineups['team'] == code)]
    D = duos[(duos['season'] == season) & (duos['team'] == code)]
    rec = records[(records['season'] == season) & (records['team'] == code)].iloc[0]
    played = int(rec['games'])
    tl = T_TEAM_LINEUP if season < cur else scaled(T_TEAM_LINEUP, played, 20)
    td = T_TEAM_DUO if season < cur else scaled(T_TEAM_DUO, played, 50)
    name = team_name(code, season)
    sl = season_label(season)
    rec_txt = f'{int(rec["wins"])}-{int(rec["losses"])}'
    q = L[L['poss'] >= tl]
    best = rank(q, 10)
    worst = rank(q, 5, asc=True) if len(q) >= 10 else q.iloc[0:0]
    used = by_minutes(L, 5)
    bd = rank(D[D['poss'] >= td], 10)
    lede = ''
    if len(best):
        b = best.iloc[0]
        nm = [names.get(p, p) for p in b['lineup'].split('-')]
        sign = '+' if b['net'] >= 0 else '−'
        lede = (f'<p class="lede">Their best lineup with at least {tl:,} possessions was '
                f'{esc(", ".join(nm[:-1]))} and {esc(nm[-1])}, at {sign}{abs(b["net"]):.1f} points per 100 possessions '
                f'over {n0(b["secs"] / 60)} minutes.</p>')
    parts = [lede,
             block('Best lineups', f'Minimum {tl:,} possessions. {NET_NOTE}', cards(best, lineup_card, names, show_team=False))]
    if len(worst):
        parts.append(block('Worst lineups', f'Minimum {tl:,} possessions.', cards(worst, lineup_card, names, show_team=False)))
    parts.append(block('Most-used lineups', 'By minutes played together.', cards(used, lineup_card, names, show_team=False)))
    parts.append(block('Best duos', f'Minimum {td:,} possessions together.', cards(bd, duo_card, names, show_team=False)))
    slug = franchise(code)['slug']
    nav = []
    seasons = sorted(lineups[lineups['slug'] == slug]['season'].unique())
    i = seasons.index(season)
    if i > 0:
        nav.append((f'{season_label(seasons[i - 1])}', f'{SITE}/{slug}/{season_label(seasons[i - 1])}/'))
    if i < len(seasons) - 1:
        nav.append((f'{season_label(seasons[i + 1])}', f'{SITE}/{slug}/{season_label(seasons[i + 1])}/'))
    nav += [(f'All {franchise(code)["name"]} seasons', f'{SITE}/{slug}/'),
            (f'{sl} league leaders', f'{SITE}/season/{sl}/'), ('Hottest lineups this week', f'{SITE}/')]
    parts.append(links_block(nav))
    parts.append(f'<p class="note">{CREDIT}</p>')
    stamp = f' <span class="stamp">Games through {ap_date(ctx["end"])}.</span>' if season == cur and ctx['end'] else ''
    sub = (f'Every five-man lineup the {sl} {esc(name)} ({rec_txt}) used, ranked by net rating, '
           f'with their most-used units and best duos.{stamp}')
    desc = (f'The best and worst lineups of the {sl} {name} ({rec_txt}) by net rating, '
            f'their most-used five-man units and their best duos.')
    return shell(f'/{slug}/{sl}/', f'{sl} {name} Lineups: Best Five-Man Units and Duos | HoopsMatic', desc,
                 f'{sl} {name} lineups', sub, '\n'.join(parts))


def page_team(ctx, slug):
    names, cur, lineups, records = ctx['names'], ctx['cur'], ctx['lineups'], ctx['records']
    t = SLUG_TEAM[slug]
    F = lineups[lineups['slug'] == slug]
    rows = []
    for season in sorted(F['season'].unique(), reverse=True):
        S = F[F['season'] == season]
        code = S['team'].iloc[0]
        rec = records[(records['season'] == season) & (records['team'] == code)].iloc[0]
        tl = T_TEAM_LINEUP if season < cur else scaled(T_TEAM_LINEUP, int(rec['games']), 20)
        b = rank(S[S['poss'] >= tl], 1)
        sl = season_label(season)
        head = (f'<h3 class="ssn"><a href="{SITE}/{slug}/{sl}/">{sl} {esc(team_name(code, season))}</a> '
                f'<span class="note">({int(rec["wins"])}-{int(rec["losses"])})</span></h3>')
        rows.append(head + (cards(b, lineup_card, names, show_team=False) if len(b) else
                            '<p class="note">Not enough possessions yet.</p>'))
    fin = F[(F['season'] < cur) & (F['poss'] >= T_FRANCHISE)]
    best = rank(fin, 10)
    car = (F.groupby('lineup', as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max')))
    car = add_ratings(car)
    car['abbr'] = t['abbr']
    used = by_minutes(car, 5)
    former = sorted({team_name(c, s) for c, s in zip(F['team'], F['season'])} - {t['name']})
    fm = f' Includes the {", the ".join(former)}.' if former else ''
    parts = [block('Franchise best ever', f'Single seasons, minimum {T_FRANCHISE:,} possessions. {NET_NOTE}',
                   cards(best, lineup_card, names)),
             block('Most minutes together', 'Every season combined.', cards(used, career_card, names)),
             block('Best lineup every season', 'Tap a season for its full lineup page.',
                   '<div class="ssns">' + ''.join(rows) + '</div>'),
             links_block([('Hottest lineups this week', f'{SITE}/'), ('Best lineups ever', f'{SITE}/all-time/'),
                          ('Best duos ever', f'{SITE}/duos/')]),
             f'<p class="note">{CREDIT}</p>']
    sub = f'The best five-man lineups in {esc(t["name"])} history, season by season since 1996-97.{esc(fm)}'
    return shell(f'/{slug}/', f'{t["name"]} Lineups: Best Five-Man Units Every Season Since 1996-97 | HoopsMatic',
                 f'The best {t["name"]} lineups ever by net rating, the five-man units they played most and their best lineup in every season since 1996-97.',
                 f'{t["name"]} lineups', sub, '\n'.join(parts))


def page_season(ctx, season):
    names, cur, lineups, duos, records = ctx['names'], ctx['cur'], ctx['lineups'], ctx['duos'], ctx['records']
    L = lineups[lineups['season'] == season]
    D = duos[duos['season'] == season]
    played = float(records[records['season'] == season]['games'].mean())
    tl = T_SEASON_LINEUP if season < cur else scaled(T_SEASON_LINEUP, played, 30)
    td = T_SEASON_DUO if season < cur else scaled(T_SEASON_DUO, played, 80)
    q = L[L['poss'] >= tl]
    sl = season_label(season)
    parts = [block('Best lineups', f'Minimum {tl:,} possessions. {NET_NOTE}', cards(rank(q, 25), lineup_card, names)),
             block('Worst lineups', f'Minimum {tl:,} possessions.', cards(rank(q, 10, asc=True), lineup_card, names)),
             block('Most-used lineups', 'By minutes played together.', cards(by_minutes(L, 10), lineup_card, names)),
             block('Best duos', f'Minimum {td:,} possessions together.',
                   cards(rank(D[D['poss'] >= td], 15), duo_card, names))]
    seasons = [(season_label(s), f'{SITE}/season/{season_label(s)}/', s == season) for s in range(cur, FIRST_SEASON - 1, -1)]
    parts.append(block('Other seasons', None, chips(seasons)))
    parts.append(links_block([('Hottest lineups this week', f'{SITE}/'), ('Best lineups ever', f'{SITE}/all-time/'),
                              ('Best duos ever', f'{SITE}/duos/')]))
    parts.append(f'<p class="note">{CREDIT}</p>')
    stamp = f' <span class="stamp">Games through {ap_date(ctx["end"])}.</span>' if season == cur and ctx['end'] else ''
    sub = f'The best and worst five-man lineups of the {sl} NBA regular season, the most-used units and the top duos.{stamp}'
    return shell(f'/season/{sl}/', f'Best NBA Lineups of {sl}: Net Rating Leaders and Top Duos | HoopsMatic',
                 f'The best and worst NBA lineups of {sl} by net rating, the most-used five-man units and the best duos of the season.',
                 f'Best NBA lineups of {sl}', sub, '\n'.join(parts))


def page_alltime(ctx):
    names, cur, lineups = ctx['names'], ctx['cur'], ctx['lineups']
    q = lineups[lineups['poss'] >= T_ALLTIME_LINEUP]
    car = (lineups.groupby(['slug', 'lineup'], as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max')))
    car = add_ratings(car)
    car['abbr'] = car['slug'].map(lambda s: SLUG_TEAM[s]['abbr'])
    parts = [block('Best single-season lineups', f'Minimum {T_ALLTIME_LINEUP:,} possessions in one season. {NET_NOTE}',
                   cards(rank(q, 50), lineup_card, names)),
             block('Worst single-season lineups', f'Minimum {T_ALLTIME_LINEUP:,} possessions in one season.',
                   cards(rank(q, 25, asc=True), lineup_card, names)),
             block('Most minutes together', 'The same five for the same franchise, every season combined.',
                   cards(by_minutes(car, 50), career_card, names)),
             links_block([('Hottest lineups this week', f'{SITE}/'), ('Best duos ever', f'{SITE}/duos/'),
                          (f'{season_label(cur)} leaders', f'{SITE}/season/{season_label(cur)}/')]),
             f'<p class="note">{CREDIT}</p>']
    return shell('/all-time/', 'Best NBA Lineups Ever: Top Five-Man Units by Net Rating Since 1996-97 | HoopsMatic',
                 'The best and worst NBA five-man lineups of every season since 1996-97 by net rating, and the lineups that played the most minutes together.',
                 'Best NBA lineups ever', 'The best and worst five-man units of any single season since 1996-97, '
                 'and the fives that stayed together longest.', '\n'.join(parts))


def page_duos(ctx):
    names, cur, duos = ctx['names'], ctx['cur'], ctx['duos']
    q = duos[duos['poss'] >= T_ALLTIME_DUO]
    car = (duos.groupby(['slug', 'p1', 'p2'], as_index=False)
             .agg(poss=('poss', 'sum'), pts=('pts', 'sum'), opp_poss=('opp_poss', 'sum'),
                  opp_pts=('opp_pts', 'sum'), secs=('secs', 'sum'), first=('season', 'min'), last=('season', 'max')))
    car = add_ratings(car)
    by = car.sort_values(['secs', 'p1'], ascending=[False, True]).head(25)
    items = []
    for i, (_, r) in enumerate(by.iterrows()):
        t = SLUG_TEAM[r['slug']]
        nm = [names.get(r['p1'], r['p1']), names.get(r['p2'], r['p2'])]
        seasons = (season_label(r['first']) if r['first'] == r['last']
                   else f'{season_label(r["first"])} to {season_label(r["last"])}')
        items.append('<li class="lu"><div class="lu-top">'
                     f'<span class="rk">{i + 1}</span><a class="tm" href="{SITE}/{t["slug"]}/">{logo(t["abbr"])}'
                     f'<span>{esc(t["name"])}, {seasons}</span></a>' + net_html(r['net']) + '</div>'
                     '<div class="faces">' + face(r['p1'], nm[0]) + face(r['p2'], nm[1]) + '</div>'
                     f'<p class="nm">{esc(" and ".join(nm))}</p>'
                     f'<p class="st">{stat_line(r, games=False)}</p></li>')
    played = float(ctx['records'][ctx['records']['season'] == cur]['games'].mean())
    td = scaled(T_SEASON_DUO, played, 80)
    C = duos[(duos['season'] == cur) & (duos['poss'] >= td)]
    parts = [block(f'Best duos of {season_label(cur)}', f'Minimum {td:,} possessions together. {NET_NOTE}',
                   cards(rank(C, 15), duo_card, names)),
             block('Best single-season duos ever', f'Minimum {T_ALLTIME_DUO:,} possessions together in one season.',
                   cards(rank(q, 50), duo_card, names)),
             block('Most minutes together', 'The same two for the same franchise, every season combined.',
                   '<ol class="cards">' + ''.join(items) + '</ol>'),
             links_block([('Hottest lineups this week', f'{SITE}/'), ('Best lineups ever', f'{SITE}/all-time/')]),
             f'<p class="note">{CREDIT}</p>']
    return shell('/duos/', 'Best NBA Duos by Net Rating Since 1996-97 | HoopsMatic',
                 'The best NBA duos by net rating: this season’s leaders, the best two-man pairings of any season since 1996-97 and the duos that played the most minutes together.',
                 'Best NBA duos', 'Two players counted whenever both were on the floor, whoever the other three were.',
                 '\n'.join(parts))


def page_404():
    return shell('/404.html', 'Page not found | HoopsMatic', 'This lineup page does not exist.', 'Page not found',
                 f'That page does not exist. Try the <a href="{SITE}/">hottest lineups this week</a> or '
                 f'<a href="{SITE}/all-time/">the best lineups ever</a>.', '', noindex=True)


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


def build(today=None):
    global FONT_V
    FONT_V = font_v()
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    lineups, records, cur_games, cur, names = load_seasons()
    duos = duos_of(lineups)
    end = cur_games['game_date'].max() if cur_games is not None and len(cur_games) else None
    ctx = dict(names=names, cur=cur, lineups=lineups, duos=duos, records=records,
               hot=hottest(cur_games, cur), end=end)
    pages = {}
    write('/', page_index(ctx), pages)
    write('/all-time/', page_alltime(ctx), pages)
    write('/duos/', page_duos(ctx), pages)
    for s in sorted(lineups['season'].unique()):
        write(f'/season/{season_label(s)}/', page_season(ctx, s), pages)
    for slug in sorted(SLUG_TEAM):
        write(f'/{slug}/', page_team(ctx, slug), pages)
    for (s, code) in sorted(set(zip(lineups['season'], lineups['team']))):
        write(f'/{franchise(code)["slug"]}/{season_label(s)}/', page_team_season(ctx, code, s), pages)
    write('/404.html', page_404(), None)
    with open(os.path.join(OUT, '.nojekyll'), 'w') as f:
        f.write('')
    sitemap(pages, today)
    print(f'{len(pages)} pages, games through {end}')
    return ctx, pages


if __name__ == '__main__':
    build()
    sys.exit(0)
