# Submission, Robust Mobile Notifications

**Your name:** Isaak Yansane-Sisk
**Email:** purebluearth@gmail.com
**Link to your fork or solution:**: https://github.com/pureblueearth/teton-notification-challenge

---

## Stack

Python (FastAPI + uvcorn, asyncpg, httpx) and Postgres as system-of-record and queue. I am most proficient in Python and wanted to use some neat features in Postgres. Nothing fancy.

## The hardest tradeoffs

1. __Using Postgres as Store+Queue vs Separate Dedicated Queue__: This was the first trade-off I thought of when considering the stack to use for ths challenge. I chose to use Postgres as both a store of record (the `events` table) and work queue (the `deliveries` table). My reasoning is that having a separate queue would've introced a dual write problem where we'd have two sources of truth. Say, if the DB write to `events` succeeds but crashes before publishing an event, the event would be stored but never delievered. So, with Postgres, we get some atomicity and a single-source-of-truth. Makes for a simpler setup for this challenge. 
 
2. __Exactly-Once vs. Crash Safety__: As there's no idempotency key in the mock gateway provided, a `kill -9` to a worker risks a duplicate notification (resend) in the window between the gateway returning a 202 and the worker persisting `delivered`. I attempt to close this by leveraging the mock gateway's 'delivery callback' mechanism so that a confirmed delivery is marked `delivered` out-of-band if the worker dies. Though this too will fail if the SIGKILL'd workers haven't come back up since the callback is 'fire-and-forget' (no retry). Also, even without a `kill -9`, if a lease expires under load we could se duplicates.
 
3. __Ordering vs End-to-End Latency__: This was the hardest tradeoff to make. The strict ordering requirement collides with the 5s notify-to-delivery SLA, making it impossible to meet using the provided mock gateway. Unless the ordering requirement is relaxed, events have to be processed single-file per-user, each paying the 2s mean gateway cost. Since correctness (ordering, dedup, &c) is scored higher than latency in the provided rubric, I opted to keep the ordering and suffer the latency.


## How to run it locally

The only prerequisites are having Python 3.10+ installed, [`uv`](https://github.com/astral-sh/uv), and a local Postgres (which I installed on MacOS using `brew install postgresql@16`). I created a small `Makefile` to help with setup:

```bash
cd service
make install
make db-create 
make seed  # seed database
make gateway &  # startup mock gateway
make api &  # startup service api
make workers &  # runs 4 workers
make eval-smoke
make eval-adversarial
```

## Reported metrics

Running `make adversarial` we get:

- Delivery rate on the harness: 100% (every notification reaches every live device)
- Duplicate rate: 0 in every scenario
- Ordering violations: 0
- Latency p50 / p95 / p99: p50 3.9s, p95 13.5s, p99 20.4 (more on these numbers below)
- Behavior under hard kill + restart: if `kill -9` after `notify(event)` intake but before push, the delivery will still have `leased_until` in the past and another worker will pick it up on restart. if `kill -9` happens after the push to gateway but before it `202`'s s.t. the worker can mark the deliverable as "delivered", we leverage the gateway's delivery callback to mark it as such "out-of-band".

The provided harness has what seems like some bugs that I thought might be good to point out. The first was in how delivery rate was calculated as it previously ignored (event, token) pairs which were __always__ expired. That conflicts with the "Delivery rate (must be 100% modulo permanently-expired tokens)" statement in the challenge `README.md`. Secondly, the delivery rate doesn't take into the actual latencies, the `time.sleep(5)` then a premature scorecard leads to another penalty in the delivery rate (we don't wait for all deliveries to drain). Finally, the reported latncies always equal 200ms as it's not measuring actual end-to-end (notify to delivery) latency. The numbers I noted above are from my own querying of `requested_at - delivered_at` after an adversarial run.


## With another week

Given another week, I'd: 

1. Experiment using `LISTEN/NOTIFY` to remove "useless" queries/polling during idle times.
2. Consider dispatching to a real queue, takes a larger redesign.
3. If we can relax the ordering, consider having a high-priority lane.
4. What happens when the `deliveries` table becomes inevitably very large? Would consider time-partitioning the table or archiving rows that have reached a terminal state.
5. Run the service against a real gateway that has idempotency keys and remeasure latency. 
