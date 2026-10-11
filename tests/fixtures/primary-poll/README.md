# Primary Tweet poll golden

Public GET `https://api.fxtwitter.com/status/2109152716954222849`, observed
2026-10-11T06:40:49.745573Z from the production server network during a read-only
investigation. Original screenshot was published 13:22:48 UTC+8; it displayed
only “Pro users, would you rather have”, omitting the primary poll.

Freeze the clock at 2026-10-11T06:40:49Z. This snapshot has three choices, 60,812
votes and a timezone-qualified deadline 2026-10-11T17:22:47Z. These are observation
time results, not the earlier delivery-time results. No viewer selection exists.
The Tweet has no quote. The provider payload is saved intact; production config,
state, private logs and QQ data are not included.

Vx, feed, conflict, missing-field and simultaneous quoted-poll cases in the tests
are explicit synthetic mutations of this payload / previously verified schemas,
not claims of additional successful live Vx captures.
