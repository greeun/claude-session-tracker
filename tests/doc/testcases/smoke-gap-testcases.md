# Smoke — Gap Test Cases

| ID | Scenario | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-SMOKE-101 | SC-SMOKE-101 | 버전 드리프트 없음 | `tracker.__version__`, `SKILL.md` frontmatter | 두 값이 동일 | Medium |
| TC-SMOKE-102 | SC-SMOKE-101 | SemVer 형태 | `tracker.__version__` | `^\d+\.\d+\.\d+$` 매칭 | Low |
