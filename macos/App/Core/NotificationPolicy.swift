import Foundation

struct NotifPrefs {
    var onWaiting: Bool
    var onIdle: Bool
    var onDone: Bool
    var onBgFailed: Bool
    static let defaults = NotifPrefs(onWaiting: true, onIdle: true,
                                     onDone: false, onBgFailed: false)
}

struct NotifSpec: Equatable {
    let sessionId: String
    let title: String
    let body: String
}

enum NotificationPolicy {
    static func specs(for transitions: [Transition], prefs: NotifPrefs) -> [NotifSpec] {
        transitions.compactMap { tr in
            switch tr.to {
            case "waiting" where prefs.onWaiting:
                return NotifSpec(sessionId: tr.sessionId, title: "입력 대기",
                                 body: "\(tr.project) 세션이 입력을 기다립니다")
            case "idle" where prefs.onIdle && tr.from == "working":
                return NotifSpec(sessionId: tr.sessionId, title: "턴 완료",
                                 body: "\(tr.project) 세션이 유휴 상태가 되었습니다")
            case "done" where prefs.onDone:
                return NotifSpec(sessionId: tr.sessionId, title: "작업 종료",
                                 body: "\(tr.project) 세션이 done으로 표시됨")
            case "ended" where prefs.onBgFailed && tr.isJob:
                return NotifSpec(sessionId: tr.sessionId, title: "백그라운드 종료",
                                 body: "\(tr.project) bg 세션이 종료/실패했습니다")
            default:
                return nil
            }
        }
    }
}
