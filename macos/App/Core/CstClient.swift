import Foundation

enum CstError: Error {
    case nonZeroExit(Int32, String)
    case timedOut
    case notFound
}

enum CstCommand {
    static func listArgs(limit: Int?, days: Int?, cwd: String?,
                         status: String?, sort: String?) -> [String] {
        var a = ["list", "--json"]
        if let limit { a += ["--limit", String(limit)] }
        if let days { a += ["--days", String(days)] }
        if let cwd { a += ["--cwd", cwd] }
        if let status { a += ["--status", status] }
        if let sort { a += ["--sort", sort] }
        return a
    }
    static func resumeArgs(_ id: String) -> [String] { ["resume", id] }
    static func doneArgs(_ id: String) -> [String] { ["done", id] }
    static func undoneArgs(_ id: String) -> [String] { ["undone", id] }
    static func showArgs(_ id: String, maxChars: Int) -> [String] {
        ["show", id, "--max-chars", String(maxChars)]
    }
}

protocol CstDataSource {
    func list(limit: Int?) async throws -> [Session]
    func preview(_ id: String, maxChars: Int) async throws -> String
    func resume(_ id: String) async throws
    func markDone(_ id: String) async throws
    func markUndone(_ id: String) async throws
}

struct CstClient: CstDataSource {
    let executablePath: String
    var timeout: TimeInterval = 15

    func list(limit: Int?) async throws -> [Session] {
        let out = try await run(CstCommand.listArgs(limit: limit, days: nil,
                                                    cwd: nil, status: nil, sort: nil))
        return try JSONDecoder().decode(SessionsPayload.self, from: out).sessions
    }
    func preview(_ id: String, maxChars: Int) async throws -> String {
        let out = try await run(CstCommand.showArgs(id, maxChars: maxChars))
        return String(data: out, encoding: .utf8) ?? ""
    }
    func resume(_ id: String) async throws { _ = try await run(CstCommand.resumeArgs(id)) }
    func markDone(_ id: String) async throws { _ = try await run(CstCommand.doneArgs(id)) }
    func markUndone(_ id: String) async throws { _ = try await run(CstCommand.undoneArgs(id)) }

    /// cst를 실행하고 stdout(Data)을 반환. 비영점 종료/타임아웃/미탐지는 throw.
    @discardableResult
    private func run(_ args: [String]) async throws -> Data {
        guard FileManager.default.isExecutableFile(atPath: executablePath) else {
            throw CstError.notFound
        }
        return try await withCheckedThrowingContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let proc = Process()
                proc.executableURL = URL(fileURLWithPath: executablePath)
                proc.arguments = args
                let outPipe = Pipe(), errPipe = Pipe()
                proc.standardOutput = outPipe
                proc.standardError = errPipe
                do {
                    try proc.run()
                } catch {
                    cont.resume(throwing: CstError.notFound); return
                }
                let deadline = DispatchTime.now() + timeout
                let killer = DispatchWorkItem { if proc.isRunning { proc.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: deadline, execute: killer)

                // stdout/stderr를 동시에 드레인한다. 순차로 읽으면 자식이 stderr 파이프의
                // 커널 버퍼(macOS 기준 ~64KB)를 채운 뒤 stdout을 계속 채우는 경우, stdout
                // readDataToEndOfFile()이 절대 끝나지 않는 파이프 데드락이 발생한다.
                var outData = Data()
                var errData = Data()
                let drainGroup = DispatchGroup()
                drainGroup.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    outData = outPipe.fileHandleForReading.readDataToEndOfFile()
                    drainGroup.leave()
                }
                drainGroup.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                    drainGroup.leave()
                }
                drainGroup.wait()
                proc.waitUntilExit()
                killer.cancel()
                if proc.terminationStatus != 0 {
                    let msg = String(data: errData, encoding: .utf8) ?? ""
                    cont.resume(throwing: CstError.nonZeroExit(proc.terminationStatus, msg))
                } else {
                    cont.resume(returning: outData)
                }
            }
        }
    }
}
