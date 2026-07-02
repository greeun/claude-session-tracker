import XCTest
@testable import cst

final class NotificationPolicyTests: XCTestCase {
    private func t(_ from: String?, _ to: String, job: Bool = false) -> Transition {
        Transition(sessionId: "a", project: "proj", from: from, to: to, isJob: job)
    }

    func testWaitingNotifiesByDefault() {
        let specs = NotificationPolicy.specs(for: [t("working", "waiting")],
                                             prefs: .defaults)
        XCTAssertEqual(specs.count, 1)
        XCTAssertEqual(specs[0].sessionId, "a")
    }

    func testIdleOnlyFromWorking() {
        XCTAssertEqual(NotificationPolicy.specs(for: [t("working", "idle")],
                                                prefs: .defaults).count, 1)
        XCTAssertEqual(NotificationPolicy.specs(for: [t("waiting", "idle")],
                                                prefs: .defaults).count, 0)
    }

    func testDoneOffByDefault() {
        XCTAssertTrue(NotificationPolicy.specs(for: [t("idle", "done")],
                                               prefs: .defaults).isEmpty)
    }

    func testBgFailedRequiresJobAndOptIn() {
        var prefs = NotifPrefs.defaults
        prefs.onBgFailed = true
        XCTAssertEqual(NotificationPolicy.specs(for: [t("working", "ended", job: true)],
                                                prefs: prefs).count, 1)
        XCTAssertEqual(NotificationPolicy.specs(for: [t("working", "ended", job: false)],
                                                prefs: prefs).count, 0)
    }
}
