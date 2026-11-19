#!/usr/bin/env bash
set -euo pipefail
curl -sS -X POST http://localhost:4000/link-analysis \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://laravel.com"}'
