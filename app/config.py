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

    # Session / cookie security (Phase 0 §8).
    # SESSION_COOKIE_SECURE is off in dev (no HTTPS on localhost) and must be
    # forced on in production — set FORCE_HTTPS_COOKIES=true on Render.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FORCE_HTTPS_COOKIES", "false").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Rate limiting (Flask-Limiter). In-memory storage for a single instance —
    # move to a Redis storage_uri when Phase 1's job queue introduces Redis
    # anyway; this is a one-line change, not a rewrite.
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # AI providers
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.6-27b")

    COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")

    ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

    # Email (password reset, notifications). Missing SMTP_HOST means
    # app/services/email_client.py raises EmailNotConfiguredError rather than
    # pretending delivery succeeded — see that module and .env.example.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
