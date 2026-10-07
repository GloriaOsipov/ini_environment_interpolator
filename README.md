# INI Environment Interpolator

Resolves `${VAR}` environment variables and `${section:key}` cross-references in INI values when `get` is called, built on the standard library's `configparser`.

```python
from ini_environment_interpolator import InterpolatingConfigParser, InterpolationError

parser = InterpolatingConfigParser(env={"API_URL": "https://svc.internal"})
parser.read_string("""
[db]
host = db.internal

[app]
url = ${API_URL}/api
db_host = ${db:host}
""")

print(parser.get("app", "url"))      # https://svc.internal/api
print(parser.get("app", "db_host"))   # db.internal
```

## Why

`configparser` ships with `BasicInterpolation` and `ExtendedInterpolation`, both of which use `%`-delimited syntax. They collide with values that legitimately contain percent signs (URL-encoded strings, `cron` snippets, Windows path-escaping), and neither reads from `os.environ`. This library exists for the case where a config file should be able to say `host = ${DB_HOST}` and have it come from the process environment without a separate post-processing step.

The trade-off: there is no default-value syntax (`${VAR:-default}`) and no nested re-expansion of a resolved value. If a referenced value itself contains `${...}`, that inner reference *will* be expanded when the outer one is fetched (resolution is recursive on stored values), but the result of an environment lookup is treated as a literal. This keeps failure modes flat — a missing reference raises `InterpolationError` (a `ValueError` subclass) with the section, key, and offending reference named, rather than producing a silently-wrong interpolated string.

## Edge you will hit

Forward references work because interpolation happens at `get`-time, after the whole file is parsed. Cyclic references (`a = ${s:b}`, `b = ${s:a}`) are detected and raise `InterpolationError`. The env-name form `${VAR}` does **not** permit internal whitespace — `${ MY_VAR }` is returned verbatim, because a space inside an env name is almost always a typo. The `${section: key}` form *does* trim whitespace around the section and key, so you can lay out values readably. `$VAR` without braces is left alone, since shell snippets in config values routinely contain literal `$`.

## Exports

- `InterpolatingConfigParser` — subclass of `configparser.ConfigParser`. Constructor accepts an optional `env: Mapping[str, str]`; if omitted, a snapshot of `os.environ` is taken at construction time. `get()` returns interpolated strings unless called with `raw=True`.
- `InterpolationError` — subclass of `ValueError`, raised on any unresolvable or cyclic reference.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

