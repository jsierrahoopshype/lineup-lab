"""Lineup Lab tests. Run from the repo root: python -m unittest discover -s tests -v

Unit tests need only the repo's data/. The site tests check the built docs/ and
are skipped when docs/ has not been built yet."""
import glob
import json
import os
import re
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'build'))
import lineup_lab_build as B  # noqa: E402

DOCS = os.path.join(ROOT, 'docs')


class Units(unittest.TestCase):
    def test_net_rating(self):
        d = B.add_ratings(pd.DataFrame({'poss': [200.0], 'pts': [240.0], 'opp_poss': [200.0], 'opp_pts': [210.0]}))
        self.assertAlmostEqual(d['ortg'][0], 120.0)
        self.assertAlmostEqual(d['drtg'][0], 105.0)
        self.assertAlmostEqual(d['net'][0], 15.0)

    def test_zero_possessions_is_not_a_number(self):
        d = B.add_ratings(pd.DataFrame({'poss': [0.0], 'pts': [0.0], 'opp_poss': [0.0], 'opp_pts': [0.0]}))
        self.assertTrue(pd.isna(d['net'][0]))

    def test_duos_sum_every_five_they_share(self):
        L = pd.DataFrame({
            'season': [2020, 2020], 'team': ['BOS', 'BOS'],
            'lineup': ['1-2-3-4-5', '1-2-6-7-8'],
            'poss': [100.0, 50.0], 'pts': [110.0, 40.0], 'opp_poss': [100.0, 50.0],
            'opp_pts': [100.0, 60.0], 'secs': [600.0, 300.0]})
        D = B.duos_of(L)
        both = D[(D['p1'] == '1') & (D['p2'] == '2')].iloc[0]
        self.assertEqual(both['poss'], 150.0)
        self.assertEqual(both['pts'], 150.0)
        self.assertEqual(both['secs'], 900.0)
        only = D[(D['p1'] == '3') & (D['p2'] == '4')].iloc[0]
        self.assertEqual(only['poss'], 100.0)
        self.assertEqual(len(D), 19)  # 10 + 10 pairs, (1,2) shared

    def test_duo_ids_order_numerically(self):
        L = pd.DataFrame({'season': [2020], 'team': ['BOS'], 'lineup': ['9-10-11-12-100'],
                          'poss': [1.0], 'pts': [1.0], 'opp_poss': [1.0], 'opp_pts': [1.0], 'secs': [1.0]})
        D = B.duos_of(L)
        row = D[(D['p1'] == '9') & (D['p2'] == '100')]
        self.assertEqual(len(row), 1)

    def test_repair_fills_an_id_from_the_same_name(self):
        col = pd.Series(['1 A B, 2 C D, 3 E F, 4 G H, 5 I J',
                         '1 A B, 2 C D, 3 E F, 4 G H, NA I J',
                         '1 A B, 2 C D, 3 E F, 4 G H, NA Nobody'])
        out = B.repair(col)
        self.assertEqual(out[1], '1 A B, 2 C D, 3 E F, 4 G H, 5 I J')
        self.assertTrue(pd.isna(out[2]))  # dropped by the caller

    def test_key_sorts_ids(self):
        self.assertEqual(B.key_of('30 X, 4 Y, 200 Z, 1 W, 5 V'), '1-4-5-30-200')

    def test_scaled_threshold(self):
        self.assertEqual(B.scaled(150, 82, 20), 150)
        self.assertEqual(B.scaled(150, 41, 20), 75)
        self.assertEqual(B.scaled(150, 1, 20), 20)
        self.assertEqual(B.scaled(150, 90, 20), 150)

    def test_franchise_history(self):
        self.assertEqual(B.franchise('SEA')['slug'], 'thunder')
        self.assertEqual(B.franchise('CHH')['slug'], 'hornets')
        self.assertEqual(B.franchise('NOH')['slug'], 'pelicans')
        self.assertEqual(B.team_name('CHA', 2010), 'Charlotte Bobcats')
        self.assertEqual(B.team_name('CHA', 2015), 'Charlotte Hornets')
        self.assertEqual(B.team_name('WAS', 1997), 'Washington Bullets')
        self.assertEqual(B.season_label(2000), '1999-00')
        self.assertEqual(B.ap_date('2026-09-05'), 'Sept. 5, 2026')

    def test_records_match_history(self):
        names = {}
        d = B.read_raw(2016, names)
        sc = d.groupby(['team', 'game_id'])[['pts', 'opp_pts']].sum()
        gsw = sc.loc['GSW']
        self.assertEqual(int((gsw['pts'] > gsw['opp_pts']).sum()), 73)
        self.assertEqual(len(gsw), 82)

    def test_sitemap_lastmod_moves_only_on_change(self):
        with tempfile.TemporaryDirectory() as t:
            data, out = B.DATA, B.OUT
            B.DATA, B.OUT = t, t
            try:
                B.sitemap({'/': 'h1', '/a/': 'h2'}, '2026-10-01')
                B.sitemap({'/': 'h1', '/a/': 'h3'}, '2026-10-02')
                with open(os.path.join(t, 'page_hashes.json')) as f:
                    st = json.load(f)
                self.assertEqual(st['/']['lastmod'], '2026-10-01')
                self.assertEqual(st['/a/']['lastmod'], '2026-10-02')
                with open(os.path.join(t, 'sitemap.xml')) as f:
                    xml = f.read()
                self.assertIn('<loc>https://hoopsmatic.com/lineups/</loc><lastmod>2026-10-01</lastmod>', xml)
            finally:
                B.DATA, B.OUT = data, out


@unittest.skipUnless(os.path.exists(os.path.join(DOCS, 'index.html')), 'docs/ not built')
class Site(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # headshot-test/ is a temporary noindex page for picking a portrait
        # style; it is not part of the section and not in the sitemap.
        cls.pages = sorted(p for p in glob.glob(os.path.join(DOCS, '**', 'index.html'), recursive=True)
                           if os.sep + 'headshot-test' + os.sep not in p)
        with open(os.path.join(DOCS, 'sitemap.xml')) as f:
            cls.sitemap = f.read()

    def test_enough_pages(self):
        self.assertGreater(len(self.pages), 900)

    def test_every_page_canonical_footer_and_no_github_io_canonical(self):
        for p in self.pages:
            with open(p, encoding='utf-8') as f:
                s = f.read()
            rel = '/' + os.path.relpath(os.path.dirname(p), DOCS).replace(os.sep, '/') + '/'
            rel = '/' if rel == '/./' else rel
            want = 'https://hoopsmatic.com/lineups' + rel
            self.assertIn(f'<link rel="canonical" href="{want}">', s, p)
            self.assertIn(f'<meta property="og:url" content="{want}">', s, p)
            self.assertIn('href="mailto:ads@hoopsmatic.com"', s, p)
            self.assertIn('href="mailto:hoopsmatic@hoopsmatic.com"', s, p)
            self.assertNotIn('github.io/lineup-lab', s, p)
            self.assertIn('name="viewport"', s, p)
            self.assertEqual(s.count('<h1>'), 1, p)
            self.assertIn(f'<loc>{want}</loc>', self.sitemap, p)

    def test_sitemap_has_dates_and_only_pages(self):
        locs = re.findall(r'<url><loc>([^<]+)</loc><lastmod>(\d{4}-\d\d-\d\d)</lastmod></url>', self.sitemap)
        self.assertEqual(len(locs), len(self.pages))
        self.assertNotIn('404', self.sitemap)

    def test_index_has_hottest(self):
        with open(os.path.join(DOCS, 'index.html'), encoding='utf-8') as f:
            s = f.read()
        self.assertIn('id="hottest"', s)
        self.assertIn('Ramiro Bentes', s)

    def test_headshot_test_page_is_noindex_and_not_in_sitemap(self):
        p = os.path.join(DOCS, 'headshot-test', 'index.html')
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                self.assertIn('content="noindex"', f.read())
            self.assertNotIn('headshot-test', self.sitemap)

    def test_404_is_noindex(self):
        with open(os.path.join(DOCS, '404.html'), encoding='utf-8') as f:
            self.assertIn('content="noindex"', f.read())

    def test_known_lineup_on_alltime(self):
        with open(os.path.join(DOCS, 'all-time', 'index.html'), encoding='utf-8') as f:
            s = f.read()
        self.assertIn('2007-08 Boston Celtics', s)
        self.assertIn('1996-97 Utah Jazz', s)


if __name__ == '__main__':
    unittest.main()
