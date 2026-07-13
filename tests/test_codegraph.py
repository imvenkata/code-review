from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

from reviewlib import codegraph  # noqa: E402


def run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class ChangedSymbolsTests(unittest.TestCase):
    def test_extracts_definitions_across_languages(self) -> None:
        chunks = [
            ("core.py", "@@ -1 +1 @@\n-def process_order(order):\n+def process_order(order, region):\n"),
            ("api.ts", "@@ -1 +1 @@\n+export function fetchUser(id) {\n"),
            ("svc.go", "@@ -1 +1 @@\n+func HandleRequest(w, r) {\n"),
            ("types.rs", "@@ -1 +1 @@\n+struct OrderTotal {\n"),
        ]
        symbols = codegraph.changed_symbols(chunks)
        self.assertIn("process_order", symbols)
        self.assertIn("fetchUser", symbols)
        self.assertIn("HandleRequest", symbols)
        self.assertIn("OrderTotal", symbols)

    def test_drops_short_names_and_ignores_context_lines(self) -> None:
        chunks = [("x.py", "@@ -1 +1 @@\n def fn(x):\n-def ab(y):\n+    return fn(x)\n")]
        # `ab` is below the length floor; `fn` appears only on a context/return line, not a def.
        self.assertEqual(codegraph.changed_symbols(chunks), [])


class ReferencesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        run("git", "init", "-b", "main", cwd=self.repo)
        run("git", "config", "user.email", "t@e.com", cwd=self.repo)
        run("git", "config", "user.name", "t", cwd=self.repo)
        (self.repo / "core.py").write_text("def process_order(o):\n    return o\n", encoding="utf-8")
        (self.repo / "caller_a.py").write_text("from core import process_order\nprocess_order(1)\n", encoding="utf-8")
        (self.repo / "caller_b.py").write_text("import core\ncore.process_order(2)\n", encoding="utf-8")
        (self.repo / "NOTES.md").write_text("process_order is documented here\n", encoding="utf-8")
        run("git", "add", "-A", cwd=self.repo)
        run("git", "commit", "-m", "base", cwd=self.repo)

    def test_finds_callers_excluding_changed_and_ignored_files(self) -> None:
        hits = codegraph.references(
            self.repo, "process_order", ("**/*.md",), {"core.py"}, cap=20
        )
        paths = {path for path, _line, _text in hits}
        self.assertEqual(paths, {"caller_a.py", "caller_b.py"})  # core.py changed, NOTES.md ignored

    def test_respects_the_per_symbol_cap(self) -> None:
        hits = codegraph.references(self.repo, "process_order", (), {"core.py"}, cap=1)
        self.assertEqual(len(hits), 1)

    def test_word_boundary_matching(self) -> None:
        (self.repo / "noise.py").write_text("process_order_total = 1\n", encoding="utf-8")
        run("git", "add", "noise.py", cwd=self.repo)
        run("git", "commit", "-m", "noise", cwd=self.repo)
        hits = codegraph.references(self.repo, "process_order", (), {"core.py"}, cap=20)
        self.assertNotIn("noise.py", {path for path, _l, _t in hits})


class CoChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        run("git", "init", "-b", "main", cwd=self.repo)
        run("git", "config", "user.email", "t@e.com", cwd=self.repo)
        run("git", "config", "user.name", "t", cwd=self.repo)

    def _commit(self, message: str, **files: str) -> None:
        for name, body in files.items():
            (self.repo / name).write_text(body, encoding="utf-8")
        run("git", "add", "-A", cwd=self.repo)
        run("git", "commit", "-m", message, cwd=self.repo)

    def test_ranks_files_by_shared_commit_frequency(self) -> None:
        self._commit("c1", core="1\n", tight="1\n", loose="1\n")
        self._commit("c2", core="2\n", tight="2\n")           # core + tight
        self._commit("c3", core="3\n", tight="3\n")           # core + tight
        self._commit("c4", unrelated="1\n")                    # neither
        ranked = codegraph.co_changed_files(self.repo, {"core"}, lookback=50, cap=10, ignore_globs=())
        as_dict = dict(ranked)
        self.assertEqual(as_dict["tight"], 3)
        self.assertEqual(as_dict["loose"], 1)
        self.assertNotIn("unrelated", as_dict)
        self.assertEqual([p for p, _ in ranked][0], "tight")  # highest first


class BuildContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        run("git", "init", "-b", "main", cwd=self.repo)
        run("git", "config", "user.email", "t@e.com", cwd=self.repo)
        run("git", "config", "user.name", "t", cwd=self.repo)
        (self.repo / "core.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
        (self.repo / "user.py").write_text("from core import widget\nwidget()\n", encoding="utf-8")
        run("git", "add", "-A", cwd=self.repo)
        run("git", "commit", "-m", "base", cwd=self.repo)

    def test_build_and_render_surfaces_reference_with_provenance(self) -> None:
        chunk = "@@ -1 +1 @@\n-def widget():\n+def widget(scale):\n"
        context = codegraph.build_context(
            self.repo, [("core.py", chunk)], {"core.py"}, (),
            max_files=40, max_refs_per_symbol=20, co_change_lookback=200,
            enable_co_change=False, budget_bytes=64 * 1024,
        )
        self.assertIn("widget", context.symbols)
        section = codegraph.render_section(context)
        self.assertIn("## Codebase context", section)
        self.assertIn("references\twidget\tuser.py:2", section)
        self.assertIn("truncated: 0", section)

    def test_budget_truncates_and_reports(self) -> None:
        chunk = "@@ -1 +1 @@\n-def widget():\n+def widget(scale):\n"
        context = codegraph.build_context(
            self.repo, [("core.py", chunk)], {"core.py"}, (),
            max_files=40, max_refs_per_symbol=20, co_change_lookback=200,
            enable_co_change=False, budget_bytes=1,  # nothing fits after the first
        )
        self.assertGreater(context.truncated, 0)

    def test_empty_when_no_references(self) -> None:
        chunk = "@@ -1 +1 @@\n+def orphan_symbol_xyz():\n"
        context = codegraph.build_context(
            self.repo, [("core.py", chunk)], {"core.py"}, (),
            max_files=40, max_refs_per_symbol=20, co_change_lookback=200,
            enable_co_change=False, budget_bytes=64 * 1024,
        )
        self.assertEqual(context.entries, [])
        self.assertIn("no related code found", codegraph.render_section(context))


if __name__ == "__main__":
    unittest.main()
