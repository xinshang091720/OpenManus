"""Launch the local capability API consumed by the business PC application."""

import argparse

import uvicorn

from app.api.capabilities import app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the OpenManus capability API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8001, type=int)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
