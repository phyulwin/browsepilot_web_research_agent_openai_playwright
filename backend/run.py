# backend/run.py — single-worker startup with Windows subprocess-capable asyncio.
import argparse
import asyncio
import sys

import uvicorn


def main():
    # Keep reload and worker supervisors out of this in-process research service.
    parser = argparse.ArgumentParser(description="Start BrowsePilot without reload or multiple workers")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = uvicorn.Config("backend.main:app", host=args.host, port=args.port, workers=1, reload=False)
    if sys.platform == "win32":
        # Own the top-level loop before loading FastAPI; Server.serve does not replace it.
        # A factory also avoids relying on deprecated global Windows loop policies.
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            runner.run(uvicorn.Server(config).serve())
    else:
        # Preserve Uvicorn's normal loop selection on Linux and other platforms.
        uvicorn.Server(config).run()


if __name__ == "__main__":
    # Handle an ordinary terminal interrupt without changing request-time event loops.
    try:
        main()
    except KeyboardInterrupt:
        pass
