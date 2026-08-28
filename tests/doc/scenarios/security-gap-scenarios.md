# Security — Gap Scenarios

웹 앱이 아니므로 OWASP Top 10 중 실재하는 표면만 다룬다. 관련 항목:
**A01 Broken Access Control**(경로 탈출), **A03 Injection**, **A08 Software and Data
Integrity Failures**(신뢰할 수 없는 아카이브 역직렬화).

Layer Ownership: `_applescript_escape`(순수 함수)는 **unit**이 소유하고,
셸 인용은 `test_bg_actions.py:48` / `test_open_folder.py`가 소유한다. 여기서는
명령 계층에만 존재하는 표면을 다룬다.

## SC-SEC-101 — 아카이브 경로 탈출 (A01/A08, Zip-Slip 계열)
- **Objective**: `cst restore`가 `projects/` 접두를 가진 채 상위로 탈출하는 tar 멤버를
  거부하고, `PROJECTS_DIR` 밖에 아무것도 쓰지 않는다.
- **Preconditions**: `PROJECTS_DIR`를 tempdir로 주입. 악성 tar을 직접 제작.
- **Steps**: 멤버 이름 `projects/../../pwned.jsonl` 을 담은 tar.gz로 restore 실행.
- **Expected**: 해당 멤버를 건너뛰고 stderr에 `Skipping unsafe path outside`,
  `PROJECTS_DIR` 밖 대상 경로는 **생성되지 않음**, rc == 1 (unsafe가 있으므로).
- **Priority**: Critical

## SC-SEC-102 — 심볼릭 링크 경유 탈출 (A01)
- **Objective**: 멤버 이름 자체는 정상이어도, 목적지 상위 경로가 `PROJECTS_DIR` 밖을
  가리키는 심볼릭 링크일 때 가드가 `realpath` 해석으로 잡아낸다.
- **Preconditions**: `PROJECTS_DIR/evil` → 외부 tempdir 심볼릭 링크를 미리 생성.
- **Steps**: 멤버 `projects/evil/x.jsonl` 로 restore 실행.
- **Expected**: unsafe로 거부, 링크 대상 디렉터리에 파일이 생기지 않음.
- **Priority**: Critical
