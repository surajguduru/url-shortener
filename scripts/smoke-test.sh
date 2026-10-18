#!/usr/bin/env bash
# Exercises the real user journey through the ingress and prints the version
# that served it, so the result can be tied to a specific build.
set -euo pipefail
BASE=${1:-http://shortener.localhost:8080}

echo "version: $(curl -fsS "$BASE/version")"

created=$(curl -fsS -X POST "$BASE/api/links" -H 'content-type: application/json' \
  -d '{"url":"https://example.com/smoke-test"}')
code=$(echo "$created" | python3 -c 'import sys, json; print(json.load(sys.stdin)["code"])')
echo "created: $created"

location=$(curl -fsS -o /dev/null -w '%{redirect_url}' "$BASE/$code")
[[ "$location" == "https://example.com/smoke-test" ]] || { echo "FAIL: redirect went to '$location'"; exit 1; }
echo "redirect: /$code -> $location"

clicks=$(curl -fsS "$BASE/api/links/$code" | python3 -c 'import sys, json; print(json.load(sys.stdin)["clicks"])')
[[ "$clicks" == "1" ]] || { echo "FAIL: expected 1 click, got $clicks"; exit 1; }
echo "clicks: $clicks"

status=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/does-not-exist")
[[ "$status" == "404" ]] || { echo "FAIL: unknown code returned $status"; exit 1; }
echo "unknown code: 404"

echo "SMOKE TEST PASSED"
