"""Project-wide constants and safety limits."""

from __future__ import annotations

# Security / resource limits (see plan section 6).
MAX_ROUTES = 10_000
MAX_SPEC_BYTES = 64 * 1024 * 1024  # 64 MB — large real-world dereferenced specs
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024  # 10 MB

# Default server binding — localhost only unless explicitly overridden.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000

# Admin dashboard prefix.
ADMIN_PREFIX = "/__admin"
