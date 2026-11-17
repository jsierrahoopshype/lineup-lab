// Global Rating+ and Real Value for every player, as one small file for the
// Trade Machine's "What this trade does" section (hoopsmatic.com/):
// docs/data/ratings-<YYYY>.json, served at /lineups/data/ratings-<YYYY>.json.
//
// Neither number is copied or re-implemented here. Each run fetches Alberto's
// CURRENT page code (aderoa/RealValue: globalratingplus.html and index.html),
// takes the script that defines processData, cuts it just before its display
// code (at "let appData = null;" or "function ratCls(", whichever comes first
// after processData), adds the one-line helpers declared after the cut, and
// runs it on the same data files the pages read (hoopsmatic.com/api/...).
// So a formula change on his pages is in the file at the next run. Read-only:
// GETs only; the pages' snapshot saving and drawing never run.
//
// Refuses to write (exit 1, the previous file stays) when the code can't be
// found or either measure comes back with fewer than MIN_PLAYERS players.
// The file keeps its "updated" time when the numbers have not changed, so
// the workflow commits only real changes.
//
// Run: node build/lineup_lab_ratings.mjs [season]   (season as the source: 2027 = 2026-27)
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SEASON = process.argv[2] || readFileSync(join(ROOT, 'data/current/season.txt'), 'utf8').trim();
const OUT = join(ROOT, 'docs/data/ratings-' + SEASON + '.json');
const PAGES = {
  gr: 'https://raw.githubusercontent.com/aderoa/RealValue/main/globalratingplus.html',
  rv: 'https://raw.githubusercontent.com/aderoa/RealValue/main/index.html',
};
const API = 'https://hoopsmatic.com/api/';
const MIN_PLAYERS = 300;
const HEADERS = { 'User-Agent': 'Mozilla/5.0 (compatible; lineup-lab-ratings/1.0)', Referer: 'https://hoopsmatic.com/' };

async function get(url) {
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(url, { headers: HEADERS, signal: AbortSignal.timeout(30000) });
      if (r.ok) return await r.text();
      if (r.status < 500) throw new Error(url + ' -> HTTP ' + r.status);
    } catch (e) { if (i === 2) throw e; }
    await new Promise((res) => setTimeout(res, 3000 * (i + 1)));
  }
  throw new Error(url + ' failed');
}

// The page's own code up to (not including) its display code, as callable functions.
export function pageCore(html, names) {
  const body = html.split(/<script\b[^>]*>/i).map((p) => p.split(/<\/script>/i)[0]).find((b) => b.includes('function processData('));
  if (!body) throw new Error('no processData');
  const from = body.indexOf('function processData(');
  const cuts = ['\nlet appData = null;', '\nfunction ratCls('].map((m) => body.indexOf(m, from)).filter((i) => i >= 0);
  if (!cuts.length) throw new Error('no cut point');
  const cut = Math.min(...cuts);
  const helpers = (body.slice(cut).match(/^(?:async )?function [A-Za-z_$][\w$]*\([^)]*\)\s*\{.*\}[ \t]*$/gm) || []).join('\n');
  // What his code may touch outside processData's math: the URL (Global
  // Rating+ reads ?phase=, default regular season) and one badge on the page
  // (Real Value's defense status), which isn't there.
  const location = { search: '' };
  const document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  const ret = names.map((n) => JSON.stringify(n) + ':typeof ' + n + '!=="undefined"?' + n + ':null').join(',');
  return new Function('location', 'document', body.slice(0, cut) + '\n' + helpers + '\n;return {' + ret + '};')(location, document);
}

function quiet(f) {
  const l = console.log, w = console.warn;
  console.log = console.warn = () => {};
  try { return f(); } finally { console.log = l; console.warn = w; }
}
async function quietAsync(f) {
  const l = console.log, w = console.warn;
  console.log = console.warn = () => {};
  try { return await f(); } finally { console.log = l; console.warn = w; }
}

export async function compute(fetchText = get) {
  const [grHtml, rvHtml] = await Promise.all([fetchText(PAGES.gr), fetchText(PAGES.rv)]);
  const g = pageCore(grHtml, ['processData', 'parseCSV', 'parseDef365', 'parseRatBase']);
  const v = pageCore(rvHtml, ['processData', 'parseCSV', 'loadInjuryFeed']);
  for (const [k, o] of [['Global Rating+', g], ['Real Value', v]]) if (!o.processData || !o.parseCSV) throw new Error(k + ': processData or parseCSV missing');
  const [tv, def, rb, con, dc] = await Promise.all(['rm-tv', 'defense-data', 'rm-rat-base', 'rm-contracts', 'defense-cache'].map((p) => fetchText(API + p)));
  if (v.loadInjuryFeed) await quietAsync(() => v.loadInjuryFeed());
  const G = quiet(() => g.processData(g.parseCSV(tv), g.parseDef365 ? g.parseDef365(JSON.parse(def)) : {}, g.parseRatBase ? g.parseRatBase(rb).rs : null));
  const R = quiet(() => v.processData(v.parseCSV(tv), v.parseCSV(con), JSON.parse(dc)));
  const gr = {}, rv = {};
  for (const p of G.players || []) if (p && p.player && Number.isFinite(p.globalPlus)) gr[p.player] = Math.round(p.globalPlus * 100) / 100;
  for (const p of R.players || []) if (p && p.player && Number.isFinite(p.uncappedTV)) rv[p.player] = Math.round(p.uncappedTV);
  const hash = (s) => createHash('sha256').update(s).digest('hex').slice(0, 12);
  return { gr, rv, source: { gr: hash(grHtml), rv: hash(rvHtml) } };
}

export function fileFor(result, season, previous, now = new Date()) {
  const sorted = (o) => Object.fromEntries(Object.keys(o).sort().map((k) => [k, o[k]]));
  const body = { season: (Number(season) - 1) + '-' + String(season).slice(2), source: result.source, gr: sorted(result.gr), rv: sorted(result.rv) };
  const same = previous && JSON.stringify({ ...previous, updated: undefined }) === JSON.stringify({ ...body, updated: undefined });
  return { updated: same ? previous.updated : now.toISOString(), ...body };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const r = await compute();
  const n = { gr: Object.keys(r.gr).length, rv: Object.keys(r.rv).length };
  if (n.gr < MIN_PLAYERS || n.rv < MIN_PLAYERS) {
    console.error('Refusing to write: Global Rating+ ' + n.gr + ', Real Value ' + n.rv + ' players (minimum ' + MIN_PLAYERS + ').');
    process.exit(1);
  }
  let prev = null;
  try { prev = existsSync(OUT) ? JSON.parse(readFileSync(OUT, 'utf8')) : null; } catch (e) { prev = null; }
  const file = fileFor(r, SEASON, prev);
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(file) + '\n');
  console.log('ratings-' + SEASON + '.json: Global Rating+ ' + n.gr + ', Real Value ' + n.rv + ' players' + (prev && prev.updated === file.updated ? ' (unchanged)' : ' (updated)'));
}
