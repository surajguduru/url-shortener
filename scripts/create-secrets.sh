#!/usr/bin/env bash
# Creates runtime secrets directly in the cluster with random values.
# Nothing secret is ever written to the repository. Idempotent: existing
# secrets are left untouched so re-running never rotates a live password.
set -euo pipefail

create_secret_if_missing() {
  local namespace=$1 name=$2; shift 2
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  if kubectl -n "$namespace" get secret "$name" >/dev/null 2>&1; then
    echo "secret $namespace/$name exists, leaving it"
  else
    kubectl -n "$namespace" create secret generic "$name" "$@" >/dev/null
    echo "secret $namespace/$name created"
  fi
}

random_password() { openssl rand -base64 24 | tr -d '/+=' ; }

create_secret_if_missing shortener url-shortener-db \
  --from-literal=username=shortener \
  --from-literal=password="$(random_password)"

create_secret_if_missing monitoring grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$(random_password)"
