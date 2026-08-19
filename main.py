import argparse
import asyncio
import ctypes
import os
import sys

from app.agent.manus import Manus
from app.logger import logger


def configure_utf8_console() -> None:
    """Keep Chinese file names and Revit messages readable in Windows terminals."""
    if os.name == "nt" and sys.stdout.isatty():
        # Python stream encoding alone is insufficient when the Windows console
        # itself still uses GBK: UTF-8 output is then rendered as mojibake.
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        if sys.stdin.isatty():
            kernel32.SetConsoleCP(65001)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run Manus agent with a prompt")
    parser.add_argument(
        "--prompt", type=str, required=False, help="Input prompt for the agent"
    )
    args = parser.parse_args()

    # Create and initialize Manus agent
    agent = await Manus.create()
    try:
        # Single-turn mode when --prompt is provided.
        if args.prompt:
            prompt = args.prompt.strip()
            if not prompt:
                logger.warning("Empty prompt provided.")
                return

            logger.warning("Processing your request...")
            await agent.run(prompt)
            logger.info("Request processing completed.")
            return

        # Interactive multi-turn loop.
        while True:
            prompt = input("Enter your prompt (type 'exit' to quit): ").strip()
            if not prompt:
                logger.warning("Empty prompt provided.")
                continue
            if prompt.lower() in {"exit", "quit"}:
                logger.info("Exiting interactive session.")
                break

            logger.warning("Processing your request...")
            await agent.run(prompt)
            logger.info("Request processing completed.")

    except KeyboardInterrupt:
        logger.warning("Operation interrupted.")
    finally:
        # Ensure agent resources are cleaned up before exiting
        await agent.cleanup()


if __name__ == "__main__":
    configure_utf8_console()
    asyncio.run(main())
