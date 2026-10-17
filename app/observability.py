"""Logging, metrics and tracing setup.

Logs are JSON on stdout (collected by the container runtime) and carry the
active trace id so a log line can be joined to its trace in Jaeger.
Metrics are served in Prometheus format on a separate port (METRICS_PORT)
that the Ingress never routes to, so they are not public. Traces are exported via
OTLP only when OTEL_EXPORTER_OTLP_ENDPOINT is set, so local runs and tests
need no collector.
"""

import logging
import os
import sys
import time

from fastapi import FastAPI, Request
from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from pythonjsonlogger.json import JsonFormatter

from app.config import settings

REQUESTS = Counter("http_requests_total", "HTTP requests handled", ["method", "route", "status"])
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
LINKS_CREATED = Counter("shortener_links_created_total", "Short links created")
REDIRECTS = Counter("shortener_redirects_total", "Redirect lookups", ["result"])
BUILD_INFO = Gauge("shortener_build_info", "Running build", ["version", "commit"])

# Probes would otherwise dominate request metrics and logs.
UNTRACKED_ROUTES = {"/healthz", "/readyz"}

access_logger = logging.getLogger("shortener.access")


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        record.trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else None
        record.span_id = format(span_context.span_id, "016x") if span_context.is_valid else None
        record.version = settings.app_version
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "{asctime}{levelname}{name}{message}",
            style="{",
            rename_fields={"asctime": "time", "levelname": "level", "name": "logger"},
        )
    )
    handler.addFilter(TraceContextFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    # Route uvicorn's own logs through the JSON handler; our middleware writes a
    # structured access log, so uvicorn's plain-text one is switched off.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def configure_tracing(app: FastAPI, engine) -> None:
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {"service.name": "url-shortener", "service.version": settings.app_version}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    SQLAlchemyInstrumentor().instrument(engine=engine)


def install_request_middleware(app: FastAPI) -> None:
    BUILD_INFO.labels(settings.app_version, settings.git_commit).set(1)

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-App-Version"] = settings.app_version
            return response
        finally:
            route = request.scope.get("route")
            # Use the route template, not the raw path, to keep label cardinality bounded.
            route_path = route.path if route else "unmatched"
            if route_path not in UNTRACKED_ROUTES:
                elapsed = time.perf_counter() - start
                REQUESTS.labels(request.method, route_path, str(status)).inc()
                REQUEST_LATENCY.labels(request.method, route_path).observe(elapsed)
                access_logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "route": route_path,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )


def start_metrics_server() -> None:
    if settings.metrics_port:
        start_http_server(settings.metrics_port)
