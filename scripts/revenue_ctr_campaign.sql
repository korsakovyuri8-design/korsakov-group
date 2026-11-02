-- Atlas R2D.1: last-30-day, observed event CTR by route + reviewed UTM labels.
-- No user identity or session join; this is NOT unique-person conversion.
-- Excludes known rollout smoke campaign from BOTH numerator and denominator.
WITH page_views AS (
  SELECT route_slug, utm_source, utm_campaign, count(*) AS views
  FROM revenue_page_views
  WHERE created_at >= now() - interval '30 days'
    AND utm_campaign IS DISTINCT FROM 'rollout_smoke'
  GROUP BY route_slug, utm_source, utm_campaign
), outbound AS (
  SELECT route_slug, utm_source, utm_campaign, count(*) AS clicks
  FROM revenue_clicks
  WHERE created_at >= now() - interval '30 days'
    AND utm_campaign IS DISTINCT FROM 'rollout_smoke'
    AND placement = 'route_landing' AND provider = 'kiwitaxi'
  GROUP BY route_slug, utm_source, utm_campaign
)
SELECT COALESCE(v.route_slug,o.route_slug) AS route,
       COALESCE(v.utm_source,o.utm_source,'untagged') AS source,
       COALESCE(v.utm_campaign,o.utm_campaign,'untagged') AS campaign,
       COALESCE(v.views,0) AS observed_views,
       COALESCE(o.clicks,0) AS observed_outbound_clicks,
       CASE WHEN COALESCE(v.views,0)>0
         THEN round(100.0*COALESCE(o.clicks,0)/v.views,2)
         ELSE NULL END AS event_ctr_pct
FROM page_views v FULL OUTER JOIN outbound o
  ON v.route_slug=o.route_slug
 AND v.utm_source IS NOT DISTINCT FROM o.utm_source
 AND v.utm_campaign IS NOT DISTINCT FROM o.utm_campaign
ORDER BY observed_views DESC, route, source, campaign;
