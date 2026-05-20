"""WSGI entry point for gunicorn."""
from app import create_app

application = create_app()
