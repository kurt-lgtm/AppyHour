"""deploy_prod import/dependency preflight — the D1 guard (ENGINEERING_GOTCHAS #1).

🔴 The burn these tests encode (2026-09-02, three instances in ONE day): a consumer was copied
to C:\\AppyHourProd\\AppyHour without its provider. `carrier_mix_pivot.py` landed while prod's
`appyhour_lib/credentials.py` was still the 2026-06-23 copy with no `get_google_credentials` —
`ImportError` on EVERY run. The copy verified perfectly: post-copy bytes equalled dev bytes.
Byte-identity is not runnability.

Every test below is written to fail against a deploy_prod.py that has no preflight, and each
one names the property it protects rather than the implementation:

  a) missing provider  -> REFUSED, zero copies, no log rows, message names provider AND consumer
  b) valid control     -> PASSES (the guard must not just refuse everything)
  c) byte-identity     -> refusal stands even though the copy would be byte-perfect (the 09-02 shape)
  d) isolation         -> the check runs in a subprocess on a PRODUCTION-shaped sys.path:
                          sys.path[0] is the prod root, zero dev-tree entries, appyhour_lib
                          never imported (the editable-install trap, D2)
  e) import safety     -> a module with a top-level side effect is checked WITHOUT executing it
  f) pre-existing      -> breakage already in prod does NOT block an unrelated deploy (A1: a gate
                          that fires on someone else's mess becomes noise and gets bypassed)
  g) optional imports  -> `try: import x / except ImportError:` is not a refusal
  h) resolver parity   -> the preflight resolver agrees with automation_health._resolve_module
                          (rule 19's semantics), so the two cannot drift apart
"""

import importlib.util
import sys
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_prod.py"
spec = importlib.util.spec_from_file_location("deploy_prod", SCRIPT)
dp = importlib.util.module_from_spec(spec)
sys.modules["deploy_prod"] = dp
spec.loader.exec_module(dp)


class _Fixture(unittest.TestCase):
    """A miniature dev/prod pair. Never touches C:\\AppyHourProd — that tree is LIVE."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.dev = root / "dev"
        self.prod = root / "prod"
        self.log = root / "deploy.jsonl"
        self.dev.mkdir(parents=True)
        self.prod.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def w(self, base, rel, text, mtime=None):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            import os
            os.utime(p, (mtime, mtime))
        return p

    def prod_files(self):
        return sorted(str(p.relative_to(self.prod)) for p in self.prod.rglob("*.py"))


class MissingProviderIsRefused(_Fixture):
    def test_new_provider_left_behind_refuses_the_consumer(self):
        """(a) The `--only` shape: consumer selected, its brand-new provider not."""
        now = time.time()
        self.w(self.prod, "reports/consumer.py", "print('v1')\n", mtime=now - 3600)
        self.w(self.dev, "reports/consumer.py",
               "from appyhour_lib.credentials import get_google_credentials\n"
               "print(get_google_credentials)\n", mtime=now)
        # provider exists ONLY in dev -> dev_only, and --include-new is off, so it stays behind
        self.w(self.dev, "appyhour_lib/__init__.py", "", mtime=now)
        self.w(self.dev, "appyhour_lib/credentials.py",
               "def get_google_credentials():\n    return None\n", mtime=now)

        c = dp.classify(self.dev, self.prod)
        c, _ = dp.scope(c, ("reports/consumer.py",))
        rc = dp.apply_copies(c, self.prod, self.log, include_new=False)

        self.assertEqual(rc, 3, "a missing provider must REFUSE with its own exit code")
        self.assertEqual((self.prod / "reports/consumer.py").read_text(encoding="utf-8"),
                         "print('v1')\n", "refusal must copy NOTHING")
        self.assertFalse(self.log.exists(), "refusal must write no deploy-log rows")

    def test_refusal_names_provider_and_consumer(self):
        now = time.time()
        self.w(self.prod, "reports/consumer.py", "print('v1')\n", mtime=now - 3600)
        self.w(self.dev, "reports/consumer.py",
               "from appyhour_lib.credentials import get_google_credentials\n", mtime=now)
        self.w(self.dev, "appyhour_lib/__init__.py", "", mtime=now)
        self.w(self.dev, "appyhour_lib/credentials.py", "def get_google_credentials():\n    ...\n",
               mtime=now)
        c = dp.classify(self.dev, self.prod)
        c, _ = dp.scope(c, ("reports/consumer.py",))
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        blob = " ".join(v["message"] for v in res["violations"])
        self.assertIn("appyhour_lib.credentials", blob, "must name the missing PROVIDER")
        self.assertIn("consumer.py", blob, "must name the CONSUMER that needs it")


class ValidControlPasses(_Fixture):
    def test_provider_shipped_alongside_consumer_passes(self):
        """(b) Same fixture, provider included -> deploy proceeds. The guard is not a brick."""
        now = time.time()
        self.w(self.prod, "reports/consumer.py", "print('v1')\n", mtime=now - 3600)
        self.w(self.dev, "reports/consumer.py",
               "from appyhour_lib.credentials import get_google_credentials\n", mtime=now)
        self.w(self.dev, "appyhour_lib/__init__.py", "", mtime=now)
        self.w(self.dev, "appyhour_lib/credentials.py", "def get_google_credentials():\n    ...\n",
               mtime=now)
        c = dp.classify(self.dev, self.prod)
        rc = dp.apply_copies(c, self.prod, self.log, include_new=True)
        self.assertEqual(rc, 0, "provider + consumer together is a runnable deploy")
        self.assertIn("appyhour_lib\\credentials.py", self.prod_files())

    def test_stdlib_and_third_party_imports_are_not_violations(self):
        now = time.time()
        self.w(self.prod, "a.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "a.py", "import json, sqlite3\nimport gspread\nfrom pathlib import Path\n",
               mtime=now)
        c = dp.classify(self.dev, self.prod)
        rc = dp.apply_copies(c, self.prod, self.log, include_new=False)
        self.assertEqual(rc, 0, "only FIRST-PARTY (dev-tree) modules are in scope")


class ByteIdentityCannotSaveIt(_Fixture):
    def test_byte_perfect_copy_still_refused_when_prod_provider_lacks_the_symbol(self):
        """(c) The exact 2026-09-02 instance #1: the provider FILE exists in prod, at an older
        version missing the symbol. The copy would verify byte-perfect and still be dead."""
        now = time.time()
        self.w(self.prod, "appyhour_lib/__init__.py", "", mtime=now - 7200)
        self.w(self.prod, "appyhour_lib/credentials.py",           # the 2026-06-23 copy
               "def get_shopify_credentials():\n    ...\n", mtime=now - 7200)
        self.w(self.dev, "appyhour_lib/__init__.py", "", mtime=now)
        self.w(self.dev, "appyhour_lib/credentials.py",            # dev has both
               "def get_shopify_credentials():\n    ...\n\n"
               "def get_google_credentials():\n    ...\n", mtime=now)
        consumer_src = ("from appyhour_lib.credentials import get_google_credentials\n"
                        "print(get_google_credentials)\n")
        self.w(self.prod, "ShippingReports/carrier_mix_pivot.py", "print('v1')\n", mtime=now - 3600)
        self.w(self.dev, "ShippingReports/carrier_mix_pivot.py", consumer_src, mtime=now)

        c = dp.classify(self.dev, self.prod)
        # --only the consumer: the provider is stale too, and is deliberately left behind
        c, _ = dp.scope(c, ("ShippingReports/*",))
        rc = dp.apply_copies(c, self.prod, self.log, include_new=False)
        self.assertEqual(rc, 3, "missing SYMBOL in an existing provider must refuse")

        # ...and prove the post-copy BYTE check apply_copies performs would have said PASS on
        # that very file. Byte-identity is the verification the 09-02 deploy actually passed.
        import shutil
        src = self.dev / "ShippingReports/carrier_mix_pivot.py"
        landed = Path(self._tmp.name) / "landed.py"
        shutil.copy2(src, landed)
        self.assertEqual(landed.read_bytes(), src.read_bytes(),
                         "the byte gate says PASS on the exact file the preflight refused")
        self.assertNotEqual(src.read_bytes(), (self.prod / "ShippingReports/carrier_mix_pivot.py")
                            .read_bytes(), "refusal must have left prod untouched")


class RunsIsolatedOnAProductionShapedPath(_Fixture):
    def test_child_pins_prod_root_and_never_imports_the_library(self):
        """(d) The editable-install trap: appyhour_lib is a pip editable install mapped to the
        DEV tree. A check that lets sys.path stay dev-shaped reports a false PASS."""
        now = time.time()
        self.w(self.prod, "a.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "a.py", "import json\n", mtime=now)
        c = dp.classify(self.dev, self.prod)
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        proof = res["path_proof"]
        self.assertEqual(Path(proof["sys_path_0"]), self.prod.resolve(),
                         "sys.path[0] must be the PROD root inside the checking process")
        self.assertEqual(proof["dev_entries"], [],
                         "no dev-tree entry may remain on the checking process's sys.path")
        self.assertFalse(proof["appyhour_lib_imported"],
                         "the checker must never import the library it is reasoning about")
        self.assertEqual(proof["workspace_entries"], [],
                         "no Claude Projects entry may remain on the checking process's sys.path")
        self.assertNotIn(str(SCRIPT.parent), proof["sys_path"],
                         "the interpreter's automatic script-dir entry (the DEV scripts dir) "
                         "must be stripped, not inherited")

    def test_proof_does_not_libel_the_prod_root_as_a_dev_entry(self):
        """The DEPLOYED copy of this script lives INSIDE the prod tree. A proof derived from the
        script's own location would report the prod root as a dev entry — evidence that mislabels
        is the failure mode this whole check exists to remove."""
        now = time.time()
        self.w(self.prod, "a.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "a.py", "import json\n", mtime=now)
        c = dp.classify(self.dev, self.prod)
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        self.assertNotIn(str(self.prod.resolve()), res["path_proof"]["dev_entries"])

    def test_check_runs_out_of_process(self):
        now = time.time()
        self.w(self.prod, "a.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "a.py", "import json\n", mtime=now)
        c = dp.classify(self.dev, self.prod)
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        import os as _os
        self.assertNotEqual(res["path_proof"]["pid"], _os.getpid(),
                            "the check must run in a separate process, never in this one")


class NeverExecutesTheCodeItChecks(_Fixture):
    def test_module_with_top_level_side_effect_is_not_executed(self):
        """(e) Many scripts in this tree do work at import time — live writes, API calls, Slack
        posts. A deploy gate that fires one of those is worse than no gate."""
        sentinel = Path(self._tmp.name) / "SIDE_EFFECT_FIRED"
        now = time.time()
        body = (f"from pathlib import Path\n"
                f"Path(r'{sentinel}').write_text('fired', encoding='utf-8')\n")
        self.w(self.prod, "writer.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "writer.py", body, mtime=now)
        # a consumer that imports it, so the provider is parsed for symbols too
        self.w(self.prod, "consumer.py", "y = 1\n", mtime=now - 3600)
        self.w(self.dev, "consumer.py", "import writer\n", mtime=now)

        c = dp.classify(self.dev, self.prod)
        rc = dp.apply_copies(c, self.prod, self.log, include_new=False)
        self.assertEqual(rc, 0)
        self.assertFalse(sentinel.exists(),
                         "the preflight executed module-level code — that is a live-write risk")


class PreExistingBreakageDoesNotBlock(_Fixture):
    def test_violation_already_present_in_prod_is_reported_not_refused(self):
        """(f) A1: an alarm that fires on unrelated pre-existing mess trains people to bypass it."""
        now = time.time()
        broken = "from ghostmod import thing\n"
        self.w(self.prod, "legacy.py", broken, mtime=now - 3600)
        self.w(self.dev, "legacy.py", broken + "# touched\n", mtime=now)
        self.w(self.dev, "ghostmod.py", "thing = 1\n", mtime=now)   # dev-only, left behind

        c = dp.classify(self.dev, self.prod)
        c, _ = dp.scope(c, ("legacy.py",))
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        self.assertEqual(res["violations"], [], "already broken in prod -> not a NEW violation")
        self.assertTrue(res["preexisting"], "...but it must still be reported, never dropped")


class OptionalImportsAreNotRefusals(_Fixture):
    def test_try_except_importerror_is_reported_unchecked(self):
        """(g) `try: import x / except ImportError: x = None` is a deliberate optional dep."""
        now = time.time()
        self.w(self.prod, "opt.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "opt.py",
               "try:\n    from plugins.extra import boost\nexcept ImportError:\n    boost = None\n",
               mtime=now)
        self.w(self.dev, "plugins/__init__.py", "", mtime=now)
        self.w(self.dev, "plugins/extra.py", "def boost():\n    ...\n", mtime=now)
        c = dp.classify(self.dev, self.prod)
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        self.assertEqual(res["violations"], [])
        self.assertTrue(any("optional" in u["message"] for u in res["unchecked"]))

    def test_type_checking_only_import_is_ignored(self):
        now = time.time()
        self.w(self.prod, "tc.py", "x = 1\n", mtime=now - 3600)
        self.w(self.dev, "tc.py",
               "from typing import TYPE_CHECKING\n"
               "if TYPE_CHECKING:\n    from ghostmod import Thing\n", mtime=now)
        self.w(self.dev, "ghostmod.py", "class Thing: ...\n", mtime=now)
        c = dp.classify(self.dev, self.prod)
        res = dp.preflight_imports(dp.copy_set(c, self.prod, include_new=False),
                                   self.dev, self.prod)
        self.assertEqual(res["violations"], [], "an if TYPE_CHECKING import never executes")


class ResolverParityWithRule19(_Fixture):
    def test_agrees_with_automation_health_resolve_module(self):
        """(h) rule 19 already owns dir-anchored module resolution. Two copies that drift are
        two answers to one question, so this pins them together on a fixture set."""
        import importlib.util as iu
        ah_path = Path(__file__).resolve().parents[1] / "scripts" / "automation_health.py"
        s = iu.spec_from_file_location("automation_health_parity", ah_path)
        ah = iu.module_from_spec(s)
        sys.modules["automation_health_parity"] = ah
        s.loader.exec_module(ah)

        self.w(self.dev, "pkg/__init__.py", "")
        self.w(self.dev, "pkg/sub/__init__.py", "")
        self.w(self.dev, "pkg/sub/leaf.py", "")
        self.w(self.dev, "solo.py", "")
        dirs = [self.dev / "pkg", self.dev]
        for mod in ("pkg.sub.leaf", "pkg.sub", "pkg", "solo", "nope", "pkg.nope"):
            theirs = ah._resolve_module(mod, dirs)
            mine = dp._pf_resolve_module(mod, dirs, dp._PlainTree())
            self.assertEqual(bool(theirs), mine is not None, f"resolvability differs for {mod}")
            if theirs:
                self.assertEqual(theirs[-1], mine, f"target differs for {mod}")


if __name__ == "__main__":
    unittest.main()
