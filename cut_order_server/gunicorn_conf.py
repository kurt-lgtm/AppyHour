"""Gunicorn config for production.

App Platform sets $PORT (default 8080). Bind to 0.0.0.0 so the platform proxy can reach it.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"
timeout = 180  # LTF reads + Shopify pull can run long
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
