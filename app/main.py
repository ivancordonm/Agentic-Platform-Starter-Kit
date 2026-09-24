"""Uvicorn entry point."""

from dotenv import load_dotenv

from app.api.routes import create_app

load_dotenv()
app = create_app()
