import SwiftUI

@main
struct CstApp: App {
    @StateObject private var store: SessionStore
    @StateObject private var config: AppConfig
    private let notifier = NotificationManager()

    init() {
        let cfg = AppConfig.shared
        let client = CstClient(executablePath: cfg.resolvedCstPath)
        let s = SessionStore(source: client)
        _config = StateObject(wrappedValue: cfg)
        _store = StateObject(wrappedValue: s)

        let notifier = self.notifier
        Task { @MainActor in
            notifier.requestAuthorization()
            s.onTransitions = { transitions in
                let specs = NotificationPolicy.specs(for: transitions,
                                                     prefs: cfg.notifPrefs)
                notifier.post(specs)
            }
            s.start(interval: cfg.pollInterval)
        }
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(store)
        } label: {
            Text(store.statusSummary)
        }
        .menuBarExtraStyle(.menu)

        Window("cst sessions", id: "main") {
            SessionListWindow()
                .environmentObject(store)
                .frame(minWidth: 900, minHeight: 500)
        }

        Settings {
            SettingsView(config: config)
        }
    }
}
