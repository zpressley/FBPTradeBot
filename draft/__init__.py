"""Draft module for FBP Trade Bot

The 2026 classes are exported lazily so `import draft.service` (the Spec 04 database layer) does
not pull in the Discord-era modules (and their dependencies) just to run.
"""

__all__ = ['DraftManager', 'PickValidator', 'ForkliftManager']

_LAZY = {
    'DraftManager': '.draft_manager',
    'PickValidator': '.pick_validator',
    'ForkliftManager': '.forklift_manager',
}


def __getattr__(name):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
