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

    # AI providers
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3-27b")

    COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")

    ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
