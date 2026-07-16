# test-audit

A language-agnostic test suite audit skill for Claude. Runs automatic test infrastructure detection, executes the suite, analyzes test quality for smells and anti-patterns, and generates a structured audit report with severity-rated findings.

## Installation

```bash
npx skill add gtrias/test-audit
```

## Usage

Invoke by asking Claude to audit your test suite. The skill works across JS/TS, Python, Go, Ruby, Java, and Rust projects.

Example prompts:
- "Audit my test suite and give me a health report"
- "Are my tests any good? Run a full test audit"
- "Find flaky tests and slow suites"
- "How healthy are our tests before the refactor?"

## Output

The skill produces two outputs:
1. An **inline summary** in the conversation
2. A **full report** saved as `test-audit-report.md` in the project root

## Requirements

- Python 3 for the infrastructure detection script
- The project's own test runner dependencies (for execution metrics)

## License

MIT
