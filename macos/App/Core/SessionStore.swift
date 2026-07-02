import Foundation
import Combine

@MainActor
final class SessionStore: ObservableObject {
    @Published private(set) var sessions: [Session] = []
    @Published private(set) var lastError: String?

    var onTransitions: (([Transition]) -> Void)?

    private let source: CstDataSource
    private var previous: [Session] = []
    private var seeded = false
    private var loop: Task<Void, Never>?
    private var refreshing = false
    private var pendingRefresh = false

    init(source: CstDataSource) { self.source = source }

    deinit { loop?.cancel() }

    func refreshOnce() async {
        if refreshing { pendingRefresh = true; return }
        refreshing = true
        defer { refreshing = false }
        repeat {
            pendingRefresh = false
            do {
                let cur = try await source.list(limit: nil)
                let trans = seeded ? DiffEngine.transitions(previous: previous, current: cur) : []
                seeded = true
                previous = cur
                sessions = cur
                lastError = nil
                if !trans.isEmpty { onTransitions?(trans) }
            } catch {
                lastError = Self.describe(error)
            }
        } while pendingRefresh
    }

    func start(interval: TimeInterval) {
        loop?.cancel()
        loop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshOnce()
                let seconds = min(max(0, interval), Double(UInt64.max) / 1_000_000_000)
                try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            }
        }
    }

    func stop() { loop?.cancel(); loop = nil }

    func resume(_ id: String) {
        Task { try? await source.resume(id); await refreshOnce() }
    }

    func toggleDone(_ s: Session) {
        Task {
            if s.isDone { try? await source.markUndone(s.sessionId) }
            else { try? await source.markDone(s.sessionId) }
            await refreshOnce()
        }
    }

    func preview(_ id: String) async -> String {
        (try? await source.preview(id, maxChars: 4000)) ?? ""
    }

    /// 메뉴바 라벨용 요약, 예: "● 1  ! 2".
    var statusSummary: String {
        let order: [(String, String)] = [("working", "●"), ("waiting", "!"),
                                          ("idle", "◦"), ("ended", "○"), ("done", "✓")]
        let counts = Dictionary(grouping: sessions, by: \.status).mapValues(\.count)
        let parts = order.compactMap { (name, glyph) -> String? in
            guard let n = counts[name], n > 0 else { return nil }
            return "\(glyph) \(n)"
        }
        return parts.isEmpty ? "cst" : parts.joined(separator: "  ")
    }

    private static func describe(_ error: Error) -> String {
        switch error {
        case CstError.notFound: return "cst 실행 파일을 찾을 수 없음 (설정에서 경로 확인)"
        case CstError.timedOut: return "cst 호출 타임아웃"
        case CstError.nonZeroExit(let code, let msg): return "cst 오류(\(code)): \(msg)"
        default: return String(describing: error)
        }
    }
}
