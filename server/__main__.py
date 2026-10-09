"""
Run the web app: python -m server [--host 127.0.0.1] [--port 8000] [--reload]

HOST and PORT environment variables (set by Railway and similar hosts) are used
as defaults.
"""

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="TravelBench Planner web app")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development)")
    args = parser.parse_args()
    uvicorn.run("server.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
