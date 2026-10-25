"""
FILE: tourgen/ingest/listing_url.py
ROLE: Scrape a property listing page: images (largest srcset variant), title, description,
      room count, surface area.

HOW IT WORKS:
  1. Checks robots.txt for our user agent; refuses disallowed URLs.
  2. Fetches HTML with httpx and parses with selectolax (og:*, <img>, srcset, JSON-LD).
  3. If fewer than MIN_IMAGES are found and Playwright is installed, renders the
     page in a headless browser and re-parses (JS-heavy sites).
  4. download_images() fetches each image into inputs/.

CONNECTS TO:
  - stages/ingest.py

INVARIANTS / GOTCHAS:
  - The user is responsible for having rights to the material; the CLI prints a
    one-line reminder (RIGHTS_REMINDER).
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from tourgen.models.assets import ListingInfo

log = logging.getLogger("tourgen.listing")

USER_AGENT = "tourgen/0.1 (+property tour generator)"
MIN_IMAGES = 5
RIGHTS_REMINDER = "Reminder: make sure you have the rights to use this listing's photos and videos."
_SKIP_IMG = re.compile(r"(logo|icon|sprite|avatar|placeholder|\.svg)", re.IGNORECASE)


class RobotsDisallowed(RuntimeError):
    pass


def robots_allows(url: str, client: httpx.Client) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = client.get(robots_url)
    except httpx.HTTPError:
        return True
    if response.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def largest_srcset(srcset: str) -> str | None:
    best: tuple[float, str] | None = None
    for candidate in srcset.split(","):
        parts = candidate.strip().split()
        if not parts:
            continue
        size = 1.0
        if len(parts) > 1 and parts[1][-1] in "wx":
            try:
                size = float(parts[1][:-1])
            except ValueError:
                size = 1.0
        if best is None or size > best[0]:
            best = (size, parts[0])
    return best[1] if best else None


def _jsonld_objects(tree: HTMLParser) -> Iterator[dict[str, Any]]:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text() or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                yield item
                stack += [v for v in item.values() if isinstance(v, (dict, list))]
            elif isinstance(item, list):
                stack += item


def parse_listing_html(html: str, url: str) -> ListingInfo:
    tree = HTMLParser(html)

    def meta(prop: str) -> str | None:
        node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(f'meta[name="{prop}"]')
        return node.attributes.get("content") if node else None

    title_node = tree.css_first("title")
    title = meta("og:title") or (title_node.text().strip() if title_node else None)
    description = meta("og:description") or meta("description")
    images: list[str] = []
    og_image = meta("og:image")
    if og_image:
        images.append(urljoin(url, og_image))
    for img in tree.css("img, source"):
        attrs = img.attributes
        candidate = None
        for key in ("srcset", "data-srcset"):
            if attrs.get(key):
                candidate = largest_srcset(attrs[key] or "")
                break
        candidate = candidate or attrs.get("data-src") or attrs.get("src")
        if candidate and not candidate.startswith("data:") and not _SKIP_IMG.search(candidate):
            images.append(urljoin(url, candidate))

    rooms: int | None = None
    surface: float | None = None
    for obj in _jsonld_objects(tree):
        if rooms is None and "numberOfRooms" in obj:
            value: Any = obj["numberOfRooms"]
            if isinstance(value, dict):
                value = value.get("value")
            with contextlib.suppress(TypeError, ValueError):
                rooms = int(float(value))
        if surface is None and isinstance(obj.get("floorSize"), dict):
            with contextlib.suppress(KeyError, TypeError, ValueError):
                surface = float(obj["floorSize"]["value"])
        if isinstance(obj.get("image"), list):
            images += [urljoin(url, i) for i in obj["image"] if isinstance(i, str)]

    unique = list(dict.fromkeys(images))
    return ListingInfo(
        url=url, title=title, description=description, rooms=rooms, surface_m2=surface,
        image_urls=unique,
    )  # fmt: skip


def _render_with_playwright(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="networkidle")
        html: str = page.content()
        browser.close()
    return html


def fetch_listing(url: str, client: httpx.Client | None = None) -> ListingInfo:
    own = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30
    )
    try:
        if not robots_allows(url, client):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")
        response = client.get(url)
        response.raise_for_status()
        info = parse_listing_html(response.text, str(response.url))
        if len(info.image_urls) < MIN_IMAGES:
            html = _render_with_playwright(url)
            if html:
                info = parse_listing_html(html, url)
        return info
    finally:
        if own:
            client.close()


def download_images(
    urls: list[str], out_dir: Path, client: httpx.Client | None = None
) -> list[tuple[str, Path]]:
    own = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60
    )
    results: list[tuple[str, Path]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for index, image_url in enumerate(urls):
            url_path = Path(urlparse(image_url).path)
            suffix = url_path.suffix.lower() or ".jpg"
            # Keep the site's filename: it often names the room and is shown to Claude.
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_", url_path.stem)[:60] or "image"
            dest = out_dir / f"listing_{index:03d}_{stem}{suffix}"
            if not dest.exists():
                try:
                    response = client.get(image_url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    log.warning("skip image %s: %s", image_url, exc)
                    continue
                dest.write_bytes(response.content)
            results.append((image_url, dest))
    finally:
        if own:
            client.close()
    return results
