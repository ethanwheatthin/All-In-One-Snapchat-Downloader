# Public Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship a static, guide-first download site in `site/` for Cloudflare Pages.

**Architecture:** Hand-written HTML/CSS plus one small vanilla JS file. No build step. Downloads use GitHub's stable `releases/latest/download/<asset>` URLs, so the page never goes stale.

**Tech Stack:** HTML, CSS (custom properties, light/dark), vanilla JS, self-hosted Source Sans 3 (WOFF2), pytest for static checks, Playwright (preinstalled) for a visual pass.

**Spec:** `docs/superpowers/specs/2026-11-01-public-site-design.md`

## Global Constraints

- Output directory `site/`, no build command.
- Asset names exactly: `AllInOneSnapchatDownloader.exe`, `AllInOneSnapchatDownloader-macos-arm64.zip`, `AllInOneSnapchatDownloader-macos-intel.zip`, `AllInOneSnapchatDownloader-linux.tar.gz`.
- No third-party requests on load except the optional `api.github.com` version call. No analytics, cookies, embeds. Fonts self-hosted.
- Accent about `#1F5E4B`, Source Sans 3, left-aligned, light and dark themes.
- No Snapchat logo or yellow, no emoji, no gradients. Disclaimer in footer.
- Works at 400px with no horizontal scroll, and without JavaScript.
- Sections and anchors: `#download`, `#features`, `#guide`, `#chat-media`, `#faq`, `#support`.

## Review Focus

- GitHub API fails or is rate limited: version label hidden, downloads still work (Task 3).
- Visitor on phone or tablet: page says desktop only, still shows all downloads (Task 3).
- Mac visitor: both chip builds shown with the "About This Mac" hint (Task 3).
- JavaScript disabled: Windows button and full platform list are plain HTML (Task 2).
- Dark mode: all text keeps contrast (Task 1, visual pass in Task 4).

## Task 1: Foundation (layout, theme, fonts)

**Files:** Create `site/index.html` (head, header, footer shell), `site/css/site.css`, `site/fonts/*.woff2`.

**Interfaces:** Produces CSS tokens `--bg --fg --muted --line --accent --tint` (light, dark media query, `[data-theme]` not needed) and the section wrapper class `.section`.

- [ ] Download Source Sans 3 400/600/700 latin WOFF2 into `site/fonts/` and declare `@font-face` with `font-display: swap`.
- [ ] Write the token system, base typography, sticky nav, 16px+ gutters, focus styles, reduced-motion.
- [ ] Commit.

## Task 2: Content (all sections, no JS needed)

**Files:** Modify `site/index.html`; create `site/images/` (copy the wizard, export and QR screenshots from `images/`, compressed).

**Interfaces:** Produces `<a id="download-primary">` (Windows link by default) and `<div id="download-note">`, `<span id="version">` (hidden by default), `<ul id="all-downloads">`, used by Task 3.

- [ ] Hero + 3-step path with the download button in step 3; Windows warning note.
- [ ] `#download` platform list with macOS chip hint and Linux/alpha note.
- [ ] `#features`, `#guide` (4 wizard steps with screenshots, alt text), `#chat-media`, FFmpeg/VLC, `#faq` as `<details>`, `#support` with QR codes, footer with disclaimer, GitHub, issues, YouTube link, README link.
- [ ] Commit.

## Task 3: Download behavior

**Files:** Create `site/js/download.js`; modify `site/index.html` to include it with `defer`.

**Interfaces:** Consumes the ids from Task 2. Produces `detectOS(ua: string, platform: string, maxTouchPoints: number): "windows"|"mac"|"linux"|"mobile"` (pure function, attached to `window.SiteDownload` for testing).

- [ ] Primary button label and link follow `detectOS`; mac shows both builds plus chip hint; mobile shows "This app runs on a computer" and keeps the list.
- [ ] Fetch `https://api.github.com/repos/ethanwheatthin/All-In-One-Snapchat-Downloader/releases/latest`; on success show the version in `#version`; on any failure leave it hidden.
- [ ] Copy buttons for install commands, with a select-text fallback.
- [ ] Commit.

## Task 4: Delivery files, tests, visual pass

**Files:** Create `site/_headers`, `site/robots.txt`, `site/sitemap.xml`, `site/404.html`, `site/og.png`, `site/README.md`, `tests/test_site.py`.

- [ ] `tests/test_site.py`: asserts every local `href`/`src` in `site/*.html` exists; every `<img>` has non-empty `alt`; all four asset names appear in the page and match `.github/workflows/build.yml` outputs; no `http(s)://` script/link/img sources other than `api.github.com` in JS and `github.com`/`youtube.com` anchors; all six anchors exist. Run `python -m pytest tests/test_site.py -q`, expect PASS.
- [ ] Playwright: screenshot desktop (1280) and phone (400) in light and dark, confirm no horizontal overflow and that `#download-primary` href matches the simulated OS.
- [ ] `site/README.md`: Cloudflare Pages settings (build command empty, output `site`), custom domain steps, placeholder domain to replace.
- [ ] Commit, push, open PR to `main`.
