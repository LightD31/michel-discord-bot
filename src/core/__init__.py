"""Core infrastructure shared by every extension.

Modules
-------
- :mod:`src.core.logging` — colored ANSI logger factory (was ``src/logutil.py``)
- :mod:`src.core.config`  — atomic JSON config I/O + reactive :class:`ConfigStore`
                            (was ``src/config_manager.py``)
- :mod:`src.core.db`      — async MongoDB singleton (was ``src/mongodb.py``)
- :mod:`src.core.http`    — shared aiohttp session + ``fetch()`` with retry
- :mod:`src.core.errors`  — base exception hierarchy

The old module paths (``src.logutil``, ``src.mongodb``, ``src.config_manager``) are
preserved as re-export shims for one release. New code should import from
``src.core``.
"""
