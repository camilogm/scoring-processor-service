# ADR 0001: Deploy on Fly.io, use Vercel for the model

- Status: accepted
- Date: 2026-09-29

## Context

We need to run the service somewhere other than a laptop, with a hosted model, and know what each
clip costs (target: under $1 per clip, docs/README.md section 22).

We were given a Vercel AI Gateway key. That key is for **calling models** through
`https://ai-gateway.vercel.sh/v1`. It is not a hosting credential. The design already planned for
it: the LLM client is OpenAI-compatible, so Ollama and the gateway are a config change.

## Decision

- **Model:** Vercel AI Gateway, using the key we were given.
- **Hosting:** Fly.io, one machine running the existing `Dockerfile`, a volume for uploads and the
  Whisper cache, and Fly Managed Postgres.
- **Cost:** each analysis stores the cost the gateway actually billed (`GET /v1/generation`) in
  `provenance.cost_usd`. `make spend` shows the balance and total spend.

## Why not host on Vercel

Vercel runs code as functions tied to a request. This service is a long-lived process. Four
assumptions of the current design do not hold on Vercel:

| The service today | On Vercel Functions |
|---|---|
| `POST` returns 202 and a background thread does the work | Work after the response is not guaranteed to run |
| Clips up to 200 MB arrive in the request body | Request bodies are capped at 4.5 MB (413 above that) |
| On startup, a sweep marks unfinished analyses as interrupted | Many instances start in parallel; each would "interrupt" work another instance is still doing |
| Uploads and the Whisper model live on disk | Only a temporary `/tmp` per instance |

Making it work on Vercel means redesigning uploads (Vercel Blob), processing (Queues or
Workflows) and crash recovery. That is a different project. On Fly the same container runs
unchanged, which keeps this task small.

## Consequences

- The deployment has one machine, because the in-process queue and the startup sweep assume a
  single instance. Scaling out needs a real queue first.
- The machine does not auto-stop: Fly stops idle machines based on HTTP traffic and would cut off
  an analysis running in the background. It costs a small fixed amount while it is up.
- If the team later wants everything on Vercel, the table above is the list of work.
