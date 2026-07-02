import Foundation
import UserNotifications

final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
    }

    func requestAuthorization() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func post(_ specs: [NotifSpec]) {
        let center = UNUserNotificationCenter.current()
        for spec in specs {
            let content = UNMutableNotificationContent()
            content.title = spec.title
            content.body = spec.body
            content.userInfo = ["sessionId": spec.sessionId]
            let req = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content, trigger: nil)
            center.add(req)
        }
    }

    // TODO(v1.1): didReceive → open "main" window + select notification.userInfo["sessionId"]
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
