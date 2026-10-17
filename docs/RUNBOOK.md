# Runbook

## What "healthy" means

| Signal | Healthy | Where to look |
|---|---|---|
| Availability SLI: share of requests that are not 5xx | ≥ 99.5% over 30 days | Grafana "Availability", `shortener:error_ratio:rate1h` |
| Latency SLI: share of redirects under 250 ms | ≥ 95% | Grafana "Redirects < 250ms", `shortener:redirect_fast_ratio:rate10m` |
| Ready app pods | ≥ 2 (HPA minimum) | `kubectl -n shortener get deploy`, Grafana "Ready app pods" |
| Database | 1/1 ready | `kubectl -n shortener get sts` |
| Running version | the tag committed in `deploy/environments/local/values.yaml` | `/version`, Grafana "Running versions", Argo CD "Synced" |

4xx responses (unknown short code, invalid URL) are user errors and don't count
against the SLO.

**Verifying recovery (after any incident):**
1. `make smoke` passes (create → redirect → click counted → 404 for unknown).
2. `curl http://shortener.localhost:8080/readyz` returns `{"status":"ok","database":"up"}`.
3. The alert has resolved in Alertmanager.
4. On the Grafana dashboard, the error ratio is back at 0 and the latency SLI is back above 95%.
5. Argo CD shows the app as Synced and Healthy with the expected version.

---

## ShortenerErrorBudgetFastBurn

**Meaning:** the 5xx ratio is above 7.2% over both 1 h and 5 m. At this rate the
30-day error budget is gone in about 2 days. **Impact:** users are getting errors now.

1. Check whether it started with a release: Argo CD History, or Grafana "Running
   versions". If the timing matches a new version → **roll back first, then
   investigate** (promote the previous tag, or `git revert` the release commit).
2. Find the failing route: Grafana "Requests per second by route and status".
3. Get the details:
   `kubectl -n shortener logs -l app.kubernetes.io/name=url-shortener | grep '"status": 5'`.
   Copy a `trace_id` and open it in Jaeger to see which span failed (an HTTP span
   vs a SQL span).
4. If the SQL spans are the ones failing, continue with *ShortenerDatabaseDown*.

## ShortenerErrorBudgetSlowBurn

**Meaning:** the 5xx ratio is above 3% over 6 h. The budget would be gone in about 5 days.
It's not urgent: follow the same diagnosis as the fast burn during working hours,
and look for one route or one pod that is consistently failing.

## ShortenerRedirectLatencyHigh

**Meaning:** fewer than 95% of redirects completed in under 250 ms over the last 10 minutes.

1. Grafana "Latency by route": is it only redirects, or every route?
2. Open a slow redirect trace in Jaeger. If most of the time is in a
   `SELECT`/`UPDATE` span → database. If it's in the app span → CPU.
3. CPU: check Grafana "App pods CPU" and `kubectl -n shortener get hpa`. If the
   HPA is already at `maxReplicas`, raise it in the values file and release.
4. Database: `kubectl -n shortener top pod`, and check Postgres logs for lock
   waits. A missing index shows up as a slow `SELECT` span.

## ShortenerNoReadyPods

**Meaning:** no app pod is Ready, so Traefik returns 503 for every request.
**Impact:** full outage. These 503s never reach the app, so the error-ratio
alerts *don't* fire. This alert is what covers that case.

1. `kubectl -n shortener get pods` and `kubectl -n shortener describe pod <pod>`.
2. `kubectl -n shortener exec deploy/url-shortener -- python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/readyz').read())"`.
   If the database is `down`, go to *ShortenerDatabaseDown*. The pods are not
   restarted in that case, by design: liveness doesn't depend on the DB.
3. CrashLoopBackOff/ImagePullBackOff after a release → roll back.

## ShortenerDatabaseDown

**Meaning:** the Postgres StatefulSet has no ready replica. App pods go NotReady
within about 10 s (`/readyz` fails twice, 5 s apart).

1. `kubectl -n shortener get sts,pod,pvc -l app.kubernetes.io/name=postgres`
2. `kubectl -n shortener logs url-shortener-postgres-0 --previous`
3. Typical causes: the pod was evicted or OOMKilled (raise the memory limit),
   the PVC is full, or someone scaled it to 0.
4. Once Postgres is Ready, the app pods become Ready again by themselves. No
   restart is needed, because of the connection pool's `pool_pre_ping`.
   **Verify:** follow the recovery checklist above.

The data is on the PVC `data-url-shortener-postgres-0` and survives pod
deletion. Deleting the PVC deletes the data.

## ShortenerMigrationFailed

**Meaning:** the Alembic Job failed, so Argo CD stops the sync before the
Deployment is updated. The old version keeps serving traffic.

1. `kubectl -n shortener logs job/url-shortener-migrate -c migrate`
   (and `-c wait-for-db` if it never got that far).
2. Fix the migration in a new commit and release again. Never edit a migration
   that has already been applied.

---

## Fault drill (rehearsed, see EVIDENCE.md)

| Step | Command | Expected |
|---|---|---|
| Inject | `kubectl -n shortener scale sts/url-shortener-postgres --replicas=0` | App pods go `0/1` within 10 s with 0 restarts; users get 503 |
| Detect | wait about 2 min | `ShortenerDatabaseDown` and `ShortenerNoReadyPods` fire |
| Recover | `kubectl -n shortener scale sts/url-shortener-postgres --replicas=1` | Pods Ready again within about 10 s |
| Verify | `make smoke` plus a link created before the fault | 307 to the original URL (data persisted on the PVC); alerts resolve |
