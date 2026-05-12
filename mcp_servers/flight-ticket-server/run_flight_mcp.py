#!/usr/bin/env python3
"""Start FlightTicketMCP safely for stdio MCP clients.

The upstream package prints startup messages to stdout. Stdio MCP clients expect
stdout to carry protocol frames, so route plain print output to stderr.
"""

import builtins
import tempfile
import sys
import socket


_original_print = builtins.print


def _print_to_stderr(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    return _original_print(*args, **kwargs)


builtins.print = _print_to_stderr

from DrissionPage import ChromiumOptions, ChromiumPage  # noqa: E402
from flight_ticket_mcp_server.tools import flight_search_tools  # noqa: E402


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _patched_searcher_init(self, headless=True):
    self.base_url = "https://flights.ctrip.com/online/list/oneway-{}-{}?_=1&depdate={}&cabin=Y_S_C_F"
    co = ChromiumOptions()
    co.set_browser_path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    co.set_local_port(_find_free_port())
    co.set_user_data_path(tempfile.mkdtemp(prefix="flight-mcp-chrome-"))
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    if headless:
        co.headless()
    self.page = ChromiumPage(co)


flight_search_tools.FlightRouteSearcher.__init__ = _patched_searcher_init

from flight_ticket_mcp_server import main  # noqa: E402


if __name__ == "__main__":
    main()
