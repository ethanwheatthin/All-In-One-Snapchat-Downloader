# Public site

Static site for the app. No build step.

## Deploy on Cloudflare Pages

1. Cloudflare dashboard, Workers & Pages, Create, Pages, Connect to Git, pick this repo.
2. Production branch: `main`. Framework preset: None. Build command: leave empty. Build output directory: `site`.
3. After the first deploy, open the project, Custom domains, Set up a custom domain, and follow the DNS prompts.
4. Replace the placeholder domain `snapchat-downloader.example` with the real one in `index.html` (canonical and Open Graph tags), `robots.txt` and `sitemap.xml`:
   `grep -rl snapchat-downloader.example site | xargs sed -i 's/snapchat-downloader.example/YOUR-DOMAIN/g'`

## How downloads stay current

Buttons link to `releases/latest/download/<asset>` on GitHub. The asset names are set in `.github/workflows/build.yml`. If you rename a release asset, update `index.html` and `js/download.js`. `tests/test_site.py` checks that they match.

## Local preview and checks

    python -m http.server 8080 --directory site
    python -m pytest tests/test_site.py -q

Fonts (Source Sans 3) are self-hosted in `fonts/`. The page makes no third-party requests except one optional call to `api.github.com` for the version number.
