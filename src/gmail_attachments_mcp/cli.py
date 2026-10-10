"""Command line entry point: `gmail-attachments-mcp {auth,serve,download}`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .auth import DEFAULT_CLIENT_SECRET, DEFAULT_TOKEN, LazySession, authorize, load_session
from .gmail import API, DEFAULT_ATTACHMENTS_DIR, Downloader


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gmail-attachments-mcp", description=__doc__)
    parser.add_argument("--token", default=DEFAULT_TOKEN,
                        help=f"Saved OAuth token (default: {DEFAULT_TOKEN})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_auth = sub.add_parser("auth", help="Authorize read-only Gmail access in the browser")
    p_auth.add_argument("--client-secret", default=DEFAULT_CLIENT_SECRET,
                        help=f"OAuth Desktop client JSON (default: {DEFAULT_CLIENT_SECRET})")
    p_serve = sub.add_parser("serve", help="Run the MCP server over stdio")
    p_dl = sub.add_parser("download", help="Save a message's attachment(s) and print JSON")
    p_dl.add_argument("message", help="Message ID (hex or decimal)")
    p_dl.add_argument("--attachment", help="partId or attachment ID (default: all)")
    for p in (p_serve, p_dl):
        p.add_argument("--attachments-dir",
                       help=f"Where attachments are saved (default: {DEFAULT_ATTACHMENTS_DIR})")

    args = parser.parse_args(argv)
    # The token and saved attachments are mailbox data.
    os.umask(0o077)
    # stdout carries MCP protocol traffic, so all logging goes to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")

    try:
        if args.cmd == "auth":
            authorize(args.client_secret, args.token)
            session = load_session(args.token)
            email = session.get(f"{API}/profile", timeout=30).json().get("emailAddress")
            print(f"Authorized {email}. Token saved to {os.path.expanduser(args.token)}")
            return

        if args.cmd == "serve" and not os.path.exists(os.path.expanduser(args.token)):
            logging.warning("No Gmail token at %s yet; download_attachment will fail until you "
                            "run `gmail-attachments-mcp auth`.", args.token)
        downloader = Downloader(LazySession(args.token), args.attachments_dir)
        if args.cmd == "download":
            print(json.dumps(downloader.download(args.message, args.attachment), indent=2,
                             ensure_ascii=False))
            return
    except ValueError as e:
        sys.exit(str(e))

    from .server import build_server

    build_server(downloader).run()
