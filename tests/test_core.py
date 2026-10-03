"""Tests for the INI Environment Interpolator.

Every test constructs a parser, feeds it a small INI string, and asserts on
the interpolated result. Determinism is guaranteed by passing an explicit
``env`` mapping to the parser so the process environment never enters the
picture.
"""

import configparser
import unittest

from ini_environment_interpolator import InterpolatingConfigParser, InterpolationError


class ParsingTests(unittest.TestCase):
    def _load(self, text, env=None):
        parser = InterpolatingConfigParser(env=env)
        parser.read_string(text)
        return parser

    def test_env_substitution(self):
        parser = self._load("[app]\nurl = ${API_URL}\n", env={"API_URL": "https://svc.internal"})
        self.assertEqual(parser.get("app", "url"), "https://svc.internal")

    def test_section_key_substitution(self):
        parser = self._load(
            "[db]\nhost = db.internal\n"
            "[app]\ndb_host = ${db:host}\n"
        )
        self.assertEqual(parser.get("app", "db_host"), "db.internal")

    def test_default_section_reference(self):
        parser = self._load(
            "[DEFAULT]\nbase = /opt/app\n"
            "[run]\npath = ${DEFAULT:base}/bin\n"
        )
        self.assertEqual(parser.get("run", "path"), "/opt/app/bin")

    def test_whitespace_around_section_key(self):
        parser = self._load(
            "[db]\nhost = db.internal\n"
            "[app]\ndb_host = ${db : host}\n"
        )
        self.assertEqual(parser.get("app", "db_host"), "db.internal")

    def test_multiple_refs_in_one_value(self):
        parser = self._load(
            "[db]\nhost = db.internal\nport = 5432\n"
            "[app]\ndsn = postgres://${db:host}:${db:port}/app\n",
            env={"USER": "admin"}
        )
        self.assertEqual(
            parser.get("app", "dsn"),
            "postgres://db.internal:5432/app"
        )

    def test_env_and_section_key_mixed(self):
        parser = self._load(
            "[db]\nhost = db.internal\n"
            "[app]\nurl = https://${db:host}/${USER}\n",
            env={"USER": "admin"}
        )
        self.assertEqual(parser.get("app", "url"), "https://db.internal/admin")

    def test_indirect_chain(self):
        # a -> b -> c, all declared in the same section. Order in the file is
        # reversed from the dependency order; resolution at read-time handles
        # this because the whole file is parsed before get() is called.
        parser = self._load(
            "[chain]\n"
            "c = gamma\n"
            "b = ${chain:c}\n"
            "a = ${chain:b}\n"
        )
        self.assertEqual(parser.get("chain", "a"), "gamma")

    def test_get_raw_returns_unresolved(self):
        parser = self._load("[app]\nurl = ${API_URL}\n", env={"API_URL": "https://svc.internal"})
        self.assertEqual(parser.get("app", "url", raw=True), "${API_URL}")

    def test_missing_env_raises(self):
        parser = self._load("[app]\nurl = ${NOPE}\n", env={})
        with self.assertRaises(InterpolationError) as ctx:
            parser.get("app", "url")
        self.assertIn("NOPE", str(ctx.exception))

    def test_missing_section_raises(self):
        parser = self._load("[app]\nurl = ${missing:host}\n")
        with self.assertRaises(InterpolationError) as ctx:
            parser.get("app", "url")
        self.assertIn("section", str(ctx.exception))

    def test_missing_key_raises(self):
        parser = self._load("[db]\nhost = db.internal\n[app]\nurl = ${db:nope}\n")
        with self.assertRaises(InterpolationError) as ctx:
            parser.get("app", "url")
        self.assertIn("key not found", str(ctx.exception))

    def test_cyclic_reference_raises(self):
        parser = self._load(
            "[DEFAULT]\na = ${DEFAULT:b}\nb = ${DEFAULT:a}\n"
        )
        with self.assertRaises(InterpolationError) as ctx:
            parser.get("DEFAULT", "a")
        self.assertIn("cyclic", str(ctx.exception))

    def test_malformed_empty_key_raises(self):
        parser = self._load("[app]\nurl = ${db:}\n[db]\nhost = x\n")
        with self.assertRaises(InterpolationError) as ctx:
            parser.get("app", "url")
        self.assertIn("malformed", str(ctx.exception))

    def test_no_refs_untouched(self):
        parser = self._load("[app]\nname = plain\n", env={})
        self.assertEqual(parser.get("app", "name"), "plain")

    def test_dollar_without_braces_untouched(self):
        # ``$VAR`` (no braces) is deliberately not a reference form. We leave
        # it alone rather than guessing, because ``configparser`` values often
        # contain shell snippets where ``$`` is significant.
        parser = self._load("[app]\ncmd = echo $HOME\n", env={"HOME": "/h"})
        self.assertEqual(parser.get("app", "cmd"), "echo $HOME")

    def test_literal_dollar_braces_untouched_when_no_match(self):
        # ``${ MY_VAR }`` has internal whitespace in the env-name slot, so it
        # does not match the env reference pattern. It is returned verbatim.
        parser = self._load("[app]\nval = ${ MY_VAR }\n", env={"MY_VAR": "x"})
        self.assertEqual(parser.get("app", "val"), "${ MY_VAR }")

    def test_interpolation_error_is_value_error(self):
        parser = self._load("[app]\nurl = ${NOPE}\n", env={})
        with self.assertRaises(ValueError):
            parser.get("app", "url")

    def test_fallback_returned_when_missing_option(self):
        parser = self._load("[app]\n", env={})
        # The fallback path should not attempt interpolation on the sentinel.
        self.assertEqual(
            parser.get("app", "missing", fallback="static"),
            "static"
        )

    def test_mutation_then_get_reflects_change(self):
        parser = self._load("[db]\nhost = db.internal\n[app]\nurl = ${db:host}\n")
        self.assertEqual(parser.get("app", "url"), "db.internal")
        parser.set("db", "host", "db2.internal")
        self.assertEqual(parser.get("app", "url"), "db2.internal")

    def test_env_snapshot_taken_at_construction(self):
        # Construct with one env, mutate the dict afterwards, confirm the
        # parser is unaffected — it copied.
        env = {"K": "first"}
        parser = self._load("[app]\nv = ${K}\n", env=env)
        env["K"] = "second"
        self.assertEqual(parser.get("app", "v"), "first")


if __name__ == "__main__":
    unittest.main()
