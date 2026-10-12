"""Minecraft status plugin; pure submodules can be imported without Nekro.

Nekro 2.4.0's collector checks ``hasattr(module, 'plugin')`` before reading
that attribute. This lazy entry point initializes the SDK hooks at that point,
while importing models, intent, query or rendering never installs dependencies.
"""
from importlib import import_module

__all__ = ["plugin", "main", "claims_message"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    from .deps import verify_imports

    verify_imports()
    plugin_module = import_module(".plugin", __name__)
    main_module = import_module(".main", __name__)
    globals().update(plugin=plugin_module.plugin, main=main_module,
                     claims_message=main_module.claims_message)
    return globals()[name]
