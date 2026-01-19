#!/usr/bin/env python3
"""Compatibility shim so `from token_manager import ...` works from repo root.

The real implementation lives in `random/token_manager.py`.
This file simply re-exports those functions.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RANDOM_DIR = os.path.join(BASE_DIR, "random")

if RANDOM_DIR not in sys.path:
    sys.path.append(RANDOM_DIR)

from token_manager import (  # type: ignore
    get_stored_token,
    save_token,
    is_token_expired,
    refresh_access_token,
    get_access_token,
)

__all__ = [
    "get_stored_token",
    "save_token",
    "is_token_expired",
    "refresh_access_token",
    "get_access_token",
]
