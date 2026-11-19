#!/usr/bin/env python3
"""
test-audit — Lightweight test infrastructure scanner.

Scans a project directory and returns JSON describing detected test runners,
config files, test files, suites, CI integration, and coverage configuration.

Usage:
    python detect_tests.py [project_dir]

Returns structured JSON to stdout. Exits 0 on success (even if no tests found),
non-zero on error.
"""

import json
import re
import sys
from pathlib import Path


def _dedupe(paths: list[str]) -> list[str]:
    """Drop duplicate config paths (glob + explicit list can both match)."""
    seen = set()
    unique = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def detect_js_ts_runner(project_dir: Path) -> list[dict]:
    """Detect JavaScript/TypeScript test runners from config files and deps."""
    runners = []
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        return runners

    try:
        pkg = json.loads(pkg_json.read_text())
    except (json.JSONDecodeError, OSError):
        return runners

    # Collect all dependencies and devDependencies
    deps = {}
    for section in ("dependencies", "devDependencies"):
        deps.update(pkg.get(section, {}))

    runner_map = {
        "vitest": {"name": "vitest", "config_files": [
            str(f.relative_to(project_dir))
            for f in project_dir.glob("vitest.config.*")
        ]},
        "jest": {"name": "jest", "config_files": [
            str(f.relative_to(project_dir))
            for f in project_dir.glob("jest.config.*")
        ] + [
            str(f.relative_to(project_dir))
            for f in [project_dir / "jest.config.js", project_dir / "jest.config.ts",
                       project_dir / "jest.config.mjs", project_dir / "jest.config.cjs"]
            if f.exists()
        ]},
        "mocha": {"name": "mocha", "config_files": [
            str(f.relative_to(project_dir))
            for f in [project_dir / ".mocharc.js", project_dir / ".mocharc.json",
                       project_dir / ".mocharc.yml"]
            if f.exists()
        ]},
        "@playwright/test": {"name": "playwright", "config_files": [
            str(f.relative_to(project_dir))
            for f in project_dir.glob("playwright.config.*")
        ]},
        "cypress": {"name": "cypress", "config_files": [
            str(f.relative_to(project_dir))
            for f in [project_dir / "cypress.config.js", project_dir / "cypress.config.ts",
                       project_dir / "cypress.config.mjs", project_dir / "cypress.config.cjs"]
            if f.exists()
        ] + ([
            str(f.relative_to(project_dir))
            for f in project_dir.glob("cypress.config.*")
        ] if list(project_dir.glob("cypress.config.*")) else [])},
    }

    for dep_key, info in runner_map.items():
        if dep_key in deps:
            runners.append({
                "name": info["name"],
                "config_files": _dedupe(info["config_files"]),
                "source": "dependencies",
            })
        elif info["config_files"]:
            runners.append({
                "name": info["name"],
                "config_files": _dedupe(info["config_files"]),
                "source": "config_file",
            })

    # Check for nx.json or turbo.json as hint for monorepo runners
    for conf in ("nx.json", "turbo.json"):
        if (project_dir / conf).exists():
            # Monorepo — check for individual package configs
            pkg_dirs = list(project_dir.glob("packages/*/package.json"))
            pkg_dirs += list(project_dir.glob("apps/*/package.json"))
            for pkg_path in pkg_dirs:
                try:
                    pkg_data = json.loads(pkg_path.read_text())
                    pkg_deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                    for dep_key in ("vitest", "jest", "mocha"):
                        if dep_key in pkg_deps:
                            existing = any(r["name"] == dep_key for r in runners)
                            if not existing:
                                runners.append({
                                    "name": dep_key,
                                    "config_files": [],
                                    "source": "monorepo_deps",
                                })
                except (json.JSONDecodeError, OSError):
                    continue

    # Deduplicate by name
    seen = set()
    unique = []
    for r in runners:
        if r["name"] not in seen:
            seen.add(r["name"])
            unique.append(r)

    return unique


def detect_python_runner(project_dir: Path) -> list[dict]:
    """Detect Python test runners."""
    runners = []

    # Pytest: pyproject.toml [tool.pytest], pytest.ini, conftest.py, setup.cfg
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text()
        if "[tool.pytest" in text:
            runners.append({"name": "pytest", "config_files": [
                str(pyproject.relative_to(project_dir))
            ], "source": "pyproject"})

    for cfg in ("pytest.ini", "setup.cfg"):
        path = project_dir / cfg
        if path.exists():
            text = path.read_text()
            if "[pytest]" in text:
                runners.append({"name": "pytest", "config_files": [
                    str(path.relative_to(project_dir))
                ], "source": "config_file"})

    if (project_dir / "conftest.py").exists():
        existing = any(r["name"] == "pytest" for r in runners)
        if not existing:
            runners.append({"name": "pytest", "config_files": [
                "conftest.py"
            ], "source": "conftest"})

    # Pytest in tox.ini
    tox_ini = project_dir / "tox.ini"
    if tox_ini.exists() and "[testenv]" in tox_ini.read_text() and "pytest" in tox_ini.read_text():
        existing = any(r["name"] == "pytest" for r in runners)
        if not existing:
            runners.append({"name": "pytest", "config_files": [
                "tox.ini"
            ], "source": "tox"})

    # Check for unittest imports in Python files
    py_files = list(project_dir.rglob("*.py"))
    for py_file in py_files[:50]:  # Sample up to 50 files
        try:
            text = py_file.read_text()
            if "import unittest" in text or "from unittest" in text:
                existing = any(r["name"] == "unittest" for r in runners)
                if not existing:
                    runners.append({
                        "name": "unittest",
                        "config_files": [],
                        "source": "imports",
                    })
                break
        except (OSError, UnicodeDecodeError):
            continue

    return runners


def detect_go_runner(project_dir: Path) -> list[dict]:
    """Detect Go test runner."""
    test_files = list(project_dir.rglob("*_test.go"))
    if test_files:
        return [{
            "name": "go_test",
            "config_files": ["go.mod" if (project_dir / "go.mod").exists() else ""],
            "source": "test_files",
        }]
    return []


def detect_ruby_runner(project_dir: Path) -> list[dict]:
    """Detect Ruby test runners."""
    runners = []

    # RSpec: spec/ dir, .rspec file
    if (project_dir / "spec").is_dir() or (project_dir / ".rspec").exists():
        configs = []
        if (project_dir / ".rspec").exists():
            configs.append(".rspec")
        runners.append({
            "name": "rspec",
            "config_files": configs,
            "source": "directory_or_config",
        })

    # Minitest: test/ dir
    if (project_dir / "test").is_dir() and list(project_dir.rglob("*_test.rb")):
        existing = any(r["name"] in ("rspec", "minitest") for r in runners)
        if not existing:
            runners.append({
                "name": "minitest",
                "config_files": [],
                "source": "test_directory",
            })

    return runners


def detect_java_runner(project_dir: Path) -> list[dict]:
    """Detect Java test runners (JUnit)."""
    # Check pom.xml for junit
    pom = project_dir / "pom.xml"
    if pom.exists():
        text = pom.read_text()
        if "junit" in text.lower():
            return [{
                "name": "junit",
                "config_files": ["pom.xml"],
                "source": "pom_xml",
            }]

    # Check build.gradle for junit
    for gradle_file in (project_dir / "build.gradle", project_dir / "build.gradle.kts"):
        if gradle_file.exists() and "junit" in gradle_file.read_text().lower():
            return [{
                "name": "junit",
                "config_files": [str(gradle_file.relative_to(project_dir))],
                "source": "gradle",
            }]

    return []


def detect_rust_runner(project_dir: Path) -> list[dict]:
    """Detect Rust test runner (cargo test)."""
    cargo = project_dir / "Cargo.toml"
    if cargo.exists():
        text = cargo.read_text()
        if "[dev-dependencies]" in text:
            return [{
                "name": "cargo_test",
                "config_files": ["Cargo.toml"],
                "source": "cargo_toml",
            }]
    return []


def detect_test_files(project_dir: Path) -> dict:
    """Detect test files across all known conventions."""
    patterns = [
        # JS/TS spec/test files
        "**/*.spec.*", "**/*.test.*",
        # Underscore-prefix convention
        "**/test_*", "**/*_test.*",
        # Test directories
        "**/__tests__/**", "**/test/**/*", "**/tests/**/*", "**/spec/**/*",
        # Python test files
        "**/*_test.py", "**/test_*.py",
        # Go test files
        "**/*_test.go",
    ]

    all_files = []
    seen = set()
    for pattern in patterns:
        for f in project_dir.glob(pattern):
            if not f.is_file():
                continue
            rel = str(f.relative_to(project_dir))
            if rel not in seen:
                seen.add(rel)
                all_files.append(rel)

    # Exclude node_modules, .git, __pycache__, vendor
    exclude_dirs = {"node_modules", ".git", "__pycache__", "vendor", ".venv", "venv", ".tox", "target"}
    all_files = [
        f for f in all_files
        if not any(f"/{exc}/" in f"/{f}/" or f.startswith(f"{exc}/") for exc in exclude_dirs)
    ]

    all_files.sort()
    capped = all_files[:100]

    # Group into suites
    suites = {}
    for f in capped:
        # Determine suite name: top-level test directory or first path component
        parts = Path(f).parts
        suite_name = parts[0] if len(parts) > 1 else "root"
        if suite_name in ("__tests__", "test", "tests", "spec", "e2e", "cypress", "integration"):
            suite_name = parts[1] if len(parts) > 2 else suite_name
        if suite_name not in suites:
            suites[suite_name] = {"name": suite_name, "path": suite_name, "file_count": 0}
        suites[suite_name]["file_count"] += 1

    return {
        "count": len(all_files),
        "paths": capped,
        "suites": sorted(
            [{"name": k, "path": v["path"], "file_count": v["file_count"]}
             for k, v in suites.items()],
            key=lambda s: -s["file_count"],
        ),
    }


def check_ci_integration(project_dir: Path) -> bool:
    """Check if any CI config references running tests."""
    ci_patterns = [
        (".github/workflows", "*.yml"),
        (".github/workflows", "*.yaml"),
        (".gitlab-ci.yml", None),
        ("Jenkinsfile", None),
        (".circleci", "*.yml"),
        (".circleci", "*.yaml"),
        ("azure-pipelines.yml", None),
        (".travis.yml", None),
        ("appveyor.yml", None),
        ("bitbucket-pipelines.yml", None),
    ]

    test_keywords = ["test", "spec", "pytest", "vitest", "jest", "rspec",
                     "cargo test", "go test", "run tests", "npm test", "mvn test"]

    for dir_or_file, glob_pattern in ci_patterns:
        path = project_dir / dir_or_file
        if not path.exists():
            continue

        files = []
        if path.is_file():
            files.append(path)
        elif glob_pattern:
            files.extend(path.glob(glob_pattern))
        else:
            continue

        for ci_file in files:
            try:
                text = ci_file.read_text().lower()
                if any(kw in text for kw in test_keywords):
                    return True
            except (OSError, UnicodeDecodeError):
                continue

    return False


def check_coverage_configured(project_dir: Path, runners: list[dict]) -> bool:
    """Check if code coverage is configured in any test runner config."""
    runner_names = [r["name"] for r in runners]

    # Vitest: coverage in vitest.config.*
    for cfg in project_dir.glob("vitest.config.*"):
        try:
            text = cfg.read_text().lower()
            if "coverage" in text or "cover" in text:
                return True
        except OSError:
            continue

    # Jest: coverage in jest.config.* or package.json
    for cfg in project_dir.glob("jest.config.*"):
        try:
            text = cfg.read_text().lower()
            if "coverage" in text or "collectcoverage" in text:
                return True
        except OSError:
            continue

    pkg_json = project_dir / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            jest_config = pkg.get("jest", {})
            if isinstance(jest_config, dict) and ("coverageThreshold" in jest_config or "collectCoverage" in jest_config):
                return True
            scripts = pkg.get("scripts", {})
            for script_cmd in scripts.values():
                if "coverage" in script_cmd.lower():
                    return True
        except (json.JSONDecodeError, OSError):
            pass

    # Pytest: coverage in pyproject.toml, setup.cfg, pytest.ini, .coveragerc
    for cfg_file in ("pyproject.toml", "setup.cfg", "pytest.ini", ".coveragerc", "tox.ini"):
        path = project_dir / cfg_file
        if path.exists():
            try:
                text = path.read_text().lower()
                if "coverage" in text or ".coveragerc" in text:
                    return True
            except (OSError, UnicodeDecodeError):
                continue

    # Go: coverprofile or -cover in Makefile/scripts
    makefile = project_dir / "Makefile"
    if makefile.exists() and "cover" in makefile.read_text().lower():
        return True

    # Rust: tarpaulin or llvm-cov in config
    cargo = project_dir / "Cargo.toml"
    if cargo.exists() and ("tarpaulin" in cargo.read_text().lower() or "llvm-cov" in cargo.read_text().lower()):
        return True

    return False


def main():
    project_dir_str = sys.argv[1] if len(sys.argv) > 1 else "."
    project_dir = Path(project_dir_str).resolve()

    if not project_dir.is_dir():
        print(json.dumps({"error": f"Directory not found: {project_dir}"}))
        sys.exit(1)

    runners = []
    runners.extend(detect_js_ts_runner(project_dir))
    runners.extend(detect_python_runner(project_dir))
    runners.extend(detect_go_runner(project_dir))
    runners.extend(detect_ruby_runner(project_dir))
    runners.extend(detect_java_runner(project_dir))
    runners.extend(detect_rust_runner(project_dir))

    test_files = detect_test_files(project_dir)
    ci_integration = check_ci_integration(project_dir)
    coverage_configured = check_coverage_configured(project_dir, runners)

    # Collect config files from all runners
    all_configs = set()
    for r in runners:
        for cfg in r.get("config_files", []):
            if cfg:
                all_configs.add(cfg)

    result = {
        "runners": runners,
        "configs": sorted(all_configs),
        "test_files": test_files,
        "suites": test_files["suites"],
        "ci_integration": ci_integration,
        "coverage_configured": coverage_configured,
    }

    print(json.dumps(result, indent=2))

    # Exit non-zero only if we couldn't detect anything AND found no test files
    if not runners and test_files["count"] == 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
