#!/usr/bin/env python3
"""Read-only raw HTTP/SSR acceptance. No browser hydration or click writes.

Usage: python3 scripts/smoke-revenue.py http://127.0.0.1:18092 --affiliate
Use --affiliate only for a build with an approved Kiwitaxi URL configured.
"""
import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.links, self.anchors, self.jsonld = [], [], [], []
        self.h1, self.title, self.text = "", "", ""
        self.in_h1 = self.in_title = self.in_ld = False
        self.ld = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta": self.meta.append(attrs)
        if tag == "link": self.links.append(attrs)
        if tag == "a": self.anchors.append(attrs)
        if tag == "h1": self.in_h1 = True
        if tag == "title": self.in_title = True
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.in_ld, self.ld = True, ""

    def handle_endtag(self, tag):
        if tag == "h1": self.in_h1 = False
        if tag == "title": self.in_title = False
        if tag == "script" and self.in_ld:
            self.jsonld.append(json.loads(self.ld))
            self.in_ld = False

    def handle_data(self, data):
        self.text += data
        if self.in_h1: self.h1 += data
        if self.in_title: self.title += data
        if self.in_ld: self.ld += data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--affiliate", action="store_true")
    args = parser.parse_args()
    catalog = json.loads((Path(__file__).resolve().parents[1] / "src/lib/revenue/transferRoutes.json").read_text())
    origin = "https://atlas.korsakovgroup.com"
    opener = build_opener(NoRedirect())

    def get(path, method="GET"):
        try:
            with opener.open(Request(args.base_url.rstrip("/") + path, method=method), timeout=20) as response:
                return response.status, response.headers, response.read().decode()
        except HTTPError as response:
            return response.code, response.headers, response.read().decode()

    assert get("/")[0] == 200, "homepage"
    for route in catalog:
        path = "/transfer/" + route["slug"]
        status, headers, html = get(path + "?utm_source=instagram")
        assert status == 200 and "text/html" in headers["content-type"], path
        doc = Document()
        doc.feed(html)
        assert doc.h1 == f'{route["origin"]}→{route["destination"]}', path + " h1"
        assert doc.title == route["title"], path + " title"
        assert [link["href"] for link in doc.links if link.get("rel") == "canonical"] == [origin + path], path + " canonical"
        for key, name, value in [("name", "description", route["description"]), ("property", "og:title", route["title"]), ("property", "og:url", origin + path), ("name", "twitter:title", route["title"]), ("name", "referrer", "no-referrer")]:
            assert any(meta.get(key) == name and meta.get("content") == value for meta in doc.meta), path + " " + name
        assert doc.jsonld == [{"@context": "https://schema.org", "@type": "WebPage", "name": route["title"], "description": route["description"], "url": origin + path, "inLanguage": "en", "isPartOf": {"@type": "WebSite", "name": "Korsakov Atlas", "url": origin + "/"}}], path + " JSON-LD"
        for section in route["sections"]:
            assert section["body"] in doc.text, path + " useful copy"
        for slug in route["related"]:
            assert any(a.get("href") == "/transfer/" + slug for a in doc.anchors), path + " related"
        affiliate = [a for a in doc.anchors if "sponsored" in a.get("rel", "")]
        if args.affiliate:
            assert len(affiliate) == 1, path + " affiliate"
            assert affiliate[0]["href"].startswith("https://kiwitaxi.com/"), path + " partner"
            assert affiliate[0]["target"] == "_blank", path + " target"
            assert set(affiliate[0]["rel"].split()) == {"noopener", "noreferrer", "sponsored"}, path + " rel"
            assert "Atlas may earn a commission" in doc.text and "check whether it serves your route" in doc.text, path + " disclosure"
        else:
            assert not affiliate, path + " unconfigured CTA"
        print("SSR_OK=" + path)
    status, headers, html = get("/transfer/not-a-catalog-route")
    assert status == 404, "unknown route must be HTTP 404"
    doc = Document()
    doc.feed(html)
    assert "noindex" in headers.get("X-Robots-Tag", "") or any(m.get("name") == "robots" and "noindex" in m.get("content", "") for m in doc.meta), "404 noindex"
    assert not any("sponsored" in a.get("rel", "") for a in doc.anchors), "404 affiliate"
    status, headers, xml = get("/sitemap.xml")
    assert status == 200 and "application/xml" in headers["content-type"], "sitemap status/type"
    locations = [item.text for item in ElementTree.fromstring(xml).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert locations == [origin + "/"] + [origin + "/transfer/" + r["slug"] for r in catalog if r["indexable"]], "sitemap URLs"
    assert get("/sitemap.xml")[2] == xml, "sitemap determinism"
    assert get("/sitemap.xml", "HEAD")[0] == 200, "sitemap HEAD"
    status, headers, robots = get("/robots.txt")
    assert status == 200 and "text/plain" in headers["content-type"], "robots"
    assert "Sitemap: " + origin + "/sitemap.xml" in robots, "robots sitemap"
    print("HOME_404_SITEMAP_ROBOTS=PASS")


if __name__ == "__main__":
    main()
