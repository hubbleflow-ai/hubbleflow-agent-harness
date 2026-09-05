"""Console entry point: `hubbleflow`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from hubbleflow.config import Config

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hubbleflow",
        description="A DeepAgents coding harness running on Gemini.",
    )
    parser.add_argument("prompt", nargs="*", help="run this prompt immediately instead of waiting for one")
    parser.add_argument("-m", "--model", metavar="ID", help="model to use, a Gemini id or a local Ollama tag (see /models)")
    parser.add_argument("-C", "--cwd", metavar="DIR", type=Path, help="workspace to operate in (default: the current directory)")
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true", help="resume this workspace's last session")
    parser.add_argument("-p", "--print", action="store_true", help="run the prompt, print the answer, and exit")
    parser.add_argument("--yolo", action="store_true", help="skip every approval prompt (it can write and run anything)")
    parser.add_argument(
        "--cache",
        nargs="?",
        const="1",
        metavar="0|1",
        help="cache the conversation prefix on Gemini's servers, cheaper input, small hourly storage cost (default: off)",
    )
    parser.add_argument(
        "--purge-caches",
        action="store_true",
        help="delete every context cache this tool has created, then exit, storage bills per hour, so this is the panic button",
    )
    parser.add_argument("-V", "--version", action="version", version=f"hubbleflow {__version__}")
    return parser


def _purge_caches() -> int:
    """Release every cache we hold, and report anything else still live."""
    from hubbleflow.cache import DISPLAY_NAME
    from hubbleflow.config import Config, api_key

    Config.load()  # resolves the API key from .env the same way a session does
    if not api_key():
        print("hubbleflow: no GOOGLE_API_KEY, so there is nothing to purge", file=sys.stderr)
        return 1

    try:
        from google import genai

        client = genai.Client(api_key=api_key())
        ours, theirs = 0, 0
        for entry in client.caches.list():
            if getattr(entry, "display_name", None) == DISPLAY_NAME:
                client.caches.delete(name=entry.name)
                ours += 1
            else:
                theirs += 1
    except Exception as error:
        print(f"hubbleflow: couldn't reach the caching API: {error}", file=sys.stderr)
        return 1

    print(f"released {ours} hubbleflow cache(s)")
    if theirs:
        print(f"{theirs} cache(s) from something else left alone")
    return 0


def _flag(value: str | None) -> bool | None:
    """`--cache`, `--cache=1` and `--cache=0` all mean what they look like."""
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.purge_caches:
        return _purge_caches()

    if args.cwd and not args.cwd.is_dir():
        print(f"hubbleflow: no such directory: {args.cwd}", file=sys.stderr)
        return 2

    model = None
    if args.model:
        from hubbleflow.models import resolve

        model = resolve(args.model)

    config = Config.load(
        workspace=args.cwd,
        model=model,
        continue_session=args.continue_session,
        auto_approve=args.yolo,
        cache=_flag(args.cache),
    )

    prompt = " ".join(args.prompt).strip() or None
    if args.print and not prompt:
        print("hubbleflow: --print needs a prompt", file=sys.stderr)
        return 2

    from hubbleflow.app import Hubbleflow  # deferred: keeps --help and --version instant

    app = Hubbleflow(config)
    try:
        return asyncio.run(app.run(prompt, interactive=not args.print))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
