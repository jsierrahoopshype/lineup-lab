"""Player portraits for Lineup Lab, self-hosted under docs/faces/<nba_id>.webp.

Every HoopsMatic section uses the same headshot: nba-headshots' face-fill/ folder (chosen Oct 26
2026; the face fills the circle, no neck line, no background), built there by
scripts/build_face_fill.py. This copies those files here, so visitors' browsers load faces from
hoopsmatic.com only.

Each run reads face-fill/.hashes.json (one small public file) and downloads only the portraits
that are new or changed since the last run (data/faces_state.json keeps what each copy was made
from). Players face-fill has no file for (no NBA photo) lose any old copy, and the pages show
their initials. No key or token: plain public file downloads.

Usage:
  python build/lineup_lab_faces.py            (nightly)
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'docs', 'faces')
STATE = os.path.join(ROOT, 'data', 'faces_state.json')
HEADSHOTS = os.path.join(ROOT, 'data', 'headshots.json')
BASE = 'https://raw.githubusercontent.com/jsierrahoopshype/nba-headshots/main/players/headshots/face-fill/'
UA = {'User-Agent': 'hoopsmatic-lineup-lab (+https://hoopsmatic.com/lineups/)'}
MAX_PER_RUN = 2500  # enough for a full refresh; a normal night downloads a handful


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(HEADSHOTS) as f:
        stems = json.load(f)  # nba id -> nba-headshots file stem
    try:
        remote = json.loads(get(BASE + '.hashes.json'))['files']
    except Exception as e:  # noqa: BLE001 - keep every portrait as it is
        print('faces: kept as they are (%s)' % e)
        return 0
    state = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)
    got = removed = failed = 0
    for pid, stem in sorted(stems.items()):
        out = os.path.join(OUT, pid + '.webp')
        r = remote.get(stem)
        if r is None:
            # no face-fill file: initials on the pages
            if os.path.exists(out):
                os.remove(out)
                removed += 1
            state.pop(pid, None)
            continue
        want = {'stem': stem, 'src': r['src'], 'sha': r['sha']}
        if state.get(pid) == want and os.path.exists(out):
            continue
        if got + failed >= MAX_PER_RUN:
            continue
        try:
            data = get(BASE + stem + '.webp')
        except Exception as e:  # noqa: BLE001 - try again next run
            print('faces: could not fetch', stem, e)
            failed += 1
            continue
        with open(out, 'wb') as f:
            f.write(data)
        state[pid] = want
        got += 1
    # portraits of ids no longer in the headshot map
    for fn in os.listdir(OUT):
        if fn.endswith('.webp') and fn[:-5] not in stems:
            os.remove(os.path.join(OUT, fn))
            state.pop(fn[:-5], None)
            removed += 1
    with open(STATE, 'w') as f:
        json.dump(state, f, indent=0, sort_keys=True)
        f.write('\n')
    print(f'faces: {got} downloaded, {removed} removed, {failed} failed, {len(state)} in all')
    return 0


if __name__ == '__main__':
    sys.exit(main())
