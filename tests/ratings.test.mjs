// build/lineup_lab_ratings.mjs: the page code is cut before its display code and
// run; the file keeps its time when nothing changed. Run: node --test tests/
import test from 'node:test';
import assert from 'node:assert/strict';
import { pageCore, fileFor } from '../build/lineup_lab_ratings.mjs';

const PAGE = '<html><script>var x=1;</script><script>\nconst K = 2;\nfunction parseCSV(t) { return t.split(","); }\n'
  + 'function processData(rows) { return { players: rows.map((r) => ({ player: r, globalPlus: K * r.length, uncappedTV: fmt(r) })) }; }\n'
  + 'let appData = null;\ndocument.getElementById("x").textContent = "draws";\nfunction fmt(s) { return s.length * 1e6; }\n</script></html>';

test('pageCore: his code up to the display code, plus one-line helpers after it; nothing after the cut runs', () => {
  const g = pageCore(PAGE, ['processData', 'parseCSV', 'appData']);
  assert.equal(g.appData, null);
  const d = g.processData(g.parseCSV('Ann,Bobby'));
  assert.deepEqual(d.players, [{ player: 'Ann', globalPlus: 6, uncappedTV: 3e6 }, { player: 'Bobby', globalPlus: 10, uncappedTV: 5e6 }]);
});

test('pageCore: no processData -> an error, so nothing is written', () => {
  assert.throws(() => pageCore('<script>function other(){}</script>', ['processData']), /no processData/);
});

test('fileFor: season label, sorted names, "updated" kept when nothing changed and moved when it did', () => {
  const r = { gr: { B: 2, A: 1 }, rv: { A: 5 }, source: { gr: 'g', rv: 'v' } };
  const a = fileFor(r, '2027', null, new Date('2026-11-17T10:00:00Z'));
  assert.equal(a.season, '2026-27');
  assert.deepEqual(Object.keys(a.gr), ['A', 'B']);
  assert.equal(fileFor(r, '2027', a, new Date('2026-11-17T13:00:00Z')).updated, '2026-11-17T10:00:00.000Z');
  assert.equal(fileFor({ ...r, rv: { A: 6 } }, '2027', a, new Date('2026-11-17T13:00:00Z')).updated, '2026-11-17T13:00:00.000Z');
});
