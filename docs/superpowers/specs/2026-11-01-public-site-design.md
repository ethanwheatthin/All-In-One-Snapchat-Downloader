# Public download site: design

## Intent

A public website where people get the app and understand how to use it, without reading a long README. Today everything lives in `README.md` and the GitHub releases page. Many visitors are non-technical people trying to save their own Snapchat memories. They arrive nervous: the data export is confusing, and Windows warns about the unsigned `.exe`. The site should make them feel they are in the right place and can finish the job.

Success: a first-time visitor can find the right download for their computer in seconds, knows what to do before and after running it, and is not scared off by the Windows warning.

## Goals

- One clear download action, with the correct file chosen for the visitor's OS.
- Every substantive part of the README presented in a friendlier form: features, getting the data, the 4-step wizard (with screenshots), chat media, FFmpeg/VLC, macOS/Linux notes, FAQ, support, disclaimer.
- Always points at the newest release without anyone editing the site.
- Professional, calm look. Reads as a small, well-made, trustworthy tool.
- Fast, accessible, works on phones (people will visit from a phone, then download on a computer).
- Privacy-consistent: the app promises nothing leaves the user's machine, so the site tracks nothing.

## Non-goals

- No backend, accounts, forms or comments.
- No analytics, cookies or third-party embeds in v1.
- No docs system or blog. The README stays the developer reference.
- No mobile app download (the app is desktop only; the site says so plainly).

## Anti-goals (a failure even if it works)

- Looks template- or AI-generated: purple gradients, emoji as icons, centered everything, identical rounded cards, dark "hacker" styling, stock illustrations.
- Looks like Snapchat or implies affiliation: no ghost logo, no Snapchat yellow. The disclaimer is visible in the footer.
- Scary or legalistic tone, or hiding the Windows warning rather than explaining it.

## Chosen direction: "Guide first"

Picked by Ethan from three mockups (the others were a product-page hero and a folder-result hero).

- The top of the page is the path: **1 Request your data** (24-48 hours) → **2 Save every ZIP in one folder** → **3 Run the app**. The download button lives in step 3.
- A short note under it explains the Windows "protected your PC" screen (More info → Run anyway; unsigned open-source app; can build from source).
- White background, deep forest green accent (about `#1F5E4B`) with a pale green tint for the active step. Source Sans 3 for text, left-aligned, restrained use of borders instead of shadows and heavy cards.
- Light and dark themes.

## Approaches considered

| Approach | Trade-off | Result |
|---|---|---|
| Plain static HTML/CSS/JS in `/site` | No build; README copy is maintained by hand and can drift | **Chosen** |
| Astro/static generator from Markdown | Nicer content authoring; adds Node tooling to a Python repo | Rejected |
| SPA (React/Angular) | Heavy, slower, worse for search | Rejected |

## Constraints and decisions

- **Hosting:** Cloudflare Pages, connected to this repo. Build command empty, output directory `site`. Custom domain is added in the Cloudflare dashboard by Ethan; the domain name is not yet chosen.
- **Location:** everything under `site/`. The README and `images/` stay as they are; site copies the screenshots it needs into `site/images/`.
- **Download links:** `https://github.com/ethanwheatthin/All-In-One-Snapchat-Downloader/releases/latest/download/<asset>`. Asset names are fixed by the release workflow: `AllInOneSnapchatDownloader.exe`, `AllInOneSnapchatDownloader-macos-arm64.zip`, `AllInOneSnapchatDownloader-macos-intel.zip`, `AllInOneSnapchatDownloader-linux.tar.gz`.
- **OS detection:** small script picks the primary button label and link. Mac visitors get both Apple Silicon and Intel with a one-line "check your chip under Apple menu → About This Mac". All platforms are always listed below the button, so a wrong guess costs nothing.
- **Version label:** fetched from the GitHub releases API on load. If it fails (rate limit, offline), the label is simply hidden. Downloads never depend on it.
- **Pages:** one long page with anchor navigation (`#download`, `#features`, `#guide`, `#chat-media`, `#faq`, `#support`) plus a 404 page. A sticky top bar links to the sections.
- **Content to include:** hero and 3-step path, Windows note, downloads for all platforms (with macOS/Linux notes and alpha label), features, the wizard walkthrough with the existing screenshots, chat media, FFmpeg and VLC, FAQ (collapsible), support (Venmo, Cash App, Buy Me a Coffee with the existing QR codes), links to GitHub, issues and the YouTube tutorial, disclaimer and license line.
- **Copy:** rewritten for non-technical readers in plain, direct sentences. Technical detail (dependencies, building from source, tests) stays in the README, linked from the footer.
- **No third-party requests on load** except the optional GitHub API version call. Fonts are self-hosted (WOFF2 in `site/fonts/`). The YouTube tutorial is a plain link, not an embed.
- **Accessibility:** semantic landmarks, visible focus, 4.5:1 contrast in both themes, alt text on screenshots, works without JavaScript (download links default to the Windows file and the full platform list is plain HTML), respects reduced motion.
- **Responsive:** works at 400px width, no horizontal scroll, steps stack to one column.
- **SEO and sharing:** page title and description, Open Graph and Twitter card with a simple generated image, `robots.txt`, `sitemap.xml`, canonical URL. These use a placeholder domain until it is chosen.
- **Cloudflare config:** a `site/_headers` file with cache rules for static assets and basic security headers (including a Content-Security-Policy that allows `api.github.com` for connect).
- **Branding:** text wordmark only. There is no logo in the repo, and none is invented for v1.

## Left to the builder

- Exact spacing, type scale and component details within the direction above.
- Screenshot sizing and compression (keep each under roughly 200 KB where possible, `loading="lazy"` below the fold).
- Wording of headings and microcopy, kept consistent with the README facts.
- The generated social image.
- A short `site/README.md` explaining how to deploy on Cloudflare Pages and set the domain.

## Open items (not blocking the build)

- Domain name: placeholders used until chosen.
- Whether to add a Windows code-signing story later; the site copy would then change.
