import XCTest
@testable import cst

final class DiffEngineTests: XCTestCase {
    private func s(_ id: String, _ status: String, job: Bool = false) -> Session {
        Session(sessionId: id, shortId: job ? "x" : nil, cwd: "/p/\(id)", project: id,
                status: status, glyph: "?", isLive: true, isDone: status == "done",
                messages: 1, summary: "", lastActivity: nil, lastTs: 0, gitBranch: "",
                job: job ? .init(short: "x", template: "bg", branch: "", worktreePath: "",
                                 state: status, tempo: "active", alive: true) : nil,
                prs: [], pinned: false)
    }

    func testNoChangeYieldsNothing() {
        let prev = [s("a", "idle")], cur = [s("a", "idle")]
        XCTAssertTrue(DiffEngine.transitions(previous: prev, current: cur).isEmpty)
    }

    func testStatusChangeDetected() {
        let prev = [s("a", "working")], cur = [s("a", "waiting")]
        let t = DiffEngine.transitions(previous: prev, current: cur)
        XCTAssertEqual(t.count, 1)
        XCTAssertEqual(t[0].from, "working")
        XCTAssertEqual(t[0].to, "waiting")
        XCTAssertEqual(t[0].sessionId, "a")
    }

    func testNewSessionHasNilFrom() {
        let t = DiffEngine.transitions(previous: [], current: [s("a", "working")])
        XCTAssertEqual(t.count, 1)
        XCTAssertNil(t[0].from)
        XCTAssertEqual(t[0].to, "working")
    }

    func testJobFlagPropagates() {
        let t = DiffEngine.transitions(previous: [s("a", "working", job: true)],
                                       current: [s("a", "ended", job: true)])
        XCTAssertTrue(t[0].isJob)
    }
}
