-- Atlas R2D.1: counts of observed, browser-hydrated route views vs outbound activations.
-- PostgreSQL, read-only. Excludes known rollout_smoke events; no unique visitors,
-- no verified partner arrivals, bookings or commissions. UTC calendar days.
-- A percentage >100 can occur due to best-effort delivery, refreshes and double clicks.
WITH views AS (
  SELECT (created_at AT TIME ZONE 'UTC')::date AS day_utc,
         route_slug, count(*) AS page_views
  FROM revenue_page_views
  WHERE created_at >= now() - interval '30 days'
    AND utm_campaign IS DISTINCT FROM 'rollout_smoke'
  GROUP BY 1,2
), clicks AS (
  SELECT (created_at AT TIME ZONE 'UTC')::date AS day_utc,
         route_slug, count(*) AS outbound_clicks
  FROM revenue_clicks
  WHERE created_at >= now() - interval '30 days'
    AND utm_campaign IS DISTINCT FROM 'rollout_smoke'
    AND placement='route_landing' AND provider='kiwitaxi'
  GROUP BY 1,2
)
SELECT COALESCE(v.day_utc,c.day_utc) AS day_utc,
       COALESCE(v.route_slug,c.route_slug) AS route_slug,
       COALESCE(v.page_views,0) AS observed_page_views,
       COALESCE(c.outbound_clicks,0) AS observed_outbound_clicks,
       CASE WHEN COALESCE(v.page_views,0)>0
         THEN round(100.0 * COALESCE(c.outbound_clicks,0)/v.page_views,2)
         ELSE NULL END AS event_ctr_percent
FROM views v FULL OUTER JOIN clicks c
  ON c.day_utc=v.day_utc AND c.route_slug=v.route_slug
ORDER BY day_utc DESC, route_slug;
