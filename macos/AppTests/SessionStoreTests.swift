import XCTest
@testable import cst

private final class MockSource: CstDataSource {
    var snapshots: [[Session]]
    private var idx = 0
    init(_ snapshots: [[Session]]) { self.snapshots = snapshots }
    func list(limit: Int?) async throws -> [Session] {
        defer { idx = min(idx + 1, snapshots.count - 1) }
        return snapshots[min(idx, snapshots.count - 1)]
    }
    func preview(_ id: String, maxChars: Int) async throws -> String { "preview" }
    func resume(_ id: String) async throws {}
    func markDone(_ id: String) async throws {}
    func markUndone(_ id: String) async throws {}
}

private func mk(_ id: String, _ status: String) -> Session {
    Session(sessionId: id, shortId: nil, cwd: "/p", project: id, status: status,
            glyph: "?", isLive: true, isDone: false, messages: 0, summary: "",
            lastActivity: nil, lastTs: 0, gitBranch: "", job: nil, prs: [], pinned: false)
}

@MainActor
final class SessionStoreTests: XCTestCase {
    func testRefreshPublishesAndDetectsTransitions() async {
        let src = MockSource([[mk("a", "working")], [mk("a", "waiting")]])
        let store = SessionStore(source: src)
        var captured: [Transition] = []
        store.onTransitions = { captured += $0 }

        await store.refreshOnce()                 // first snapshot: new session a->working
        XCTAssertEqual(store.sessions.count, 1)
        XCTAssertEqual(captured.map(\.to), ["working"])

        await store.refreshOnce()                 // second: working -> waiting
        XCTAssertEqual(captured.map(\.to), ["working", "waiting"])
        XCTAssertEqual(store.sessions.first?.status, "waiting")
    }
}
