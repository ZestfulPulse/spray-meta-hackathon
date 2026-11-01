# P0-G5 — Meta provider secret location (where the Meta API key is read)

This report states WHERE the SPRAY runtime reads the Meta Model API key, how
the owner supplies it, and confirms that no credential value is shown here, no
value is committed to the repository, and no live call is made. It is an
offline, no-live-call report by design.

## Summary (no value shown)

- Credential kind: Meta Model API key (dev.meta.ai, Muse Spark series). The
  retired legacy Llama API (api.llama.com) is NOT used.
- Environment variable NAME the runtime reads: `META_API_KEY`.
- Where it is read: `spray_schema/meta_planner.py`, via `os.environ` at
  runtime, inside the `MetaPlanner` constructor.
- How it is used: as a request header only. It is never placed in the request
  body, never logged, never printed, and never written to any evidence file.
- It is NEVER a source literal, NEVER committed, and NEVER echoed. This
  document shows only the variable NAME, never any value.

## Exact read location

`spray_schema/meta_planner.py` reads the key from the process environment
through `os.environ.get` using the environment variable named `META_API_KEY`.
The constructor also accepts an explicit `api_key` keyword argument, which is
used only by the offline tests that inject a non-credential placeholder through
a fake transport. In normal operation the key comes from the environment
variable named above and from nowhere else.

If the environment variable named `META_API_KEY` is absent (and no explicit
test override is supplied), the adapter fails closed by raising before any
request is constructed, so a missing credential cannot lead to a partial or
unauthenticated call.

## Header-only, never in the body

Every outbound Meta request applies the S6 redaction layer
(`spray_schema/model_observation.py`: `redact` and `sanitize_observation`) to
the full content that would be sent, and the API key is attached only as a
request header. The authoritative proof lives in
`tests/test_meta_request_redaction.py`, which uses a fake transport to capture
the exact bytes that would be sent and asserts that no secret, token,
credentialed URL, JWT, AWS key, owner-identifying string, or the API key itself
appears anywhere in the request body.

## How the owner sets it

The owner exports the environment variable named `META_API_KEY` in their own
shell or secret manager before running any code path that constructs a
`MetaPlanner`. The value lives only in the owner's environment. It is never
placed in a file in this repository, never committed, and never shown in logs,
evidence, or this document.

Plainly stated: no value is shown in this report, no value exists anywhere in
the repository, and no live call is made. The exact Muse Spark model id must
still be confirmed by the owner against the Meta Model API Models page at
https://dev.meta.ai/models (the sandbox could not reach it; see the note in
`spray_schema/meta_planner.py`).

See also [the repository overview](../README.md).
