# app/config.py
# Loads environment variables.  All secrets come from the environment —
# never hardcoded here.  .env.example lists the required keys.

import os
from dotenv import load_dotenv

# Load .env from the project root (one level above this file's package).
# This must run before any os.environ.get() call below.
load_dotenv()


class Config:
    # Flask core
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    # Database — Neon PostgreSQL connection string
    # Format: postgresql://user:password@host/dbname?sslmode=require
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Neon (like most serverless/managed Postgres) closes idle connections
    # from its side without warning — a connection that's been sitting in
    # the pool for a while comes back as "SSL connection has been closed
    # unexpectedly" on the next request instead of reconnecting. pool_pre_ping
    # tests a connection with a cheap SELECT 1 before handing it to a
    # request and transparently replaces it if it's dead; pool_recycle
    # forces a refresh before Neon's own idle timeout would ever hit it.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    # AI providers
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")

    COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")

    ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

    # Twilio WhatsApp citizen intake (app/whatsapp/routes.py). Optional —
    # without these the webhook still runs but cannot verify a request
    # actually came from Twilio (see _validate_twilio_signature()); use the
    # /whatsapp/test-send adapter (WHATSAPP_TEST_ADAPTER=1) to exercise the
    # real intake pipeline locally without any Twilio credentials.
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "")
