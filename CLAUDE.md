# lineup-lab

Static site for https://hoopsmatic.com/lineups/, built from Ramiro Bentes' public lineup data. See README.md for the pages, data files and nightly workflow.

## Rules

- Every page's canonical, og:url and sitemap entry is on `https://hoopsmatic.com/lineups/...`, never github.io.
- A sitemap `<lastmod>` moves only when that page's content changes (`data/page_hashes.json`). Never stamp every page with today.
- No keys, tokens or secrets. If something seems to need one, find another route first.
- Every page ends with the contact footer (hoopsmatic@hoopsmatic.com / ads@hoopsmatic.com) and credits Ramiro Bentes.
- No small kicker or label lines above or beside titles.
- Mobile first: nothing may scroll sideways at any width from 320px.
- Nothing new loads in the visitor's browser beyond hoopsmatic.com and jsierrahoopshype.github.io (fonts are self-hosted). Adding a third party means https://hoopsmatic.com/privacy needs updating.
- Run `python -m unittest discover -s tests -v` before pushing.
