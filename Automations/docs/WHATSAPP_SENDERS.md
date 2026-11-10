# WhatsApp senders (9 Nov 2026)

The gateway keeps two WhatsApp numbers linked at once and sends each alert from the number chosen
for its lane. Page: <http://127.0.0.1:3005> (this Mac only), or **📲 Senders** in the launcher. It
shows both QR codes and one Kamran / Hifza switch per lane.

| Lane | Group | Normal sender |
|---|---|---|
| DM order alerts (DM Coupon + DM Reseller) | `hathoro_orders` | Hifza |
| DM tracking ID alerts | `hathoro_tracking` | Hifza |
| DM insights | `hathoro_insights` | Hifza |
| DM placer notices | `recipient_phone`, with `lane: hathoro_notices` | Hifza |
| DM closing (delivery messages + review requests) | `hathoro_messages` | Kamran |
| All HS lanes | `hoank_*` | Kamran |
| Wrenchly orders, tracking, closing | `wrenchly_orders`, `wrenchly_tracking`, `wrenchly_messages` | Kamran |
| HS and WR placer notices, WR insights, anything else | `recipient_phone` | Kamran |

## How it works

* `whatsapp_gateway/server.js`: `ACCOUNTS` holds the two logins (`auth_session/` is Kamran's,
  `auth_session_hifza/` is Hifza's, both git-ignored). `LANES` holds the table above.
* The lane comes from the target group, so callers do not change. Only the placers' notices have no
  group of their own: `engine/orders.py` calls `utils.whatsapp.set_alert_store()` at start-up, and a
  DM placer's no-group alerts carry `lane: hathoro_notices`.
* The sender is decided when an alert leaves the queue, not when it is queued, so a switch also
  moves what is already waiting. Switches are saved in `whatsapp_gateway/routes.json`.
* Each number has its own pacing, and one number being offline does not stop the other.

## When a number cannot send

An alert waits if its number is not linked or is not in the target group. The page says which and
why. Nothing moves to the other number by itself: that is the owner's choice (9 Nov 2026). Unchanged
from before: launcher closed means nothing leaves, and duplicates within 6 h are dropped.

Hifza's number must be a member of every group her lanes post to. The group list refreshes on
WhatsApp's own group events, every 5 minutes, and every minute while the page is open.

## Messages people write in the Orders Alerts groups

`server.js` (`noteGroupMessage`) records messages in HOANK Orders Alerts and Hathoro Orders Alerts
into `whatsapp_gateway/inbox.json` (git-ignored, last 200). `gui/wa_inbox.py` reads `GET /inbox`
every 3 s and shows the launcher's 🔔: one window with who wrote what, a sound and a macOS
notification. Closing the window marks them read (`POST /inbox/seen`).

* Not recorded: the gateway's own alerts (message ids noted before sending, kept 48 h), the owner's
  own messages, reactions, edits, deletions, and anything older than 48 h.
* Hifza typing by hand does count. A group both numbers are in delivers twice; the message id dedupes.
* Messages that arrive while the launcher is closed are kept and show on the 🔔 count when it opens,
  without a popup. While the gateway itself is down, WhatsApp delivers them when it reconnects.
* To watch more groups, add the lane to `INBOX_LANES`.

## Restarting

Kill node on :3005. The next alert, or the launcher's **📲 Senders** button, starts it again through
`utils.whatsapp.ensure_gateway_running()`. Logins and switches persist.
