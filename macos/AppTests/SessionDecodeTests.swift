import XCTest
@testable import cst

final class SessionDecodeTests: XCTestCase {
    func testDecodePayload() throws {
        let json = """
        {"schema":1,"version":"1.9.1",
         "counts":{"working":1,"waiting":0,"idle":0,"ended":1,"done":0},
         "sessions":[
           {"sessionId":"s1","shortId":"ab12","cwd":"/Users/x/proj","project":"proj",
            "status":"working","glyph":"●","isLive":true,"isDone":false,"messages":5,
            "summary":"hi","lastActivity":"2026-07-02T18:27:00+09:00","lastTs":1751449620,
            "gitBranch":"develop",
            "job":{"short":"ab12","template":"bg","branch":"feat","worktreePath":"/wt",
                   "state":"working","tempo":"active","alive":true},
            "prs":[{"host":"github","repo":"o/r","number":1,"url":"https://x/pull/1"}],
            "pinned":true}
         ]}
        """.data(using: .utf8)!
        let payload = try JSONDecoder().decode(SessionsPayload.self, from: json)
        XCTAssertEqual(payload.schema, 1)
        XCTAssertEqual(payload.sessions.count, 1)
        let s = payload.sessions[0]
        XCTAssertEqual(s.id, "s1")
        XCTAssertEqual(s.status, "working")
        XCTAssertTrue(s.isLive)
        XCTAssertEqual(s.job?.short, "ab12")
        XCTAssertTrue(s.job?.alive ?? false)
        XCTAssertEqual(s.prs.first?.number, 1)
        XCTAssertTrue(s.pinned)
    }
}
