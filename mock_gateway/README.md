# Mock push gateway

Single-file HTTP server that simulates how real push gateways (APNs, FCM, web push) actually behave, flakiness and all. Your service talks to it instead of a real provider.

## Running

```bash
python3 mock_gateway/server.py
# or:  make gateway
```

Listens on `:9000` by default. No external dependencies — Python 3.10+ stdlib only.

## Endpoints

### `POST /push`

Your service POSTs here.

```json
{
  "device_token": "...",
  "platform":     "ios" | "android" | "web",
  "event_id":     "...",       // include if you want delivery tracked in /_stats
  "title":        "...",
  "body":         "...",
  "priority":     "high" | "normal",
  "callback_url": "..."        // optional, gateway POSTs delivery confirmation here
}
```

| Response                 | Meaning                                  |
|--------------------------|------------------------------------------|
| `202 Accepted`           | Queued for delivery                      |
| `410 Gone`               | `device_token` expired; stop sending     |
| `429 Too Many Requests`  | Rate limited; `Retry-After` header set   |
| `500` / `503`            | Transient; retry with backoff            |
| (no response within 30s) | Treat as dropped                         |

### `GET /_stats`

Returns every push attempt the gateway has seen, plus the set of tokens that have been marked expired. The eval harness (`eval/run.py`) uses this to compute delivery rate, duplicates, and latency.

### `POST /_reset`

Clears the in-memory state — fresh run.

### `GET /_health`

Liveness check.

## Behaviour knobs (env vars)

| Var | Default | Meaning |
|---|---|---|
| `PORT` | `9000` | Listen port |
| `FAIL_5XX_RATE` | `0.05` | Probability of returning 5xx |
| `RATE_LIMIT_RPS_PER_TOKEN` | `5` | Sliding 1s window; over → 429 |
| `TOKEN_EXPIRY_RATE` | `0.02` | Probability a given token is marked expired on first sight |
| `DROP_RATE` | `0.01` | Probability the gateway never responds |
| `LATENCY_MAX_MS` | `4000` | Upper bound on simulated tail latency |
| `CALLBACK_DELAY_MS` | `200` | Delay before delivery callback fires |

For the adversarial scenario, restart the gateway with:

```bash
FAIL_5XX_RATE=0.3 DROP_RATE=0.1 python3 mock_gateway/server.py
```
