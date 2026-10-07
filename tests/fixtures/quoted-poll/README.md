# Public quoted poll fixtures

Captured read-only from the production Host network, 2026-10-07 18:13 UTC+8:

- `fxtwitter.json`: unchanged response from `https://api.fxtwitter.com/status/2107578625419866469`.
- `vxtwitter.json`: unchanged response from `https://api.vxtwitter.com/thsottiaux/status/2107578625419866469`.
- `feed-target.json`: unchanged target object extracted from `https://codex-reset.com/api/feed`.
- `card.json`: Fx quote normalized by the plugin parser, plus faithful human Chinese translations for presentation-only preview/Host acceptance. Counts, percentages, time and author come from Fx; no viewer selection is invented.

Main ID: `2107578625419866469`; quote ID: `2107576143285219799`.
Fx and Vx differ in field names and snapshot age. Feed lacks quote/poll entirely.
Active polls, unavailable polls and feed-with-quote regressions are deliberately derived test variants, not captured source responses.
