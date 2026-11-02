# Atlas R2D.2 — Google Search Console integration checklist

Current production R2B already serves the indexable homepage + 9 canonical transfer pages, deterministic `/sitemap.xml` and `/robots.txt`. Public HTTP 200 and raw SSR checks passed. This does **not** mean Google has indexed any pages.

The account owner must verify a Search Console property for `atlas.korsakovgroup.com` (URL-prefix `https://atlas.korsakovgroup.com/` or the broader `korsakovgroup.com` Domain property). Domain properties require adding Google's exact TXT verification record in the account's DNS. **Do not fabricate or hardcode a verification token.** Do not edit DNS without the owner's authorized access.

Once verified: open https://search.google.com/search-console/ → Sitemaps → submit `https://atlas.korsakovgroup.com/sitemap.xml`. Check Sitemaps processing and Pages/Indexing report, inspect top 9 URLs individually as needed, then monitor actual impressions, organic clicks, average position, index coverage. Sitemap submission is a discovery hint, not a promise of indexing/ranking.

R2D.1 event CTR (browser pageviews/outbound clicks) and Search Console CTR (Google organic clicks/impressions) are DIFFERENT metrics. Don't combine their denominators. Consider campaigns for Tashkent/Montenegro only after route factual accuracy and localized legal/commercial claims are reviewed.

A Windsor.ai Google Search Console integration (optional) can later read GSC/GA4 data in ChatGPT after the owner connects Google credentials. It cannot itself establish domain ownership or make up historical data. Pending Google account access, this checklist is the maximum truthful preparation.
