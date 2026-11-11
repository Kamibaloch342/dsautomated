# WhatsApp senders (9 Nov 2026)

The gateway keeps two WhatsApp numbers linked at once and sends each alert from the number chosen
for its lane. Page: <http://127.0.0.1:3005> (this Mac only), or **📲 Senders** in the launcher. It
shows both QR codes and one Kamran / Hifza switch per lane.

| Lane | Group | Normal sender |
|---|---|---|
| DM order alerts (DM Coupon + DM Reseller) | `hathoro_orders` | Hifza |
| DM tracking ID alerts | `hathoro_tracking` | Hifza |
| DM insights, and DM placer notices since 11 Nov 2026 | `hathoro_insights` | Hifza |
| DM closing (delivery messages + review requests) | `hathoro_messages` | Kamran |
| All HS lanes; HS placer notices go to `hoank_insights` since 11 Nov 2026 | `hoank_*` | Kamran |
| Wrenchly orders, tracking, closing | `wrenchly_orders`, `wrenchly_tracking`, `wrenchly_messages` | Kamran |
| WR placer notices, WR insights (no `wrenchly_insights` group yet), anything else | `recipient_phone` | Kamran |
| Buyer requested cancel | DM: `hathoro_orders`, WR: `wrenchly_orders`, HS: `hoank_insights` | as the group's lane |

## How it works

* No code boxes (owner, 11 Nov 2026): `utils/whatsapp.py` (`_plain`) drops every backtick from a
  text or caption before it goes to the gateway, so ids show as plain text. Bold and italic stay.

* `whatsapp_gateway/server.js`: `ACCOUNTS` holds the two logins (`auth_session/` is Kamran's,
  `auth_session_hifza/` is Hifza's, both git-ignored). `LANES` in `server.js` holds the sender
  lanes; the table shows where each kind of alert lands.
* The lane comes from the target group, so callers do not change. Only the placers' notices have no
  group of their own: `engine/orders.py` calls `utils.whatsapp.set_alert_store(STORE_ACCOUNT)` at
  start-up.
* Placer notices go to the store's Insights group (owner, 11 Nov 2026: "General group > send to
  insights"). `utils.whatsapp._notice_target` sends every text or photo a placer sends without a
  group to `get_whatsapp_group(<store>, "insights")`: delivery time high / no Zoro date / cart busy /
  card declined / two profiles on one Zoro account, Manual-C no coupon, manual code which item, big
  order '20', placed then cancelled, the Ultra out-of-stock and back-in-stock lines, and HOANK's
  buyer-requested-cancel. The sender then follows that group's lane: DM Insights from Hifza, HS
  Insights from Kamran, so the DM Insights switch on the page now also moves DM placer notices.
  `hathoro_notices` is used only if Hathoro had no Insights group (the notice would fall back to
  `recipient_phone`).
* Wrenchly has no `wrenchly_insights` group, so its placer notices still go to `recipient_phone`.
  Adding `wrenchly_insights` under `whatsapp.groups` in `bot_config.json` moves them there with no
  code change (the gateway then sends them on its `general` lane until it gets a Wrenchly Insights
  lane). The tracker, closing, the dashboard and the launcher never set a store: their sends without
  a group still go to `recipient_phone`.
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
