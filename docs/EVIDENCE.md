# Verification evidence

Everything below came from this project's own cluster and repository. Commands
are included so each result can be reproduced.

- **Environment:** kind v0.33.0 (Kubernetes v1.37.0, 3 nodes), Docker 29.5.2,
  Helm 4.3.1, macOS (arm64).
- **Baseline:** `fd7d63e`. **PR branch:** `devops`.

## 1. Source → CI → image

> Filled in after the branch is pushed. You need: the CI run URL for the final
> commit, the image tag it pushed (`sha-xxxxxxx`), and the promote run URL.

| Item | Value |
|---|---|
| Final commit | _pending push_ |
| CI run (all jobs green) | _pending push_ |
| Image | `ghcr.io/surajguduru/url-shortener:sha-…` |
| Promote run (with approval) | _pending push_ |
| Release commit created by promote | _pending push_ |

Local equivalents of the CI gates, run before pushing:

```
$ make test            → 11 passed (includes a real `alembic upgrade head` via the CLI)
$ make lint            → ruff: All checks passed; helm lint: 0 failed;
                         kubeconform: 17 resources, 15 valid, 2 skipped (CRDs; CI validates them via the CRD catalog)
$ trivy fs --scanners secret,misconfig --severity HIGH,CRITICAL .   → 0 findings
$ trivy image --severity HIGH,CRITICAL --ignore-unfixed <image>     → debian 13.7: 0, Python: 0
```

The Trivy misconfiguration scan caught one real issue during development
(Jaeger without `readOnlyRootFilesystem`). It was fixed, not suppressed.

## 2. Deployed version and a working user journey

```
$ make smoke
version: {"version":"dev-7515b28","commit":"7515b28"}
created: {"code":"P6eOsBZ","target_url":"https://example.com/smoke-test","clicks":0,...}
redirect: /P6eOsBZ -> https://example.com/smoke-test
clicks: 1
unknown code: 404
SMOKE TEST PASSED
```

The smoke test goes through the real ingress (`shortener.localhost:8080` →
Traefik → Service → pod → Postgres), and the version it prints identifies the
build that served it.

## 3. Logs ↔ traces ↔ metrics for one request

Log line from the app pod (JSON, carries the trace id and the version):

```json
{"time": "2026-10-17 06:17:41,646", "level": "INFO", "logger": "shortener.access", "message": "request",
 "method": "GET", "route": "/{code}", "status": 307, "duration_ms": 15.89,
 "trace_id": "c816c94bff509784d3ef6173126aac60", "span_id": "52d1cb08f65b0d62", "version": "dev-fd7d63e"}
```

The same request's trace in Jaeger (`/api/traces/<trace_id>`), after the fix in §6:

```
GET /{code}          16367 us
  connect              791 us
  SELECT shortener    2068 us
  UPDATE shortener     515 us
```

Prometheus is scraping both pods (`up{namespace="shortener"}` = 1 for each), and
all six alert rules are loaded: `ShortenerErrorBudgetFastBurn`,
`ShortenerErrorBudgetSlowBurn`, `ShortenerRedirectLatencyHigh`,
`ShortenerNoReadyPods`, `ShortenerDatabaseDown`, `ShortenerMigrationFailed`.
Grafana has the "URL Shortener" dashboard and Prometheus, Jaeger and
Alertmanager datasources.

## 4. Zero-downtime rolling update

A request loop hit a redirect every 50 ms during `kubectl rollout restart deploy/url-shortener`:

```
requests during rolling restart:
 960 307
```

Every one of the 960 requests succeeded, with zero errors.

## 5. Fault injection and recovery: database outage

| Time (UTC) | Action / observation |
|---|---|
| 06:22:04 | Created link `18pLBap`, then `kubectl scale sts/url-shortener-postgres --replicas=0` |
| +25 s | App pods `0/1 Running`, **restarts = 0** (liveness ignores the DB, by design); user request → **HTTP 503** from Traefik |
| 06:25:09 | Alertmanager: `ShortenerDatabaseDown` (critical), `ShortenerNoReadyPods` (critical) firing |
| 06:25:17 | `kubectl scale … --replicas=1` |
| 06:25:24 | Both app pods Ready again (7 s); `/readyz` → `{"status":"ok","database":"up"}` |
| 06:25:24 | The link created *before* the outage redirects correctly: **data survived on the PVC** |
| 06:26:10 | No Shortener alerts firing |

- **Symptom:** 503s for every request.
- **Hypothesis:** the database is unavailable, so the pods were removed from the
  Service by readiness.
- **Evidence:** `/readyz` reported `database: down`; the DatabaseDown alert;
  pods NotReady but not restarting.
- **Change:** restore the StatefulSet.
- **Verification:** pods Ready, the old link resolves, alerts resolved.

**Lesson recorded:** the error-budget alerts did *not* fire during this
outage, because the 503s came from Traefik and never reached the app's
metrics. That's why `ShortenerNoReadyPods` exists, and it's listed as a known
limitation (an edge SLI is the next step).

## 6. Using observability to find a real defect

The first redirect trace showed **two** `SELECT`s:

```
connect, SELECT, UPDATE, connect, SELECT
```

The second `SELECT` came from reading `link.target_url` after `commit()`,
because SQLAlchemy expires objects on commit. Capturing the value before the
commit removed one query per redirect (trace in §3). Similarly, the startup log
showed `SQLAlchemyInstrumentor ... sqlalchemy 2.1.5 falls outside of that range`,
which is why SQLAlchemy is pinned to 2.0.x.

## 7. Security controls verified

```
# Pod in another namespace → app (blocked by default-deny NetworkPolicy)
$ kubectl run np-test --image=curlimages/curl -- curl -m 4 http://url-shortener.shortener.svc/version
000  (timeout)

# Pod in another namespace → Postgres
$ kubectl run np-test2 --image=busybox -- nc -w 3 -z url-shortener-postgres-0.url-shortener-postgres.shortener.svc 5432
blocked

$ kubectl get ns shortener --show-labels
pod-security.kubernetes.io/enforce=restricted   # every pod in the namespace is admitted under "restricted"
```

## 8. Runtime state

```
HPA  url-shortener  cpu: 15%/70%  min 2  max 5  replicas 2
PDB  url-shortener  minAvailable 1  allowedDisruptions 1
PVC  data-url-shortener-postgres-0  Bound  1Gi  RWO  standard
```

## 9. Bugs the pipeline itself caught during development

- The migration Job failed on first deploy with `ModuleNotFoundError: No module named 'app'`,
  because the `alembic` CLI doesn't add the working directory to `sys.path`. The
  fix is `prepend_sys_path = .`, plus a regression test that runs the real CLI
  (`tests/test_migrations.py`). The test was confirmed to fail without the fix.
  On that *first* `helm install` the hook is `post-install`, so the app pods were
  already running and `/readyz` passed even though the table didn't exist yet.
  That's a gap in the plain-Helm path. Under Argo CD the sync waves (Postgres →
  migration → Deployment) stop the Deployment from being applied when the
  migration fails. On upgrades the hook is `pre-upgrade`, so the old pods keep
  serving if a migration fails.
