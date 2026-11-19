"""Behavior tests for the detect_tests.py interface (stdlib unittest only).

These tests pin the module's interface — the JSON-shaped detection results
each detect_* function returns, plus the CLI's exit-code contract — so
refactors of the implementation keep behaviour stable.

Run with:  python3 -m unittest discover -s tests
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "detect_tests.py"

_spec = importlib.util.spec_from_file_location("detect_tests", SCRIPT)
assert _spec is not None and _spec.loader is not None
detect_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(detect_tests)


def make_project(files: dict) -> Path:
    """Create a throwaway project tree from {relative_path: content}."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _TMP_DIRS.append(tmp)
    return root


_TMP_DIRS: list = []


def tearDownModule():
    for tmp in _TMP_DIRS:
        tmp.cleanup()


class JsTsRunnerDetection(unittest.TestCase):
    def test_vitest_detected_from_dev_dependency(self):
        root = make_project({
            "package.json": json.dumps({"devDependencies": {"vitest": "^1.0.0"}}),
        })
        runners = detect_tests.detect_js_ts_runner(root)
        self.assertEqual([r["name"] for r in runners], ["vitest"])
        self.assertEqual(runners[0]["source"], "dependencies")

    def test_jest_detected_from_config_file_only(self):
        root = make_project({
            "package.json": json.dumps({"name": "x"}),
            "jest.config.js": "export default {};",
        })
        runners = detect_tests.detect_js_ts_runner(root)
        self.assertEqual([r["name"] for r in runners], ["jest"])
        self.assertEqual(runners[0]["source"], "config_file")
        self.assertEqual(runners[0]["config_files"], ["jest.config.js"])

    def test_cypress_config_not_duplicated(self):
        root = make_project({
            "package.json": json.dumps({"devDependencies": {"cypress": "^13"}}),
            "cypress.config.js": "export default {};",
        })
        runners = detect_tests.detect_js_ts_runner(root)
        self.assertEqual([r["name"] for r in runners], ["cypress"])
        self.assertEqual(runners[0]["config_files"], ["cypress.config.js"])

    def test_no_package_json_yields_no_runners(self):
        root = make_project({"README.md": "hi"})
        self.assertEqual(detect_tests.detect_js_ts_runner(root), [])

    def test_invalid_package_json_is_ignored(self):
        root = make_project({"package.json": "{not json"})
        self.assertEqual(detect_tests.detect_js_ts_runner(root), [])

    def test_monorepo_package_dependency_detected(self):
        root = make_project({
            "nx.json": "{}",
            "package.json": json.dumps({"name": "mono"}),
            "apps/web/package.json": json.dumps({"devDependencies": {"vitest": "^1.0.0"}}),
        })
        runners = detect_tests.detect_js_ts_runner(root)
        self.assertEqual([r["name"] for r in runners], ["vitest"])
        self.assertEqual(runners[0]["source"], "monorepo_deps")


class PythonRunnerDetection(unittest.TestCase):
    def test_pytest_via_pyproject(self):
        root = make_project({"pyproject.toml": "[tool.pytest.ini_options]\n"})
        runners = detect_tests.detect_python_runner(root)
        self.assertEqual([r["name"] for r in runners], ["pytest"])
        self.assertEqual(runners[0]["source"], "pyproject")

    def test_pytest_via_conftest(self):
        root = make_project({"conftest.py": ""})
        runners = detect_tests.detect_python_runner(root)
        self.assertEqual([r["name"] for r in runners], ["pytest"])
        self.assertEqual(runners[0]["source"], "conftest")

    def test_unittest_via_imports(self):
        root = make_project({"app.py": "import unittest\n"})
        runners = detect_tests.detect_python_runner(root)
        self.assertEqual([r["name"] for r in runners], ["unittest"])
        self.assertEqual(runners[0]["source"], "imports")

    def test_empty_project_yields_nothing(self):
        root = make_project({"main.py": "print('hi')\n"})
        self.assertEqual(detect_tests.detect_python_runner(root), [])


class OtherRunnerDetection(unittest.TestCase):
    def test_go_test_files(self):
        root = make_project({"foo_test.go": "package foo\n", "go.mod": "module foo\n"})
        runners = detect_tests.detect_go_runner(root)
        self.assertEqual([r["name"] for r in runners], ["go_test"])

    def test_ruby_rspec_via_spec_dir(self):
        root = make_project({"spec/foo_spec.rb": ""})
        runners = detect_tests.detect_ruby_runner(root)
        self.assertEqual([r["name"] for r in runners], ["rspec"])

    def test_java_junit_via_pom(self):
        root = make_project({"pom.xml": "<dependencies><junit/></dependencies>"})
        runners = detect_tests.detect_java_runner(root)
        self.assertEqual([r["name"] for r in runners], ["junit"])

    def test_rust_cargo_dev_dependencies(self):
        root = make_project({"Cargo.toml": "[dev-dependencies]\n"})
        runners = detect_tests.detect_rust_runner(root)
        self.assertEqual([r["name"] for r in runners], ["cargo_test"])


class TestFileDetection(unittest.TestCase):
    def test_finds_test_files_and_excludes_vendor_dirs(self):
        root = make_project({
            "src/foo.spec.ts": "",
            "tests/test_a.py": "",
            "node_modules/pkg/bar.test.js": "",
        })
        result = detect_tests.detect_test_files(root)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["paths"], ["src/foo.spec.ts", "tests/test_a.py"])

    def test_suites_group_by_top_level_dir(self):
        root = make_project({
            "tests/test_a.py": "",
            "tests/test_b.py": "",
            "spec/x.spec.js": "",
        })
        result = detect_tests.detect_test_files(root)
        counts = {s["name"]: s["file_count"] for s in result["suites"]}
        self.assertEqual(counts.get("tests"), 2)


class CiAndCoverage(unittest.TestCase):
    def test_ci_integration_detected(self):
        root = make_project({
            ".github/workflows/ci.yml": "jobs:\n  build:\n    steps:\n      - run: npm test\n",
        })
        self.assertTrue(detect_tests.check_ci_integration(root))

    def test_ci_without_test_keywords(self):
        root = make_project({
            ".github/workflows/ci.yml": "jobs:\n  build:\n    steps:\n      - run: npm run build\n",
        })
        self.assertFalse(detect_tests.check_ci_integration(root))

    def test_coverage_via_package_script(self):
        root = make_project({
            "package.json": json.dumps({"scripts": {"test": "vitest run --coverage"}}),
        })
        self.assertTrue(detect_tests.check_coverage_configured(root, []))


class CliInterface(unittest.TestCase):
    def test_json_output_and_zero_exit_on_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"devDependencies": {"vitest": "^1"}})
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(root)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertEqual([r["name"] for r in payload["runners"]], ["vitest"])
            self.assertFalse(payload["ci_integration"])

    def test_exit_2_when_nothing_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "README.md").write_text("docs only\n")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), tmp],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 2)

    def test_exit_1_on_missing_directory(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "/nonexistent/path/xyz"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error", json.loads(proc.stdout))


if __name__ == "__main__":
    unittest.main()
