import Foundation

final class AppConfig: ObservableObject {
    static let shared = AppConfig()
    private let d = UserDefaults.standard

    @Published var cstPath: String { didSet { d.set(cstPath, forKey: "cstPath") } }
    @Published var pollInterval: Double { didSet { d.set(pollInterval, forKey: "pollInterval") } }
    @Published var notifWaiting: Bool { didSet { d.set(notifWaiting, forKey: "notifWaiting") } }
    @Published var notifIdle: Bool { didSet { d.set(notifIdle, forKey: "notifIdle") } }
    @Published var notifDone: Bool { didSet { d.set(notifDone, forKey: "notifDone") } }
    @Published var notifBgFailed: Bool { didSet { d.set(notifBgFailed, forKey: "notifBgFailed") } }

    init() {
        cstPath = d.string(forKey: "cstPath") ?? "~/.local/bin/cst"
        pollInterval = d.object(forKey: "pollInterval") as? Double ?? 3.0
        notifWaiting = d.object(forKey: "notifWaiting") as? Bool ?? true
        notifIdle = d.object(forKey: "notifIdle") as? Bool ?? true
        notifDone = d.object(forKey: "notifDone") as? Bool ?? false
        notifBgFailed = d.object(forKey: "notifBgFailed") as? Bool ?? false
    }

    var resolvedCstPath: String { (cstPath as NSString).expandingTildeInPath }

    var notifPrefs: NotifPrefs {
        NotifPrefs(onWaiting: notifWaiting, onIdle: notifIdle,
                   onDone: notifDone, onBgFailed: notifBgFailed)
    }
}
