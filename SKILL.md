---
name: test-audit
description: >
  Perform a comprehensive audit of a project's test suite health. Detects test infrastructure, runs the suite, analyzes test quality for smells and anti-patterns, and generates a structured report with severity-rated findings. Use when the user wants to audit tests, review test quality, check test health, find flaky tests, identify test smells, assess test maintenance burden, troubleshoot slow tests, or evaluate their test setup. Also triggers on phrases like "test audit", "test maintenance", "my tests are a mess", "are my tests good", "review my test suite", "test quality report", "flaky test investigation", "slow test diagnosis", "how healthy are my tests".
---

# Test Audit Skill

## Overview

This skill produces a comprehensive test suite audit. It is **read-only** on production code — it reads test files and configs but never modifies them. Output is dual-mode:

1. **Inline summary** in the conversation for immediate understanding.
2. **Full report** saved as `test-audit-report.md` in the project root.

Language-agnostic with runtime discovery. Works across JS/TS, Python, Go, Ruby, Java, Rust, and any project with test files following common conventions.

---

## Phase 1: Detect Infrastructure

Run the detection script:

```bash
python <skill_path>/scripts/detect_tests.py <project_dir>
```

Parse the JSON output. It returns:

| Field | Description |
|---|---|
| `runners` | Detected test frameworks with config files and detection source |
| `configs` | All discovered config file paths |
| `test_files.count` | Total test files found |
| `test_files.paths` | Relative paths (capped at 100) |
| `suites` | Test suites grouped by directory, with file count |
| `ci_integration` | Boolean — CI config references test commands |
| `coverage_configured` | Boolean — coverage thresholds configured in runner config |

**If Python is unavailable**, fall back to manual detection:
- Use `grep` for `"vitest"`, `"jest"`, `"@playwright/test"` in `package.json`
- Use `glob` for `vitest.config.*`, `jest.config.*`, `playwright.config.*`
- Use `glob` for `**/*.spec.*`, `**/*.test.*`, `**/*_test.*`, `**/*_test.go`, `**/test_*.py`
- Check `.github/workflows/*.yml` for test commands
- Check config files for coverage keywords

**If multiple runners detected** or results are ambiguous, confirm with the user before proceeding.

**Display a brief summary**: runner name(s), config file(s), test count, CI status, coverage configuration.

**If no test infrastructure found** (exit code 2), report that to the user and suggest they ask about adding tests. Do not proceed further.

**Frameworks detected automatically**:

| Ecosystem | Frameworks |
|---|---|
| JS/TS | Vitest, Jest, Mocha, Playwright, Cypress |
| Python | Pytest (via config/conftest), unittest (imports) |
| Go | `go test` (`_test.go` files + go.mod) |
| Ruby | RSpec (`spec/`, `.rspec`), Minitest (`test/`) |
| Java | JUnit (pom.xml, build.gradle) |
| Rust | `cargo test` (Cargo.toml dev-dependencies) |
| Fallback | Generic `.spec.*`, `*_test.*`, `test_*`, `__tests__/` |

---

## Phase 2: Execute & Measure

Run the full test suite using the detected runner's standard command:

| Runner | Command |
|---|---|
| Vitest | `npx vitest run` or `npm run test` |
| Jest | `npx jest` or `npm test` |
| Playwright | `npx playwright test` |
| Cypress | `npx cypress run` |
| Pytest | `python -m pytest -v` or `pytest -v` |
| Go | `go test ./...` |
| RSpec | `bundle exec rspec` |
| Cargo | `cargo test` |
| Maven | `mvn test` |

**Capture**:
- Total pass / fail / skip count
- Total execution time
- Per-suite timing if the runner reports it

**If tests require env vars, database connections, or API keys** the skill cannot provide: note this limitation and continue with static analysis only. Do not fabricate credentials or skip config.

**If tests fail**: report the failure summary (failed suite names, error messages for the first 3 failures) but continue with the audit. The audit covers structure and quality even when the code is broken. The health score accounts for failures.

**If no runner detected** (no tests to run): skip this phase entirely, note "execution metrics unavailable" in the report, and proceed to Phase 3 with static analysis only.

---

## Phase 3: Surface Analysis

Scan file names, configs, and test structure without reading full test bodies. Check each of these dimensions:

### 3a. Naming Consistency
Check if test file naming follows a single convention. Conventions include:
- `.spec.ts` / `.spec.tsx` (Vitest/Jest)
- `.test.ts` / `.test.tsx`
- `_test.go` (Go)
- `test_*.py` (Python)
- `_spec.rb` (RSpec)
- `*_test.rb` (Minitest)

**Why this matters**: Mixed naming conventions create confusion about where to add new tests and what patterns to follow. A team standard that every file follows is more important than which specific convention is chosen.

**Flag**: Files using a different convention than the dominant one (>80% threshold). Mixed naming is a Warning.

### 3b. Organization
Determine whether tests are **colocated** with source code or in **separate directories**.

**Why this matters**: Colocation makes it easier to find tests for a module and encourages developers to write tests alongside code. Separate test directories are common in monorepos and are fine when consistently applied. The problem is inconsistency.

**Flag**: Inconsistent organization (some colocated, some in test directories) as Info. Entirely one approach or the other is fine.

### 3c. Assertion Density
Grep for assertions (`expect(`, `assert.`, `to_be`, `should`, `assertThat`) per test file. Calculate average and flag outliers.

**Why this matters**: A test file with 50+ assertions across many `it`/`test` blocks is fine (each test tests one thing). But a single test body with 10+ unrelated assertions indicates an "over-tester" — a test that verifies too many behaviors at once. Over-testers fail for multiple reasons simultaneously, making debugging harder.

**Flag**: Files with >10 assertions per `it`/`test`/`def test_` block as Warning.

### 3d. Duplicate Detection
Look for test files or `describe`/`context` blocks with identical or near-identical names testing the same module.

**Why this matters**: Duplicate tests add maintenance burden without increasing coverage. They often result from merge conflicts or parallel workstreams.

**Flag**: Identical test file base names in different locations as Warning. Identical `describe` block names within the same file as Critical.

### 3e. Config Health
Inspect test runner config for:
- **Timeouts**: Are they set to reasonable values? (Default Vitest/Jest timeout of 5000ms is fine for unit tests; integration/E2E should be higher)
- **Coverage**: Is coverage configured with thresholds? What thresholds?
- **Parallelization**: Is parallel execution enabled? (Vitest/Jest default to parallel; Pytest needs `-n auto` with pytest-xdist)
- **Transform/Ignore patterns**: Are there unusual transform exclusions that might hide broken tests?

**Why this matters**: Runner config is the foundation. Missing coverage thresholds means coverage can silently degrade. Disabled parallelization means slow CI. Overly aggressive transform exclusions can mask real issues.

**Flag**: Missing coverage config as Warning. Disabled parallelization as Info. Absurdly low timeouts as Info.

### 3f. Test Type Balance
Use directory names as heuristics to classify tests:

| Type | Heuristic (directory name) |
|---|---|
| E2E | `e2e/`, `cypress/`, `playwright/` |
| Integration | `integration/`, `integration-tests/` |
| Unit | Everything else |

Report the percentage breakdown.

**Why this matters**: An overabundance of E2E tests with few unit tests means slow feedback loops and fragile CI. An overabundance of unit tests with zero integration tests means confidence gaps in real-world behavior.

**Flag**: >50% E2E as Warning (slow feedback). <10% unit as Info (possible coverage gaps on edge cases).

---

## Phase 4: Deep Analysis

Sample test files intelligently. Do not read every file — that wastes tokens and distracts from the systematic issues.

### Sampling Strategy

1. **Priority files**: Files flagged by surface analysis (naming issues, high assertion density, duplicates).
2. **Coverage-based**: If coverage reporting is available, prioritize files with low coverage.
3. **Random sample**: Fill remaining slots from the full set, weighted toward larger test files (more likely to contain issues).
4. **Cap**: Maximum 10 files in deep analysis. Larger codebases with >100 test files cap at 10 and state this explicitly in the report.

### Code Smell Checks

For each sampled file, check these established patterns from Meszaros' xUnit Test Patterns:

#### Implementation Assertions
Tests asserting on internal state, private methods, or implementation details rather than observable behavior.

**Detection**: Look for assertions on private fields, `__`, `#` (JS private), `_` prefix methods, `.internal`, or assertions that check implementation steps rather than outcomes.

**Why it matters**: Implementation-asserting tests break on refactors that don't change behavior. They create a "change detector" effect where any code change — even a correct one — requires test updates. This undermines the safety net tests are supposed to provide.

**Severity**: Critical

#### Conditional Test Logic
`if`/`switch`/`for` statements inside `it`/`test`/`describe`/`context` bodies.

**Detection**: Grep for branching keywords inside test bodies.

**Why it matters**: Conditional tests execute different code paths on each run, making them unpredictable. The branch not exercised in CI may fail in production. Tests should be deterministic: given the same setup, always the same assertions.

**Severity**: Critical

#### Over-Testers
Single test body with too many unrelated assertions (>5 unrelated assertions).

**Detection**: Count unique assertion symbols per `it`/`test` block. More than 5 likely means multiple behaviors under test.

**Why it matters**: When an over-tester fails, you don't know which behavior broke. It also encourages adding more assertions rather than writing focused tests. Each test should verify one behavior.

**Severity**: Warning

#### Magic Numbers/Strings
Hard-coded values in assertions without named constants or fixtures.

**Detection**: Look for raw numbers and strings in assertion arguments that aren't assigned to a named variable first.

**Why it matters**: Magic values obscure *why* a particular value is expected. When the value needs to change, every assertion must be updated individually. Named constants express intent: `EXPECTED_USER_COUNT` vs `42`.

**Severity**: Warning

#### State Dependency
Tests that rely on shared state, global variables, module-level mutables, or test execution order.

**Detection**: Look for files with mutable module-level variables, test suites without proper setup/teardown, or tests that modify globals without restoring them.

**Why it matters**: State-dependent tests are order-dependent: they pass in isolation but fail in CI when reordered, or vice versa. This is the most common cause of flaky tests.

**Severity**: Critical

#### Mock Overuse
Every dependency mocked, even simple value objects or pure functions.

**Detection**: Count the mock/stub ratio relative to the number of dependencies used. If every import is mocked, that's a red flag.

**Why it matters**: Over-mocked tests become brittle tautologies — they test that the mock returns what the mock was configured to return. They break on any refactor, even behavior-preserving ones. Prefer real instances for value objects, pure functions, and stable dependencies. Mock only external I/O.

**Severity**: Warning

#### Meaningless Test Names
Test names like `test1`, `should work`, `it works`, `test_functionality`, `check`, `verify` — names that don't describe the behavior being tested.

**Detection**: Grep for single-word test names, generic patterns like `test_*` without a behavior description, or any name shorter than 10 characters (excluding framework-provided descriptions).

**Why it matters**: A test name is the first — and often only — documentation of what behavior is expected. When a test named `test1` fails, the developer must read the test body to understand what broke. A well-named test like `handles_empty_cart_on_checkout` communicates intent immediately.

**Severity**: Warning (Critical if systematically every name is meaningless)

#### Test-Specific Code Duplication
Same setup logic repeated across multiple test files without helper extraction.

**Detection**: Compare factory/setup blocks across sampled files. Look for identical fixture creation, identical mock setups, or same multi-line arrange blocks.

**Why it matters**: Duplicated setup means changes to the object under test require N edits across N test files. This is both a maintenance burden and a source of bugs when not all copies are updated consistently. Extract helpers, fixtures, or shared factories.

**Severity**: Warning

---

## Phase 5: Health Score & Report

### Health Score Formula (0-10)

Calculate the score from four weighted dimensions:

| Dimension | Weight | Scoring |
|---|---|---|
| Execution success | 30% (3 pts) | All pass = 3/3, partial failures <50% = 2/3, >50% fail or can't run = 0/3 |
| No critical smells | 30% (3 pts) | 0 critical = 3/3, 1-2 = 2/3, 3-5 = 1/3, 5+ = 0/3 |
| Config completeness | 20% (2 pts) | Has config + coverage + CI = 2/2, missing one = 1.5/2, missing two+ = 1/2 |
| Naming & organization | 20% (2 pts) | Consistent naming + good structure = 2/2, issues found = 1/2, major issues = 0.5/2 |

Round to one decimal place.

### Report Template

Generate the report using this exact structure and save it as `test-audit-report.md` in the project root:

```markdown
# Test Suite Audit Report

## Executive Summary
**Health Score**: X/10 — [one-line assessment]
**Critical**: N | **Warning**: N | **Info**: N

## Infrastructure
- **Runner**: [name + version if detectable]
- **Config**: [paths]
- **Total test files**: N across N suites
- **CI integration**: Yes/No
- **Coverage configured**: Yes/No

## Execution Health
- **Total time**: Xs
- **Pass rate**: X% (N passed, N failed, N skipped)
- **Slowest suites**: [top 3 — or "not available" if couldn't run]

## Quality Findings

| Severity | Finding | Location | Detail |
|----------|---------|----------|--------|
| Critical | [finding] | [file:line or suite] | [why it matters + concrete example] |
| Warning | [finding] | [file:line or suite] | [why it matters + concrete example] |
| Info | [finding] | [file:line or suite] | [why it matters + concrete example] |

## Maintainability Metrics
- Test/production code ratio: X:1
- Avg assertions per test file: X
- Naming consistency: [consistent / mixed / inconsistent]
- Top test types: [unit: X%, integration: X%, E2E: X%]
- **Note on sampling**: Deep analysis sampled up to 10 test files. Findings represent sample quality, not a full inventory of every issue.

## Recommendations (prioritized)
1. **[CRITICAL]** ...
2. **[WARNING]** ...
3. **[INFO]** ...
```

### Inline Summary Format

In the conversation, provide a concise summary:

```
**Test Suite Audit**: [project name]

Score: X/10 — [one-line assessment]
Runners: [name] | Tests: N files | Pass rate: X%
Findings: N critical, N warning, N info

Top recommendations:
1. [CRITICAL] ...
2. [CRITICAL] ...
3. [WARNING] ...

Full report saved to `test-audit-report.md`.
```

---

## Behavioral Rules

1. **Explain WHY**, not just what. Every check includes an explanation of why it matters so the user understands the impact.
2. **No fabrication**. If tests can't run, say so. If coverage isn't available, note it. Never make up metrics.
3. **State sampling limits explicitly**. Deep analysis always mentions its cap of 10 files.
4. **Imperative tone**. "Run", "Detect", "Sample", "Generate", "Check" — not "this will run" or "we should check".
5. **Prioritize findings by severity**. Critical findings first in recommendations. A single critical finding is more important than ten info items.
6. **Document the formula**. The inline health score formula is documented above so it's applied consistently across runs.
