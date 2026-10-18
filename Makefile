CLUSTER   := shortener
NAMESPACE := shortener
RELEASE   := url-shortener
IMAGE     := ghcr.io/surajguduru/url-shortener
GIT_SHA   := $(shell git rev-parse --short HEAD)
DEV_TAG   := dev-$(GIT_SHA)
VALUES    := deploy/environments/local/values.yaml

.PHONY: help venv test lint image scan platform deploy-local gitops status urls smoke grafana-password argocd-password clean

help:            ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv:            ## Create virtualenv with dev dependencies
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

test:            ## Run unit tests
	.venv/bin/pytest -q

lint:            ## Lint Python, Helm chart and rendered manifests
	.venv/bin/ruff check . && .venv/bin/ruff format --check .
	helm lint charts/url-shortener -f $(VALUES)
	helm template $(RELEASE) charts/url-shortener -n $(NAMESPACE) -f $(VALUES) | kubeconform -strict -summary -ignore-missing-schemas

image:           ## Build the image locally, tagged with the git commit
	docker build -t $(IMAGE):$(DEV_TAG) --build-arg APP_VERSION=$(DEV_TAG) --build-arg GIT_COMMIT=$(GIT_SHA) .

scan: image      ## Scan the local image for fixable HIGH/CRITICAL CVEs
	trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(IMAGE):$(DEV_TAG)

platform:        ## Create the kind cluster and install platform components
	scripts/bootstrap-platform.sh

deploy-local: image  ## Dev loop: build, load into kind, helm upgrade (bypasses GitOps)
	kind load docker-image $(IMAGE):$(DEV_TAG) --name $(CLUSTER)
	helm upgrade --install $(RELEASE) charts/url-shortener -n $(NAMESPACE) \
	  -f $(VALUES) --set image.tag=$(DEV_TAG) --wait --timeout 5m

gitops:          ## Register the app with Argo CD (deploys the tag committed in $(VALUES))
	kubectl apply -f deploy/argocd/application.yaml

status:          ## Show what is running and which version
	kubectl -n $(NAMESPACE) get deploy,sts,pods,hpa,pdb,ingress -o wide
	@echo; kubectl -n $(NAMESPACE) get deploy $(RELEASE) -o jsonpath='image: {.spec.template.spec.containers[0].image}{"\n"}'

smoke:           ## End-to-end user journey through the ingress
	scripts/smoke-test.sh http://shortener.localhost:8080

urls:            ## Print local URLs
	@echo "App:          http://shortener.localhost:8080"
	@echo "Grafana:      http://grafana.localhost:8080   (user admin, make grafana-password)"
	@echo "Prometheus:   http://prometheus.localhost:8080"
	@echo "Alertmanager: http://alertmanager.localhost:8080"
	@echo "Jaeger:       http://jaeger.localhost:8080"
	@echo "Argo CD:      http://argocd.localhost:8080    (user admin, make argocd-password)"

grafana-password:
	@kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo

argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo

clean:           ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)
