"""Interpolating INI parser built on the standard library's configparser.

Design choices, stated plainly
------------------------------
This module resolves two reference forms and nothing else:

* ``${VAR}`` — an environment variable. The name matches
  ``[A-Za-z_][A-Za-z0-9_]*``.
* ``${section:key}`` — a value already declared in another section. The section
  name and key are trimmed of surrounding whitespace, and must already exist
  *in the parsed data* by the time the reference is encountered. Forward and
  self-references therefore resolve only if they appear textually earlier in
  the same section (a common, useful restriction).

There is deliberately *no* support for the dollar-sign-without-braces syntax
(``$VAR``), no default-value syntax (``${VAR:-default}``), and no nested
expansion of the result of an expansion. If you need those, use
``configparser.ExtendedInterpolation``. The point of this library is a small,
flat resolver with a simple failure mode: any reference that cannot be
resolved raises :class:`InterpolationError` with enough context to find it.

Cycles (``a = ${DEFAULT:b}``, ``b = ${DEFAULT:a}``) are detected and reported
rather than causing infinite recursion.
"""

from __future__ import annotations

import configparser
import os
import re
from typing import Mapping, MutableMapping

__all__ = ["InterpolatingConfigParser", "InterpolationError"]

# A reference is ``${`` followed by either an env-style name, or a
# ``section:key`` pair, followed by ``}``. Section and key names are trimmed
# of whitespace in the ``section:key`` form so that users can lay out INI
# values readably (``${db : host}`` is the same as ``${db:host}``). The env
# form does not permit internal whitespace — ``${ MY_VAR }`` is *not* a valid
# reference and is left untouched, which is the safest default: a stray space
# in an env name almost always indicates a typo rather than intent.
_REF_RE = re.compile(
    r"\$\{(?:(?P<env>[A-Za-z_][A-Za-z0-9_]*)|(?P<sect>[^:}]+):(?P<key>[^:}]*))\}"
)


class InterpolationError(ValueError):
    """Raised when a reference in an INI value cannot be resolved.

    The message names the offending reference and, where useful, the section
    and key in which it appeared. This is a subclass of :class:`ValueError`
    so existing ``except ValueError`` blocks around ``configparser`` usage are
    not bypassed.
    """


class InterpolatingConfigParser(configparser.ConfigParser):
    """A :class:`configparser.ConfigParser` that resolves ``${...}`` references.

    Interpolation happens after the file is read, the moment
    :meth:`get` is called. This is preferable to rewriting values at parse
    time for two reasons:

    1. A value such as ``${DEFAULT:db_url}`` may reference a key declared
       *after* it in the file. Postponing resolution to read-time means
       declaration order within a file does not matter, as long as the
       referenced key exists by the time someone asks for it.
    2. ``configparser`` internals (raw vs. interpolated access, ``DEFAULTSECT``
       fallthrough) are preserved exactly. We only override the value-producing
       step.

    The trade-off is that mutation after parsing (calling ``set`` to change a
    referenced value) is honoured on subsequent ``get`` calls — which is
    usually desirable, but means interpolated values are not cached.
    """

    def __init__(self, *args, env: Mapping[str, str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # Take a snapshot of the environment at construction time. Doing this
        # once, rather than reading ``os.environ`` on every ``get``, makes
        # interpolation deterministic for the life of the parser — important
        # for tests and for config that is validated once at startup.
        self._env: Mapping[str, str] = dict(env) if env is not None else dict(os.environ)

    # ``ConfigParser`` routes every value retrieval through ``_interpolate``
    # when ``interpolation`` is set. We bypass that mechanism entirely by
    # leaving interpolation as the default (``BasicInterpolation``) and
    # overriding the higher-level ``get``. The default ``BasicInterpolation``
    # would mangle ``$`` signs and treat ``%`` specially, so we also pass
    # ``raw=True`` to the underlying retrieval to get the literal stored string
    # before applying our own resolver.
    def get(self, section, option, *, raw=False, vars=None, fallback=configparser._UNSET):
        raw_value = super().get(section, option, raw=True, vars=vars, fallback=fallback)
        if raw or not isinstance(raw_value, str):
            # If the caller asked for raw, or the value is not a string (e.g.
            # a sentinel fallback object returned verbatim), do not touch it.
            return raw_value
        return self._resolve(raw_value, section, str(option))

    def _resolve(self, value: str, origin_section: str, origin_key: str,
                 _seen: tuple[tuple[str, str], ...] = ()) -> str:
        """Resolve every ``${...}`` reference in *value*.

        *origin_section* and *origin_key* are the location of *value* in the
        INI file, used for cycle detection and for error messages. *_seen* is
        the chain of (section, key) pairs currently being expanded; a repeated
        pair means a cycle.
        """
        def replace(match: re.Match[str]) -> str:
            env_name = match.group("env")
            if env_name is not None:
                if env_name in self._env:
                    return self._env[env_name]
                raise InterpolationError(
                    f"unresolved environment reference '${{{env_name}}}' "
                    f"in [{origin_section}]{origin_key}"
                )
            sect = match.group("sect").strip()
            key = match.group("key").strip()
            if not sect or not key:
                # The regex permits an empty section or key (e.g. ``${:foo}``).
                # Treat that as a malformed reference rather than silently
                # resolving to the default section, which would surprise.
                raise InterpolationError(
                    f"malformed reference '{match.group(0)}' "
                    f"in [{origin_section}]{origin_key}"
                )
            return self._lookup(sect, key, origin_section, origin_key, _seen)

        return _REF_RE.sub(replace, value)

    def _lookup(self, sect: str, key: str,
                origin_section: str, origin_key: str,
                _seen: tuple[tuple[str, str], ...]) -> str:
        marker = (sect, key)
        if marker in _seen:
            chain = " -> ".join(f"[{s}]{k}" for s, k in (*_seen, marker))
            raise InterpolationError(
                f"cyclic reference detected: {chain}"
            )
        # Use raw retrieval so we get the literal stored value, not
        # BasicInterpolation's %-handling.
        if not self.has_section(sect) and sect != self.default_section:
            raise InterpolationError(
                f"unresolved reference '${{{sect}:{key}}}' "
                f"in [{origin_section}]{origin_key}: section '{sect}' not found"
            )
        if not self.has_option(sect, key):
            raise InterpolationError(
                f"unresolved reference '${{{sect}:{key}}}' "
                f"in [{origin_section}]{origin_key}: key not found"
            )
        raw_value = super().get(sect, key, raw=True)
        # ``super().get`` for a missing option returns the sentinel
        # ``configparser._UNSET``; we check for it defensively even though
        # ``has_option`` should have guarded the path.
        if raw_value is configparser._UNSET:
            raise InterpolationError(
                f"unresolved reference '${{{sect}:{key}}}' "
                f"in [{origin_section}]{origin_key}: key not found"
            )
        return self._resolve(raw_value, sect, key, (*_seen, marker))
