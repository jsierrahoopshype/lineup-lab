"""Player heights, so each lineup can be shown shortest to tallest (roughly
point guard to center).

Source: Wikidata (https://www.wikidata.org), whose facts are public domain
(CC0): every person with an NBA.com player ID (property P3647) and a height
(P2048). One public query, no account, key or token.

Writes data/heights.json: NBA player id -> height in centimetres. On any
failure, or if the answer looks too small, the previous file is kept and the
run goes on (the pages then keep their previous order for unknown players).
"""
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'heights.json')
ENDPOINT = 'https://query.wikidata.org/sparql'
# Wikidata asks every script to identify itself with a contact.
UA = 'hoopsmatic-lineup-lab/1.0 (https://hoopsmatic.com/lineups/; hoopsmatic@hoopsmatic.com)'
QUERY = """
SELECT ?id ?amount ?unit WHERE {
  ?p wdt:P3647 ?id ;
     p:P2048/psv:P2048 [ wikibase:quantityAmount ?amount ; wikibase:quantityUnit ?unit ] .
}
"""
# Wikidata unit item -> centimetres per unit
UNITS = {
    'http://www.wikidata.org/entity/Q174728': 1.0,     # centimetre
    'http://www.wikidata.org/entity/Q11573': 100.0,    # metre
    'http://www.wikidata.org/entity/Q218593': 2.54,    # inch
    'http://www.wikidata.org/entity/Q3710': 30.48,     # foot
}


def fetch():
    url = ENDPOINT + '?' + urllib.parse.urlencode({'query': QUERY, 'format': 'json'})
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/sparql-results+json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def parse(data):
    """id -> cm. A player with several heights keeps the middle one."""
    seen = {}
    for b in data['results']['bindings']:
        pid = ''.join(ch for ch in b['id']['value'] if ch.isdigit())
        unit = UNITS.get(b['unit']['value'])
        if not pid or unit is None:
            continue
        cm = float(b['amount']['value']) * unit
        if 150 <= cm <= 240:  # anything else is a typo in the source
            seen.setdefault(pid, []).append(round(cm, 1))
    return {k: sorted(v)[len(v) // 2] for k, v in seen.items()}


def main():
    try:
        h = parse(fetch())
    except Exception as e:  # noqa: BLE001 - keep the old file on any error
        print('heights: kept previous file (%s)' % e)
        return 0
    if len(h) < 1000:
        print('heights: only %d players, kept previous file' % len(h))
        return 0
    with open(OUT, 'w') as f:
        json.dump(h, f, indent=0, sort_keys=True)
        f.write('\n')
    print('heights: %d players' % len(h))
    return 0


if __name__ == '__main__':
    sys.exit(main())
