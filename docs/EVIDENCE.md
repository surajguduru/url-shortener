# Verification evidence

Everything below came from this project's own cluster and repository. Commands
are included so each result can be reproduced.

- **Environment:** kind v0.33.0 (Kubernetes v1.37.0, 3 nodes), Docker 29.5.2,
  Helm 4.3.1, macOS (arm64).
- **Baseline:** `fd7d63e` (tag `baseline`). **Main PR:** #1 (`devops`); follow-ups #2–#4 (Argo CD target, promote fix, operational fixes + this evidence).

## 1. Source → CI → image → approval → Git → Argo CD → running pods

This is one traced release, carried out on 18 October 2026 with real runs:

| Step | Evidence |
|---|---|
| Source commit | `f46ac40` (merge of PR #3) on `main` |
| CI run (4/4 jobs green: tests, chart, security scan, build+image scan+push) | [actions/runs/39134320654](https://github.com/surajguduru/url-shortener/actions/runs/39134320654) |
| Image published | `ghcr.io/surajguduru/url-shortener:sha-f46ac40` (public, OCI revision label `f46ac40`) |
| Promote run: waited for the required reviewer on the `local` environment, then approved | [actions/runs/39134454760](https://github.com/surajguduru/url-shortener/actions/runs/39134454760) |
| Release commit written by promote | `3ad41e3 release(local): dev -> sha-f46ac40` |
| CI on the release commit | Correctly **skipped** (`paths-ignore: deploy/environments/**`); nothing new to build |
| Argo CD sync | `Synced / Healthy`, revision `3ad41e3`, "successfully synced (all tasks run)". Waves: Postgres → migration Job `Complete` → Deployment rolled |
| Running version | Deployment image `…:sha-f46ac40`; `X-App-Version: sha-f46ac40` |

```
$ make smoke
version: {"version":"sha-f46ac40","commit":"f46ac40"}
created: {"code":"uhfPVEl","target_url":"https://example.com/smoke-test","clicks":0,...}
redirect: /uhfPVEl -> https://example.com/smoke-test
clicks: 1
unknown code: 404
SMOKE TEST PASSED
```

Other green CI runs: [PR #1](https://github.com/surajguduru/url-shortener/actions/runs/39038103987),
[main after PR #1](https://github.com/surajguduru/url-shortener/actions/runs/39038225115),
[PR #3](https://github.com/surajguduru/url-shortener/actions/runs/39134121054).
PR builds are scanned but never pushed.

### Rollback through Git, under live traffic

Here `sha-f46ac40` was treated as a bad release and rolled back by promoting the
previous image, while a loop sent a redirect and a `/version` request every 100 ms:

| Time (UTC) | Event |
|---|---|
| 14:37:43 | Rollback promote approved and completed: [actions/runs/39135507475](https://github.com/surajguduru/url-shortener/actions/runs/39135507475), commit `daa7c35 release(local): sha-f46ac40 -> sha-ebfc4eb` |
| 14:38:14 | Argo CD applied `…:sha-ebfc4eb` |
| 14:38:43 | Rollout complete: **about 60 s from approval to fully rolled back** |

```
responses during rollback (status, serving version):
 6515 307 sha-f46ac40
  146 307 sha-ebfc4eb
```

That's **6,661 / 6,661 requests successful**, and the serving version switches
partway through. Afterwards it was rolled forward again with the same mechanism
([actions/runs/39136945528](https://github.com/surajguduru/url-shortener/actions/runs/39136945528)).
Every release and rollback is a Git commit naming who requested it and which
run did it.

### Drift detection and self-heal

```
14:19:34 manual change: kubectl patch configmap url-shortener-config LOG_LEVEL=DEBUG
14:19:36 LOG_LEVEL is back to: INFO          ← Argo CD reverted it to what Git says (~2 s)
```

### Promote safety: a race found and fixed

The first promote run ([39129440519](https://github.com/surajguduru/url-shortener/actions/runs/39129440519))
was dispatched while `main` was at `ebfc4eb`, and approved after PR #2 had moved
`main`. Its push was rejected:

```
[main 12f195b] release(local): dev -> sha-ebfc4eb
 ! [rejected]        main -> main (fetch first)
```

It failed safely, so nothing half-applied. PR #3 fixed it: the workflow now
checks out the branch tip *after* approval and rebases before pushing.

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

## 9. Bugs found while operating the system

- **Argo CD stuck "Progressing" on the Ingress.** Argo CD only calls an Ingress
  healthy once the controller writes an address into `status`. Traefik behind a
  NodePort wrote none, so the sync waiting on wave 0 never reached the migration
  or the Deployment. The fix is in `deploy/platform/traefik-values.yaml`: publish
  `127.0.0.1`, which is the address it really serves on.
- **"Availability" showed "No data" when healthy.** With zero 5xx responses, the
  error-ratio numerator has no series, so the ratio was empty instead of 0. The
  fix is `(… or vector(0))` in the recording rules. The alerts weren't affected
  (an empty result never fires), but the dashboard was misleading exactly when
  things were fine.
- **A transient `Degraded`** right after a rollout: the HPA showed
  `cpu: <unknown>` until metrics-server had a sample for the new pods (about
  1 minute), then returned to Healthy. It's expected behaviour, not a fault.

## 10. Bugs the pipeline itself caught during development

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
