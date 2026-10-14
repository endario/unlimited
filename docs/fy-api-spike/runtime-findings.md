# FY gateway runtime spike

Checked 2026-10-13 against `http://aicoding.woyoalliance.com:90`, using `FY_API_KEY` from the existing local environment file. The owner explicitly authorized sending the key over HTTP. No repository content was sent; inference prompts requested only `OK`. No load testing or configuration changes were performed.

## Usability

| Request | Observed result |
| --- | --- |
| `GET /v1/models` | HTTP 200; lists GPT and GLM aliases, including `gpt-6.1-sol` and `glm-5.3`. |
| `POST /v1/chat/completions`, `glm-5.3` | HTTP 200 and `OK` with `max_tokens: 256`. An earlier budget of 64 ended with `finish_reason: length`, reasoning text and no answer. |
| `POST /v1/chat/completions`, `gpt-6.1-sol` | HTTP 200 and `OK`. |
| `POST /v1/messages`, `gpt-6.1-sol` | HTTP 200 and an Anthropic-shaped text response containing `OK`. |
| `POST /v1/responses`, `gpt-6.1-sol`, structured input array | Both streaming and non-streaming returned `OK`; the stream included `response.completed`. |
| Same Responses request with string input | Non-streaming returned HTTP 502, `Codex returned empty responses across all available accounts`, on both attempts. Streaming returned HTTP 200 but an error event saying input was missing. |

For Responses, the working input was `[{"role":"user","content":[{"type":"input_text","text":"Reply with exactly OK."}]}]`. String shorthand is not interoperable in these tests. The successful Responses payloads reported `max_output_tokens: null` despite the request specifying 128; output-budget enforcement is not established. Streaming completion reported an empty `output` array despite emitting the answer through delta events. Clients should not assume the final event contains the accumulated answer.

A follow-up Anthropic Messages stream with `gpt-6.1-sol` emitted an `echo` tool-use block with JSON input `{"text":"OK"}` and `stop_reason: tool_use`. `gpt-6-luna` returned `OK` through Messages. `glm-5.3-flash` failed on Messages and Chat Completions with HTTP 503, `model_not_found`, and no available channel under group `aic`.

These checks establish minimal inference connectivity and a streamed tool call, not long-context behavior, sustained throughput or upstream model identity. Model names are gateway aliases. GLM Chat Completions additionally carried `usage_source: anthropic` and `billing_usage.source: claude_messages`; this demonstrates gateway translation/accounting metadata, not a verified underlying vendor model.

## Quota and accounting

Authenticated `GET /api/usage/token/` returned:

```json
{
  "expires_at": 0,
  "model_limits": {},
  "model_limits_enabled": false,
  "total_available": 0,
  "total_granted": 0,
  "total_used": 0,
  "unlimited_quota": true
}
```

The same quota values remained after the inference tests. `GET /v1/dashboard/billing/usage` returned `total_usage: 0` before and after them. Thus these endpoints expose no accumulating usage for this key during this spike. Do not interpret zero available quota as exhaustion when `unlimited_quota` is true.

Follow-up `GET /api/log/token` returned token-scoped request logs, including `created_at`, `type`, `model_name`, `prompt_tokens`, `completion_tokens` and `quota`. Successful inference entries included nonzero prompt/completion token counts but zero accounting quota. Thus observed request token usage is available separately from quota. This is a bounded recent history, not a lifetime aggregate: upstream [`GetLogByTokenId`](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.37/model/log.go) orders newest first and limits to `common.MaxRecentItems`. It provides no query pagination through [`GetLogByKey`](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.37/controller/log.go).

`GET /v1/dashboard/billing/subscription` returned `soft_limit_usd`, `hard_limit_usd` and `system_hard_limit_usd` all equal to `100000000`, with `access_until: 0`. Upstream uses this as an unlimited-token sentinel, not a measured dollar balance. See [primary-source findings](primary-sources.md) for quota interpretation and rate-limit mechanisms.

The key has no configured token quota cap, expiration or model allowlist according to its usage endpoint. This does not establish an unlimited account balance, absence of request pacing, or unlimited upstream capacity. No rate-limit or retry headers were observed in inference responses. RPM, TPM, concurrency, account funding and upstream account limits remain unknown; no saturation test was attempted.

## Transport and disposition

HTTPS failed without credentials on port 90 (`WRONG_VERSION_NUMBER`) and the default HTTPS port (`UNEXPECTED_EOF_WHILE_READING`). Requests were sent over the authorized plain-HTTP endpoint. This remains unsuitable for sensitive project content without an encrypted access path.
