# Link analysis API

Takes a website URL, reads the page, and returns a structured analysis of the business behind
it: competitors, search queries, SEO fixes, services and target audience.

FastAPI and Gemini 3.5 Flash-Lite. The model response is constrained by a JSON schema, so every
successful request returns the same shape.

**Local API:** http://localhost:4000/link-analysis
**Docs:** http://localhost:4000/docs

The repo includes a `Dockerfile` and `render.yaml`; the deployment section below has the
two-step Render setup if a hosted demo is needed.

## Run locally

```bash
git clone https://github.com/AbhishekPriy9/link-analysis-api.git
cd link-analysis-api

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# put your key in .env (see below)

uvicorn app.main:app --host 0.0.0.0 --port 4000
```

Open http://localhost:4000/docs for the Swagger UI.

## API key

Create a key at https://aistudio.google.com/apikey. The free tier covers
`gemini-3.5-flash-lite`, which is the model this service uses. `gemini-3.8-flash` is better on
paper but was not available on the free key during testing; see the comparison below.

Put it in `.env`:

```
GEMINI_API_KEY=your_key_here
```

`.env` is git-ignored. The key is read at startup and is never logged or returned in a
response.

## Request

```bash
curl -X POST http://localhost:4000/link-analysis \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://laravel.com"}'
```

## Response

The full response from the measured run on 19 November 2026 is in
[`examples/laravel-response.json`](examples/laravel-response.json). It is abridged below, with
one query and one service shown to keep this page readable.

The first HTTP call took 11.3 s; the same URL served from cache took 4 ms:

```text
INFO: 127.0.0.1:48658 - "POST /link-analysis HTTP/1.1" 200 OK
INFO: 127.0.0.1:57604 - "POST /link-analysis HTTP/1.1" 200 OK
```

```json
{
  "success": true,
  "website": "https://laravel.com",
  "brand": {
    "name": "Laravel",
    "category": "PHP Web Framework",
    "summary": "Laravel is an open-source PHP web framework designed for building modern web applications and AI agents with expressive syntax. It offers a comprehensive ecosystem including hosting, preview environments, and performance monitoring tools.",
    "products_services": [
      "Laravel Framework",
      "Laravel Cloud",
      "Preview Environments",
      "Laravel Nightwatch",
      "AI SDK and Boost"
    ]
  },
  "analysis": {
    "competitor_analysis": {
      "brand_overview": "Laravel is a leading PHP web framework known for its developer-friendly ecosystem, elegant syntax, and rapid deployment capabilities.",
      "main_category": "Web Development Frameworks",
      "competitors": [
        {
          "name": "Symfony",
          "website": "https://symfony.com",
          "strengths": ["Highly modular and flexible", "Strong enterprise adoption"],
          "weaknesses": ["Steeper learning curve for beginners"]
        }
      ],
      "strengths": ["Extremely rich ecosystem with first-party tools like Cloud and Nightwatch"],
      "opportunities": ["Expand enterprise adoption through advanced cloud deployment features"],
      "disclaimer": "Competitors are AI suggestions based on the page content and public knowledge. Verify them before using this analysis."
    },
    "target_search_queries": [
      {
        "query": "Laravel framework documentation",
        "platforms": ["Google"],
        "intent": "Find technical guides and API references for building with Laravel",
        "business_relevance": "Drives developers directly to core educational and technical resources",
        "target_audience": "Software developers and engineers"
      }
    ],
    "top_services": [
      {
        "name": "Laravel Web Framework",
        "description": "An open-source PHP framework with built-in tools for routing, authentication, and database ORM.",
        "target_industry": "Software Development",
        "target_customer": "Web Developers",
        "main_benefit": "Speeds up application development with clean and expressive code.",
        "source": "website"
      }
    ]
  },
  "meta": {
    "model": "gemini-3.5-flash-lite",
    "processing_ms": 11310,
    "fetched_at": "2026-11-19T02:33:19.682066Z",
    "input_tokens": 1046,
    "output_tokens": 2032,
    "cached": false
  }
}
```

## How it works

1. Validate the URL and resolve the host. Private, loopback, link-local and reserved addresses
   are rejected before any request is made, and again after every redirect. The validated IP is
   pinned into the connection, so a second DNS answer cannot redirect the socket elsewhere.
2. Fetch the page with httpx (15 s timeout, 5 redirects, 3 MB cap) and accept it only if it is
   `text/html` or `application/xhtml+xml`.
3. Strip scripts, styles, forms and navigation noise. Keep the title, meta description,
   headings and up to 12,000 characters of body text.
4. Send one primary request to Gemini with the page content and a JSON schema covering all five
   sections. Thinking is set to `medium`; the token cap is 16,384 to leave room for it. A
   fallback model is tried once if the provider returns 429, 500 or 503.
5. Validate the model output with Pydantic and return it. A 15-minute in-memory cache avoids
   paying twice for the same URL.

The first successful call is the expensive one. The second call for the same URL returns
`meta.cached: true` with `processing_ms: 0` and does not touch the network.

## Errors

| Status | Code | When |
|---:|---|---|
| 400 | `invalid_url` | Bad scheme, credentials in the URL, private/internal host |
| 422 | `invalid_request` | Missing, empty, too long or malformed request body |
| 502 | `fetch_failed` | DNS failure, page unreachable, HTTP error, non-HTML or empty page |
| 502 | `llm_failed` | Gemini error, timeout, invalid JSON |
| 503 | `llm_not_configured` | `GEMINI_API_KEY` is missing |

All errors use `{"success": false, "error": {"code": "...", "message": "..."}}`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | required | Google AI Studio key |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | primary model |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash` | retry on 429/500/503 |
| `LLM_TIMEOUT_SECONDS` | `75` | per-call timeout |
| `FETCH_TIMEOUT_SECONDS` | `15` | page fetch timeout |
| `MAX_CONTENT_CHARS` | `12000` | text sent to the model |
| `MAX_RESPONSE_BYTES` | `3000000` | download cap |
| `MAX_REDIRECTS` | `5` | redirect cap |
| `CACHE_TTL_SECONDS` | `900` | cache lifetime |

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

All tests are offline. HTTP is mocked with `httpx.MockTransport` and the LLM client is faked,
so the suite does not need a key or network access. Current result: **78 passed**.

## LLM choice

Prices checked on 19 November 2026 from the provider pricing pages. USD per 1M tokens.

| Model | Input | Output | Context | Structured JSON | Free tier |
|---|---:|---:|---|---|---|
| Gemini 3.8 Flash | 0.75 | 3.75 | 1,048,576 in / 65,536 out | `response_format` schema | yes |
| Gemini 3.5 Flash-Lite | 0.30 | 2.50 | 1,048,576 in / 65,536 out | `response_format` schema | yes |
| GPT-5.4 mini | 0.75 | 4.50 | 400,000 (272,000 max input) | structured outputs | no |
| Claude Sonnet 5.5 | 2.00 | 10.00 | 1,000,000 | `output_config` JSON schema | no |

Cost per analysis at the measured 1,046 input and 2,032 output tokens:

| Model | Cost per call |
|---|---:|
| Gemini 3.5 Flash-Lite | $0.0054 |
| Gemini 3.8 Flash | $0.0084 |
| GPT-5.4 mini | $0.0099 |
| Claude Sonnet 5.5 | $0.0224 |

Those figures are arithmetic from the published prices, not invoices. At the free-tier limits
the Gemini calls cost nothing, so the measured run cost $0.00.

**Recommendation: Gemini 3.5 Flash-Lite.** On the free key, this was the only Flash model that
completed reliably:

| Model | Result on 19 Nov 2026 |
|---|---|
| Gemini 3.5 Flash-Lite | full analysis in 11.3 s |
| Gemini 3.8 Flash | timed out at 120 s on two attempts |
| Gemini 3.6 Flash | 503, high demand |
| Gemini 3.5 Flash | 503, high demand |

Flash-Lite has a 1M-token input window, enforces the response schema, and is the cheapest model
in the table. Gemini 3.8 Flash is the better model on paper and is a one-line config change if
the key has access to it; it is not the default here because it did not respond. GPT-5.4 mini
is the non-Google alternative, but its output tokens cost more.

I did not run a cross-provider accuracy benchmark. The choice is based on the availability
results above, price, context size and schema support. The 11.3 s is a single end-to-end
measurement (fetch, one model call, validation) of the local API, not a benchmark.

Sources:
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
- https://ai.google.dev/gemini-api/docs/thinking
- https://platform.openai.com/docs/models/gpt-5.4-mini
- https://platform.claude.com/docs/en/about-claude/pricing
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs

## Cost controls

- One model call produces all five sections.
- Page text is capped at 12,000 characters after HTML cleaning.
- The same URL is cached for 15 minutes.
- Search grounding is off, so there are no per-search charges.
- The fallback model is only used after a provider error, not on every request.
- Thinking is set to `medium`. Lowering it to `low` is a one-line change if latency matters
  more than depth; Flash-Lite defaults to `minimal` when the field is omitted.

## Limits

- Competitor names are model suggestions unless the page itself names them. The API replaces
  the model's disclaimer with a fixed one, so this is always stated in the response.
- No search volume or ranking data is reported.
- JavaScript-heavy sites that render on the client return little usable text.
- The SSRF check resolves the host before each request and redirect, and the validated IP is
  pinned into the connection. Redirects are revalidated before they are followed.
- The cache is per process. A restart clears it.

## Deploy on Render

The repo contains `render.yaml` and a `Dockerfile`.

[Create a Render Blueprint from this repo](https://render.com/deploy?repo=https://github.com/AbhishekPriy9/link-analysis-api)

1. In the Render dashboard choose **New → Blueprint** and connect this repository.
2. Apply the blueprint. When Render asks for `GEMINI_API_KEY`, paste the key.
3. The service builds the image and serves `/link-analysis` and `/docs` on its
   `onrender.com` URL.

The free plan sleeps after a period without traffic, so the first request after a pause can
take up to a minute to wake.

## Layout

```
app/
  main.py                  FastAPI app, routes, error handlers
  config.py                environment settings
  schemas.py               request, response, LLM schema
  services/
    website_extractor.py   fetch + HTML cleaning
    llm_service.py         Gemini call + schema validation + fallback
    analysis_service.py    orchestration + cache
  utils/
    validators.py          URL and SSRF checks
    cache.py               TTL cache
tests/                     offline tests
```
