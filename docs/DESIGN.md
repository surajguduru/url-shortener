# Design decisions

Each section covers what was chosen, why it fits this app, the alternative
that was considered, and when that alternative would be the better choice.

## 1. Architecture and environment

- **One stateless service plus one database.** The app has a single job, so
  splitting it into microservices would add network hops and failure modes
  without adding value. The failure boundary that matters is app ↔ database,
  and every probe, alert and NetworkPolicy is built around it.
- **kind with 3 nodes** (1 control plane, 2 workers), not Minikube or a single
  node. With several nodes, the topology spread, PDB and node-drain behaviour
  can actually be observed. It's free and reproducible from one config file.
  *Different choice:* a managed cluster (EKS/GKE) with Terraform once the system
  needs real users, multiple AZs or managed Postgres.
- **Networking:** the only published port is a NodePort mapped by kind to
  `127.0.0.1:8080`, so nothing is exposed on the LAN. The `*.localhost` host
  names avoid editing `/etc/hosts`.
- **Ingress controller: Traefik.** ingress-nginx was retired in 2026, and
  Traefik supports the standard `Ingress` API, so the chart stays portable.
  *Next step:* the Gateway API (HTTPRoute), which also allows weighted canaries.

## 2. Kubernetes workloads

| Choice | Why | Alternative / when |
|---|---|---|
| App as a **Deployment** | Stateless; any pod can serve any request | – |
| Postgres as a **StatefulSet + volumeClaimTemplate** | Stable identity and a PVC that follows the pod; data survives restarts (proven in the fault drill) | Managed DB (RDS/Cloud SQL) or an operator (CloudNativePG) for HA, backups and PITR. Set `postgres.enabled=false` |
| **startupProbe** `/healthz` | Protects slow cold starts without slowing liveness | – |
| **livenessProbe** `/healthz` does *not* touch the DB | A DB outage shouldn't restart every app pod (restart storm, more DB load when it comes back) | – |
| **readinessProbe** `/readyz` checks the DB | Pods that can't serve are taken out of the Service; recovery is automatic | – |
| `maxSurge 1 / maxUnavailable 0`, `minReadySeconds 5`, preStop `sleep 5` | Zero-downtime rollouts: an old pod is only removed after a new one is Ready, and endpoints are updated before SIGTERM. Measured: 960/960 requests OK during a rolling restart | – |
| **HPA** on CPU, 2–5 replicas | The workload is CPU-bound request handling. A minimum of 2 keeps a pod on each worker | Scale on request rate via prometheus-adapter or KEDA when CPU stops tracking load |
| **PDB** `minAvailable: 1` | A node drain can't take out both replicas | – |
| CPU **request but no limit**, memory request and limit | CPU limits cause throttling and higher latency even when the node is idle. A memory limit stops a leak from hurting neighbours | Add CPU limits on a shared, multi-tenant cluster |
| One uvicorn worker per pod | Kubernetes scales by adding pods, and the Prometheus client stays correct without multiprocess mode | – |
| Migrations as a **Job**, not at app start | Several replicas starting at once would race on DDL. A failed migration stops the release *before* new code runs | – |

## 3. Deployment configuration

- **One Helm chart** with production-leaning defaults, plus **one small values
  file per environment** (`deploy/environments/<env>/values.yaml`) containing
  only the differences. There's no copy-pasted YAML per environment to drift
  apart. *Alternative:* Kustomize overlays. They're fine too, but Helm's
  templating and helpers keep the app and the migration Job consistent (shared
  env, security context).
- **Platform components** are pinned upstream charts with checked-in values
  files and one idempotent bootstrap script. Re-running it converges, and
  versions only change through a commit.
- `image.tag` in the environment values file is **the release pointer**. It's
  the one line that changes per release.
- A config checksum annotation rolls the pods when the ConfigMap changes.

## 4. CI/CD

- **CI on GitHub Actions** (lint → tests → chart validation → secret and IaC
  scan → build → image scan → push). The image is **built once** and tagged
  `sha-<commit>`, so the tag is immutable and traceable to source. The same
  artefact is promoted, never rebuilt.
- **Pull-based CD with Argo CD.** GitHub-hosted runners can't reach a cluster
  on a laptop. The push-based options were a self-hosted runner with cluster
  credentials, or exposing the API server. Argo CD inside the cluster pulls from
  Git instead, so no cluster credentials leave the cluster. It also gives drift
  detection and self-heal for free.
- **Where the approval sits:** CI and image publishing are fully automatic,
  because they're safe and reversible. *Promotion* to the running environment
  is a manual `workflow_dispatch` that needs a required reviewer on the `local`
  GitHub Environment, so a person decides what reaches users. *Alternative:*
  auto-promote every green `main` build (continuous deployment), once there are
  automated post-deploy checks to trust.
- **Sync ordering:** Argo CD sync waves: Postgres (0) → migration Job hook (1) →
  Deployment (2). For plain Helm the same Job is a `post-install,pre-upgrade` hook.

## 5. Release control

- **Strategy: rolling update.** There's one service, migrations are kept
  backward compatible, and both versions can safely serve at the same time for
  the ~30 s a rollout takes. *Blue-green* would double the resources and need
  a traffic switch. *Canary* (Argo Rollouts + Gateway API weights, analysed
  with the SLO queries already defined here) becomes worth it when there's
  enough traffic for a 5% slice to be statistically meaningful.
- **Recovering from a bad release:** promote the previous tag, or `git revert`
  the release commit. Argo CD rolls back with the same zero-downtime mechanics,
  and Git keeps the audit trail. The fast-burn alert's first step is "roll back
  first".
- **Traceability:** commit → `sha-xxxxxxx` image (OCI labels hold the revision)
  → `release(local): old -> new` commit naming the requester and the workflow
  run → `app.kubernetes.io/version` label → `/version`, `X-App-Version`, every
  log line, every trace, and the `shortener_build_info` metric.
- **Drift:** Argo CD shows OutOfSync for any manual `kubectl` change and
  self-heals it back to Git.

## 6. Observability and reliability

- **Metrics (Prometheus):** RED metrics per route *template*. `/{code}` is
  used, not the raw path, so label cardinality stays bounded. There are also
  business counters (links created, redirect hit/miss) and a build-info gauge.
  Metrics are served on **a separate port, 9000**, that the Ingress never routes
  to, so they aren't public.
- **SLOs:** 99.5% non-5xx and 95% of redirects under 250 ms. The alerts use
  multi-window burn rates (14.4× over 1 h and 5 m to page, 6× over 6 h and 30 m
  for a ticket), so they fire fast on real incidents and stay quiet on blips.
  Each alert links to a runbook entry with a concrete action and a recovery check.
- **Logs:** JSON on stdout with `trace_id`, `span_id` and `version`. User URLs
  are deliberately not logged. *Next step:* Loki for retention and search.
- **Traces:** OpenTelemetry for FastAPI and SQLAlchemy, exported over OTLP to
  Jaeger. Tracing is switched on by an environment variable, so tests and local
  runs need no collector. Tracing found a real inefficiency: an extra `SELECT`
  on every redirect, caused by reading an expired ORM object after commit. It's fixed.
- **A known gap:** an SLI measured inside the app can't see requests that never
  reach a pod. `ShortenerNoReadyPods` covers that, and the proper fix is an
  edge SLI from Traefik metrics.

## 7. Security

| Layer | Control |
|---|---|
| Repo | No credentials committed; Trivy secret scan in CI; `.env` ignored |
| Supply chain | Every GitHub Action pinned to a full commit SHA; image built once and promoted by its commit-SHA tag (never rebuilt); promote refuses tags that CI didn't build. *Next step:* deploy by digest and sign images (cosign) so tags cannot be repointed |
| CI permissions | `contents: read` by default; `packages: write` only on the image job; `contents: write` only on promote; PR builds never push |
| Image | Multi-stage build (no compilers in runtime), non-root UID 10001, Trivy fails on fixable HIGH/CRITICAL |
| Pods | Namespace enforces Pod Security **restricted**; `readOnlyRootFilesystem`, all capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`; no service-account token mounted |
| Secrets | Created in-cluster with random values by a script and referenced by name; never in values files or Git. *Production:* External Secrets + Vault/cloud KMS, etcd encryption at rest |
| Network | Default-deny NetworkPolicy; only Traefik → app:8000, Prometheus → app:9000, app/migrations → Postgres:5432, app → Jaeger:4317, DNS. Verified: a pod in another namespace can reach neither the app nor Postgres |
| Exposure | Only the ingress is published, on 127.0.0.1; Postgres and metrics are cluster-internal |
