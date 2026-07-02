import Foundation

struct Transition: Equatable {
    let sessionId: String
    let project: String
    let from: String?      // nil이면 신규 세션
    let to: String
    let isJob: Bool
}

enum DiffEngine {
    /// status가 바뀐(또는 새로 등장한) 세션만 Transition으로 반환.
    static func transitions(previous: [Session], current: [Session]) -> [Transition] {
        let prev = Dictionary(previous.map { ($0.sessionId, $0) },
                              uniquingKeysWith: { a, _ in a })
        var out: [Transition] = []
        for s in current {
            let old = prev[s.sessionId]
            if old?.status != s.status {
                out.append(Transition(sessionId: s.sessionId, project: s.project,
                                      from: old?.status, to: s.status,
                                      isJob: s.job != nil))
            }
        }
        return out
    }
}
