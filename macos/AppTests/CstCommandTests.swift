import XCTest
@testable import cst

final class CstCommandTests: XCTestCase {
    func testListArgs() {
        XCTAssertEqual(CstCommand.listArgs(limit: 30, days: nil, cwd: nil,
                                           status: nil, sort: nil),
                       ["list", "--json", "--limit", "30"])
        XCTAssertEqual(CstCommand.listArgs(limit: nil, days: 7, cwd: "/p",
                                           status: "waiting", sort: "time"),
                       ["list", "--json", "--days", "7", "--cwd", "/p",
                        "--status", "waiting", "--sort", "time"])
    }

    func testActionArgs() {
        XCTAssertEqual(CstCommand.resumeArgs("s1"), ["resume", "s1", "--spawn"])
        XCTAssertEqual(CstCommand.doneArgs("s1"), ["done", "s1"])
        XCTAssertEqual(CstCommand.undoneArgs("s1"), ["undone", "s1"])
        XCTAssertEqual(CstCommand.showArgs("s1", maxChars: 4000),
                       ["show", "s1", "--max-chars", "4000"])
    }
}
