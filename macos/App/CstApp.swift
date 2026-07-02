import SwiftUI

@main
struct CstApp: App {
    @StateObject private var store: SessionStore
    @StateObject private var config: AppConfig
    private let notifier = NotificationManager()

    init() {
        let cfg = AppConfig.shared
        let client = CstClient(executablePath: cfg.resolvedCstPath)
        _config = StateObject(wrappedValue: cfg)
        _store = StateObject(wrappedValue: SessionStore(source: client))
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(store)
                .onAppear {
                    notifier.requestAuthorization()
                    store.onTransitions = { transitions in
                        let specs = NotificationPolicy.specs(for: transitions,
                                                             prefs: config.notifPrefs)
                        notifier.post(specs)
                    }
                    store.start(interval: config.pollInterval)
                }
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
