# Mock push gateway

Single-file HTTP server that simulates how real push gateways (APNs, FCM, web push) actually behave, flakiness and all. Your service talks to it instead of a real provider.

This stub will be fleshed out before public release. Until then, treat it as the spec your service must work against:

## Endpoints

```
POST /push
{
  "device_token": "...",
  "platform":     "ios" | "android" | "web",
  "title":        "...",
  "body":         "...",
  "priority":     "high" | "normal",
  "callback_url": "..."   // optional, for async delivery confirmation
}

Responses:
  202 Accepted             , queued for delivery
  410 Gone                 , device_token expired; stop sending
  429 Too Many Requests    , Retry-After header set
  500 / 503                , transient; retry
  (no response within 30s) , treat as dropped
```

## Behavior knobs (env vars)

| Var | Default | Meaning |
|---|---|---|
| `FAIL_5XX_RATE` | `0.05` | Random 5xx response rate |
| `RATE_LIMIT_RPS_PER_TOKEN` | `5` | After this, returns 429 with Retry-After |
| `TOKEN_EXPIRY_RATE` | `0.02` | Probability a given token is marked expired this hour |
| `DROP_RATE` | `0.01` | Probability a request gets no response |
| `LATENCY_P99_MS` | `4000` | Tail latency target |

## Running locally

```bash
# fleshing-out: docker run -p 9000:9000 teton-challenge-mock-gateway
# or: python -m mock_gateway --port 9000
```

> **Note:** until this is shipped, infer the interface from the spec above and the README in the repo root. We will run your service against the real mock when grading.
