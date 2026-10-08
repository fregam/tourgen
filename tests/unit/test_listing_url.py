"""ingest/listing_url.py: HTML parsing, srcset, robots.txt, downloads (no network)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tourgen.ingest.listing_url import (
    RobotsDisallowed,
    download_images,
    fetch_listing,
    largest_srcset,
    parse_listing_html,
)

HTML = """
<html><head>
<title>Fallback</title>
<meta property="og:title" content="Sunny 3-bed house">
<meta name="description" content="Lovely home">
<meta property="og:image" content="/hero.jpg">
<script type="application/ld+json">
{"@type": "SingleFamilyResidence", "numberOfRooms": {"value": "5"},
 "floorSize": {"value": 120, "unitCode": "MTK"}, "image": ["https://cdn.x/1.jpg"]}
</script>
</head><body>
<img src="/logo.svg"><img srcset="/a-small.jpg 400w, /a-big.jpg 1600w" src="/a-small.jpg">
<img data-src="/b.jpg"><picture><source srcset="/c.webp 1x, /c@2x.webp 2x"></picture>
</body></html>
"""


def test_largest_srcset() -> None:
    assert largest_srcset("a.jpg 400w, b.jpg 1600w, c.jpg 800w") == "b.jpg"
    assert largest_srcset("a.jpg 1x, b.jpg 2x") == "b.jpg"
    assert largest_srcset("") is None


def test_parse_listing_html() -> None:
    info = parse_listing_html(HTML, "https://site.example/listing/1")
    assert info.title == "Sunny 3-bed house"
    assert info.description == "Lovely home"
    assert info.rooms == 5 and info.surface_m2 == 120
    assert info.image_urls[0] == "https://site.example/hero.jpg"
    assert "https://site.example/a-big.jpg" in info.image_urls
    assert "https://site.example/c@2x.webp" in info.image_urls
    assert "https://cdn.x/1.jpg" in info.image_urls
    assert not any("logo" in u for u in info.image_urls)


def _client(robots: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, content=b"jpegbytes")
        return httpx.Response(200, text=HTML)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_listing_respects_robots() -> None:
    with pytest.raises(RobotsDisallowed):
        fetch_listing("https://site.example/listing/1", _client("User-agent: *\nDisallow: /"))
    info = fetch_listing("https://site.example/listing/1", _client("User-agent: *\nAllow: /"))
    assert info.title == "Sunny 3-bed house"


def test_download_images(tmp_path: Path) -> None:
    got = download_images(["https://site.example/a.jpg"], tmp_path, _client(""))
    assert got[0][1].read_bytes() == b"jpegbytes"
