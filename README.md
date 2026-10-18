# URL Shortener: Advanced DevOps final project

A small URL shortener (FastAPI + PostgreSQL), taken from a bare baseline to a
system that is built, scanned, released through GitOps, run on Kubernetes, and
observable with metrics, logs and traces.

- **Baseline commit:** `fd7d63e` (tag `baseline`): the app before any DevOps work.
- **All improvements:** PR #1 (`devops` → `main`). Small follow-ups after running
  the real release flow: #2 (Argo CD tracks `main`), #3 (promote race fix),
  #4 (Ingress health and SLO rule fixes, evidence). Release commits
  `release(local): …` on `main` are written by the promote workflow.
- Design decisions and trade-offs: [docs/DESIGN.md](docs/DESIGN.md)
- Alert response and recovery checks: [docs/RUNBOOK.md](docs/RUNBOOK.md)
- Verification evidence: [docs/EVIDENCE.md](docs/EVIDENCE.md)

## Architecture

```mermaid
flowchart LR
  user([Browser / curl]) -->|shortener.localhost:8080| traefik[Traefik ingress<br/>ns: traefik]
  subgraph ns_shortener [ns: shortener · PSS restricted · default-deny NetworkPolicy]
    svc[Service url-shortener] --> app1[app pod]
    svc --> app2[app pod]
    app1 & app2 -->|5432| pg[(Postgres StatefulSet<br/>+ PVC)]
    job[migration Job<br/>alembic upgrade head] -->|5432| pg
  end
  traefik -->|:8000| svc
  prom[Prometheus<br/>ns: monitoring] -->|scrape :9000| app1 & app2
  app1 & app2 -->|OTLP :4317| jaeger[Jaeger<br/>ns: observability]
  prom --> am[Alertmanager]
  grafana[Grafana] --> prom & jaeger

  dev([git push]) --> gha[GitHub Actions CI<br/>test · lint · scan · build]
  gha -->|sha-xxxxxxx| ghcr[(GHCR image)]
  promote[promote workflow<br/>manual approval] -->|commit image.tag| git[(Git repo)]
  argocd[Argo CD<br/>ns: argocd] -->|pull| git
  argocd -->|helm render + sync| ns_shortener
  ghcr -.->|image pull| app1
```

**Request path:** client → Traefik (NodePort mapped to `127.0.0.1:8080`) →
`Service` → one of the Ready app pods → Postgres. A redirect is one `SELECT`
plus one `UPDATE` of the click counter.

**Commit to running pod:**
1. Push to `devops`/`main` → **CI** runs ruff, pytest (including a real
   `alembic upgrade`), helm lint, kubeconform, Trivy secret/IaC scan → builds the
   image → Trivy scans it (fails on fixable HIGH/CRITICAL) → pushes
   `ghcr.io/surajguduru/url-shortener:sha-<commit>`.
2. **Promote** (manual `workflow_dispatch`, gated by the `local` GitHub
   Environment's required reviewer) checks that the image exists and commits the
   new `image.tag` to `deploy/environments/local/values.yaml`.
3. **Argo CD** in the cluster sees the commit and syncs in waves: Postgres
   (wave 0) → migration Job (wave 1) → Deployment rolling update (wave 2).
4. The running version is visible at `/version`, in the `X-App-Version` header,
   the `app.kubernetes.io/version` label, every log line, every trace, and the
   Grafana "Running versions" panel.

## What changed from the baseline

| Area | Baseline | Now |
|---|---|---|
| Packaging | none | Multi-stage Dockerfile, non-root UID 10001, version baked in |
| Runtime | `uvicorn` by hand | kind cluster; Deployment + StatefulSet/PVC, probes, HPA, PDB, spread |
| Config | one env var with hard-coded password | Helm chart + per-env values; ConfigMap + Secret; no credentials in Git |
| Schema | `create_all` at startup | Alembic migrations run by a Job before the rollout |
| CI | none | GitHub Actions: lint, tests, chart validation, Trivy (repo + image), GHCR push |
| CD / release | none | Manual-approval promote → Git → Argo CD; rolling update; rollback by revert |
| Health | none | `/healthz` (liveness, no DB) and `/readyz` (DB check) |
| Metrics | none | Prometheus metrics on internal port 9000, SLO recording and alert rules, Grafana dashboard |
| Logs | `print` | JSON logs with `trace_id`, `span_id` and `version` |
| Tracing | none | OpenTelemetry → Jaeger (HTTP and SQL spans) |
| Security | none | PSS `restricted`, default-deny NetworkPolicies, read-only root FS, dropped caps, no SA token, SHA-pinned actions, scans |

## Run it locally

### Prerequisites

Docker (≈6 GB memory for the full stack), `kind` ≥ 0.24, `kubectl`, `helm` ≥ 3.14,
Python 3.12+. Optional: `trivy`, `kubeconform`.
On macOS: `brew install kind helm kubeconform trivy`.

### 1. Platform (one time, about 5–8 minutes)

```bash
make platform
```

This creates a 3-node kind cluster and installs Traefik, metrics-server,
kube-prometheus-stack (Prometheus, Alertmanager, Grafana), Jaeger and Argo CD.
It also generates random database and Grafana passwords **directly in the
cluster** (`scripts/create-secrets.sh`); nothing secret is written to disk or Git.

### 2a. Deploy through GitOps (the release path)

```bash
make gitops     # registers the Argo CD Application (tracks the branch in deploy/argocd/application.yaml)
```

Argo CD deploys whatever `image.tag` is committed in
`deploy/environments/local/values.yaml`. To release a new build, run the
**promote** workflow in GitHub Actions with the `sha-…` tag that CI printed.

GHCR packages must be **public** (Package settings → Change visibility) for
kind to pull them without an image pull secret.

### 2b. Deploy for development (bypasses CI and Argo CD)

```bash
make venv test
make deploy-local   # builds the image, loads it into kind, helm upgrade --install
```

If Argo CD is also managing the app, it reverts manual changes (self-heal), so
use one path or the other.

### 3. Use it

```bash
make smoke   # end-to-end user journey through the ingress
make urls    # where everything lives
make status  # pods, HPA, PDB, running image
```

| What | URL | Login |
|---|---|---|
| App | http://shortener.localhost:8080 | – |
| Grafana (dashboard "URL Shortener") | http://grafana.localhost:8080 | `admin` / `make grafana-password` |
| Prometheus | http://prometheus.localhost:8080 | – |
| Alertmanager | http://alertmanager.localhost:8080 | – |
| Jaeger | http://jaeger.localhost:8080 | – |
| Argo CD | http://argocd.localhost:8080 | `admin` / `make argocd-password` |

`*.localhost` resolves to 127.0.0.1 in browsers and curl. Ports are bound to
127.0.0.1 only.

### Rollback

- **Preferred:** run **promote** with the previous tag, or `git revert` the
  `release(local): …` commit. Argo CD syncs the old version and Git history
  records who rolled back, and why.
- **Emergency:** Argo CD UI → History and Rollback (this disables auto-sync until
  Git is fixed, which shows up as drift).

Migrations must stay backward compatible with the previous release
(expand/contract), so the old code still works against the new schema.

### Running the app without Kubernetes

```bash
export DATABASE_URL=postgresql+psycopg://shortener:shortener@localhost:5432/shortener
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8000
```

## Known limitations

- A single local environment. The chart supports more (one values file per
  environment), but only `local` exists.
- Postgres is a single replica with no backups or HA. In production it would be
  a managed database (`postgres.enabled=false`, `database.externalHost`).
- Secrets are plain Kubernetes Secrets created by a script. In production they
  would come from External Secrets/Vault or Sealed Secrets, with etcd encryption.
- No TLS: everything is HTTP on 127.0.0.1. In production cert-manager would
  issue certificates for the ingress.
- Jaeger keeps traces in memory, and logs are read with `kubectl logs` (no
  Loki), so both are lost when their pods restart.
- The availability SLI is measured inside the app, so it can't see requests
  that never reach a pod (all pods NotReady → Traefik returns 503).
  `ShortenerNoReadyPods` covers that gap; an edge SLI from Traefik metrics would
  be the next step.
- Alertmanager has no receiver configured (no email or Slack). Alerts show in the
  Alertmanager and Prometheus UIs.
- GitHub can't reach a laptop cluster, which is why CD is pull-based (Argo CD).
  For the same reason there's no Git webhook, so Argo CD notices a release on
  its 3-minute poll. Use `argocd app sync` or a refresh to apply it sooner.
