"""Static checks for the public site in site/."""
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = ["index.html", "404.html"]
ASSETS = [
    "AllInOneSnapchatDownloader.exe",
    "AllInOneSnapchatDownloader-macos-arm64.zip",
    "AllInOneSnapchatDownloader-macos-intel.zip",
    "AllInOneSnapchatDownloader-linux.tar.gz",
]
ANCHORS = ["download", "features", "guide", "chat-media", "faq", "support"]


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs, self.imgs, self.ids, self.hrefs = [], [], set(), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "img":
            self.imgs.append(a)
        is_asset_link = tag == "link" and a.get("rel") in ("stylesheet", "preload", "icon")
        if (tag in ("script", "img") or is_asset_link) and (a.get("src") or a.get("href")):
            self.refs.append((tag, a.get("src") or a.get("href")))
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])


def parse(name):
    c = Collect()
    c.feed((SITE / name).read_text(encoding="utf-8"))
    return c


@pytest.mark.parametrize("page", PAGES)
def test_local_references_exist(page):
    for _tag, ref in parse(page).refs:
        u = urlparse(ref)
        if u.scheme:
            continue
        target = SITE / u.path.lstrip("/") if u.path.startswith("/") else SITE / u.path
        assert target.exists(), f"{page} references missing {ref}"


@pytest.mark.parametrize("page", PAGES)
def test_no_third_party_loads(page):
    for tag, ref in parse(page).refs:
        assert not urlparse(ref).scheme, f"{page} loads external {tag}: {ref}"


def test_images_have_alt_and_size():
    for img in parse("index.html").imgs:
        assert img.get("alt", "").strip(), f"missing alt on {img.get('src')}"
        assert img.get("width") and img.get("height"), f"missing size on {img.get('src')}"


def test_all_sections_present_and_linked():
    c = parse("index.html")
    for a in ANCHORS:
        assert a in c.ids, f"missing section id {a}"
        assert f"#{a}" in c.hrefs or a == "chat-media", f"nothing links to #{a}"
    for href in c.hrefs:
        if href.startswith("#") and href != "#":
            assert href[1:] in c.ids, f"dead anchor {href}"


def test_asset_names_match_release_workflow():
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    html = (SITE / "index.html").read_text(encoding="utf-8")
    js = (SITE / "js/download.js").read_text(encoding="utf-8")
    for name in ASSETS:
        assert name in workflow, f"{name} not produced by build.yml"
        assert name in html, f"{name} missing from index.html"
    assert "AllInOneSnapchatDownloader-linux.tar.gz" in js
    assert "AllInOneSnapchatDownloader-macos-arm64.zip" in html


def test_download_links_use_stable_latest_url():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    links = re.findall(r'href="(https://github\.com/[^"]*/releases/latest/download/[^"]+)"', html)
    assert len(links) >= len(ASSETS)
    assert not re.search(r"releases/download/[vV]\d", html), "version-pinned download link"


def test_no_tracking_or_inline_styles():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and "style=" not in html  # keeps the CSP strict
    for bad in ("google-analytics", "gtag", "googletagmanager", "fonts.googleapis"):
        assert bad not in html


def test_disclaimer_and_delivery_files():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert "not affiliated with, endorsed by, or connected to Snap Inc." in html
    for f in ("_headers", "robots.txt", "sitemap.xml", "404.html", "og.png", "README.md"):
        assert (SITE / f).exists(), f"site/{f} missing"
