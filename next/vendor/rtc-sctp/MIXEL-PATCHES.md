# rtc-sctp 0.21.1 patches (Mixel, 18 Nov 2026)

Sender-side congestion control that tolerates random loss (nothing on the wire, no timers touched: RTO_MIN and the INIT/COOKIE timers stay as in the registry crate, see the 2.0.17 note in vendor/webrtc/MIXEL-PATCHES.md).
- `note_rtt` / `path_queueing`: lowest round trip of 30 s; the path is "queueing" when the smoothed round trip exceeds it by 100 ms + a quarter of it.
- Congestion avoidance grows by 8 MTU per window instead of 1 MTU while the path is not queueing; fast recovery trims the window to 7/8 instead of 1/2 while the path is not queueing; window capped at 192 kB.
Reason: one association at 1 % loss and 280 ms round trip tops out near 50 kB/s with Reno-type control (Mathis bound).
