#!/usr/bin/env python3
"""Yahoo OAuth token management for FBP trade bot — compatibility shim.

The real implementation lives in data_pipeline/token_manager.py, which is
the copy every data_pipeline/*.py script actually loads in practice (they
run as `python3 data_pipeline/foo.py`, so Python resolves their bare
`from token_manager import ...` against that directory).

This root-level file exists only so scripts that run with the repo root
on sys.path (get_token.py, calculate_baselines.py, test_yahoo_2026.py,
fetch_2026_yahoo_data.py) can still do `from token_manager import
get_access_token` without a second, drifting copy of ~100 lines of OAuth
logic. Previously there were three independent copies of this file (here,
data_pipeline/, and random/) that had already started to diverge slightly;
this consolidates them to one real implementation plus this shim.

If you need to change token/refresh logic, edit data_pipeline/token_manager.py
— this file should never need to change except if that path moves.
"""

import importlib.util
import os

_IMPL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data_pipeline", "token_manager.py"
)
_spec = importlib.util.spec_from_file_location("_fbp_token_manager_impl", _IMPL_PATH)
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)

# Re-export the public API from the real implementation.
get_stored_token = _impl.get_stored_token
save_token = _impl.save_token
is_token_expired = _impl.is_token_expired
refresh_access_token = _impl.refresh_access_token
get_access_token = _impl.get_access_token

CLIENT_ID = _impl.CLIENT_ID
CLIENT_SECRET = _impl.CLIENT_SECRET
REDIRECT_URI = _impl.REDIRECT_URI
AUTH_URL = _impl.AUTH_URL
TOKEN_URL = _impl.TOKEN_URL
TOKEN_FILE = _impl.TOKEN_FILE

__all__ = [
    "get_stored_token",
    "save_token",
    "is_token_expired",
    "refresh_access_token",
    "get_access_token",
    "CLIENT_ID",
    "CLIENT_SECRET",
    "REDIRECT_URI",
    "AUTH_URL",
    "TOKEN_URL",
    "TOKEN_FILE",
]
