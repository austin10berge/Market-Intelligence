"""Default idle timeout for MCP streamable-HTTP sessions.

Neither mcp-proxy (schwab-mcp) nor fastmcp (alpaca-mcp-server) passes
session_idle_timeout when constructing mcp.server.streamable_http_manager.
StreamableHTTPSessionManager, so it defaults to None: sessions a client
abandons without a clean disconnect never leave the manager's
_server_instances dict. Confirmed as the cause of both containers leaking
to ~2GB over 8 days uptime (2026-08-02/03). Patching the shared base class
here covers both wrappers, since fastmcp's subclass reaches it via
super().__init__(). 1800s matches the value the SDK's own docstring
recommends for "most deployments".

Auto-imported by the interpreter at startup (stdlib `site` behavior) as
long as this file is on sys.path — no changes needed to how either
server is invoked.
"""

import mcp.server.streamable_http_manager as _shm

_orig_init = _shm.StreamableHTTPSessionManager.__init__


def _init_with_idle_timeout(self, *args, **kwargs):
    kwargs.setdefault("session_idle_timeout", 1800)
    _orig_init(self, *args, **kwargs)


_shm.StreamableHTTPSessionManager.__init__ = _init_with_idle_timeout
