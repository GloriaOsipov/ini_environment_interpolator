"""INI Environment Interpolator.

A focused wrapper around :mod:`configparser` that resolves ``${ENV}`` and
``${section:key}`` references while parsing, before the caller ever sees the
value. The parser itself is untouched; only string values are rewritten, so
quoting, multiline, and escaping behave exactly as :mod:`configparser` does.
"""

from .core import InterpolatingConfigParser, InterpolationError

__all__ = ["InterpolatingConfigParser", "InterpolationError"]
