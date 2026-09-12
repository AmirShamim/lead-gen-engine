"""
Lead Generation Engine — Web Portal & Mobile Queue Server

Startup script that initializes the database, detects local Wi-Fi IP
for smartphone access, and launches the FastAPI application.

Usage:
    .venv/Scripts/python portal.py
    .venv/Scripts/python portal.py --port 8080
    .venv/Scripts/python portal.py --host 0.0.0.0
"""

import argparse
import os
import socket
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    QUEUE_HOST,
    QUEUE_PORT,
    PORTAL_AUTH_USERNAME,
    PORTAL_AUTH_PASSWORD,
    DATABASE_URL,
    DB_PATH,
)
from db import init_db
from stages.stage6_queue import app


def get_lan_ip() -> str:
    """Detect local LAN IP address for phone access on the same Wi-Fi."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't actually connect, just determines route
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description="Lead Gen Engine — Web Portal")
    parser.add_argument("--host", default=QUEUE_HOST, help=f"Host to bind (default: {QUEUE_HOST})")
    parser.add_argument("--port", type=int, default=QUEUE_PORT, help=f"Port to bind (default: {QUEUE_PORT})")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    # Initialize DB schema & backfill follower tiers
    print("\n[1/2] Initializing database engine...")
    init_db()

    lan_ip = get_lan_ip()
    db_type = "Azure PostgreSQL" if (DATABASE_URL and "postgres" in DATABASE_URL.lower()) else f"SQLite WAL ({DB_PATH})"

    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + "  ⚡ LEAD GENERATION ENGINE — AUTONOMOUS PORTAL".center(68) + "║")
    print("╠" + "═" * 68 + "╣")
    print(f"║  Database:       {db_type}".ljust(69) + "║")
    print(f"║  Desktop Portal: http://localhost:{args.port}".ljust(69) + "║")
    print(f"║  Mobile Phone:   http://{lan_ip}:{args.port}/queue".ljust(69) + "║")
    print(f"║  Login Username: {PORTAL_AUTH_USERNAME}".ljust(69) + "║")
    print(f"║  Login Password: {PORTAL_AUTH_PASSWORD}".ljust(69) + "║")
    print("╚" + "═" * 68 + "╝")
    print(f"\n  📱 To open on your phone: Ensure your phone is on the same Wi-Fi,")
    print(f"     then open Safari/Chrome and go to: http://{lan_ip}:{args.port}/queue\n")

    import uvicorn
    uvicorn.run("stages.stage6_queue:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
