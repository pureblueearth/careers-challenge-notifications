# Teton Challenge, Robust Mobile Notifications

> **No prior experience required. The solution is the signal.**
> Every submission gets feedback within 7 days. → `info@teton.ai`

---

At Teton we run AI in care homes that watches for things that matter, a resident getting out of bed, a fall, a change in breathing. When something happens, a nurse's phone has to know. Quickly. Once.

The phone ringing is the moment we earn trust. Drop the notification and we miss the fall. Send it twice and we cry wolf. Send it late and we may as well not have sent it.

**This challenge is about building the part of our system that does that, and making it actually robust, not just "works on a good day".**

## The problem

Build a notification service. It accepts an incoming `notify(event)` request and is responsible for delivering that event to one or more user devices via a (mocked) push gateway that behaves like the real ones do, APNs, FCM, web push.

Sounds easy. Until the gateway returns 429. Or the token expired three weeks ago and nobody told you. Or your worker died holding the message in memory. Or the upstream service retried because it didn't see your ack, and now the nurse's phone has rung twice for the same fall at 3am.

## What you build

A service (any language, any stack) that exposes:

```
POST /notify
{
  "event_id": "evt_abc123",
  "recipient_user_id": "user_42",
  "title": "Fall detected, Room 14",
  "body": "Mrs. Hansen, please check immediately",
  "priority": "high",       // high | normal
  "occurred_at": "2026-05-23T18:53:49Z"
}
```

A user has 1..N device tokens. You can manage them however you like:

```
POST   /devices    {user_id, token, platform}
DELETE /devices/{token}
```

We provide a **mock gateway** (HTTP server in this repo, single binary or Docker) that simulates real-world push behavior:

- Random 5xx responses (~5%)
- 429 rate limits with `Retry-After`
- Random tail latency (p99 several seconds)
- Token expiry → returns `410 Gone`; your job is to invalidate
- Occasional outright drops (no response within 30s)
- Per-device delivery confirmation via a callback you register

## What "robust" means here

The bar, these are not nice-to-haves:

1. **Every event is delivered at least once** to every valid device within **5 seconds p95** of `notify` returning 2xx, even with the gateway misbehaving as above.
2. **No event is delivered twice** to the same device, even if `/notify` is retried by an upstream caller with the same `event_id`.
3. **Per-recipient ordering is preserved.** If event A arrives before event B for the same user, A is delivered first to all of that user's devices.
4. **Expired tokens are detected and removed**, automatically, without dropping the event.
5. **Worker crashes don't drop in-flight events.** If you kill -9 the process mid-delivery, every event still gets delivered after restart.
6. **High-priority events bypass low-priority queue depth.** A fall notification can't sit behind 10,000 marketing pings.
7. **You have observability**, you can answer "what happened to event_id X?" in under 30 seconds, with no live debugging.

## What we evaluate

We will run our own grading harness against your service. It:

- Submits **10,000 events** across **500 simulated users**, each with 1-3 devices, at variable rates including bursts.
- Configures the mock gateway to fail at the rates above plus an adversarial mode with 30% gateway failure.
- Retries some `notify` calls with the same `event_id` (testing your dedup).
- Hard-kills your service mid-run and brings it back up.
- Measures:
  - **Delivery rate** (must be 100% modulo permanently-expired tokens)
  - **Duplicate rate** (must be 0)
  - **Ordering violations per user** (must be 0)
  - **Latency** p50, p95, p99 from `notify` to delivery
  - **Recovery behavior** after crash

We will also **read your code**. A solution that passes the harness but is unreadable, or relies on a single magic library that does it all for you, scores lower than a clean, well-reasoned solution that's a few percentage points worse on a metric.

## Scoring (out of 100)

| Category | Points |
|---|---|
| Correctness (no drops, no dupes, ordering) | 35 |
| Robustness under failure (gateway flakiness, crash recovery) | 25 |
| Latency under load | 15 |
| Code quality and design clarity | 15 |
| Observability (logs, metrics, traceability) | 10 |

**Pass bar:** 75. Below that, we won't reply with a yes, but we'll always reply.

## What's in this repo

```
.
├── README.md         , this file
├── SUBMISSION.md     , fill this in with your submission
├── mock_gateway/     , the push gateway simulator (read its README)
├── load_harness/     , a basic load generator you can run locally
└── eval/             , the test scenarios we'll run against your service
```

## What we are explicitly **not** looking for

- A 50-page architecture document.
- A clever framework choice. Use what you know.
- A UI. There is no UI.
- Marketing-grade README in your submission. Just a few sentences on the choices that mattered.

## What to send us

Email **info@teton.ai** with subject **`Solution: Robust mobile notifications`** and:

1. A link to your fork (public) or a tarball.
2. A short writeup, under 400 words, covering:
   - Stack choice and why.
   - The two or three hardest tradeoffs you made.
   - What you would do next if you had another week.
3. Instructions to run your service against `mock_gateway/`.
4. Your **CV** (attached), plus **LinkedIn** and **GitHub** links so we can put the work in context.

**We reply with feedback within 7 days, every submission, no exceptions.** If your work hits the bar, the next step is a conversation with engineers.

## Notes

- Time: most strong candidates spend 8–20 hours on this. Spend more if you want; spend less if you can.
- Stack: any language. Any database. Any queue. Hosted infra is allowed; we'll run it locally too, so document the local path.
- LLMs: use them as you normally would. We don't care how you get there, we care that you understand every line you ship and the choices behind it.

Good luck.

The Teton engineering team
