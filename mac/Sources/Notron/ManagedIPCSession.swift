import Foundation
import Darwin

/// Access-only session supply on a dedicated inherited Unix socket. No listener
/// path, refresh token, environment secret, or stdout credential is involved.
@MainActor public final class ManagedIPCSession {
    private let session:AccountSession
    private let serviceURL:URL
    private var stopped=false
    private var channels:[UUID:(FileHandle,()->Void)]=[:]
    private var observer:NSObjectProtocol?
    public init(session:AccountSession,serviceURL:URL) {
        self.session=session;self.serviceURL=serviceURL
        observer=NotificationCenter.default.addObserver(forName:Notification.Name("com.m1labs.notron.managedSessionStopped"),object:nil,queue:.main) { [weak self] _ in
            MainActor.assumeIsolated {self?.stop()}
        }
    }
    public func reply(_ data:Data) async throws -> Data {
        guard !stopped,data.count<=1024,
              let value=try JSONSerialization.jsonObject(with:data) as? [String:Any],
              let operation=value["operation"] as? String else {throw SessionError.unavailable}
        if operation=="configuration",Set(value.keys)==["operation"] {
            return try JSONSerialization.data(withJSONObject:["service_url":serviceURL.absoluteString])
        }
        guard operation=="access_token",Set(value.keys)==["operation","force_refresh"],
              let flag=value["force_refresh"] as? NSNumber,CFGetTypeID(flag)==CFBooleanGetTypeID() else {throw SessionError.unavailable}
        let token=try await session.accessToken(forceRefresh:flag.boolValue)
        guard !stopped,token.utf8.count<=16384 else {throw SessionError.signedOut}
        return try JSONSerialization.data(withJSONObject:["access_token":token])
    }
    public func makeChannel(onStop:@escaping ()->Void) throws -> FileHandle {
        guard !stopped else {throw SessionError.signedOut}
        var descriptors:[Int32]=[0,0]
        guard socketpair(AF_UNIX,SOCK_STREAM,0,&descriptors)==0 else {throw SessionError.unavailable}
        let parent=FileHandle(fileDescriptor:descriptors[0],closeOnDealloc:true)
        let child=FileHandle(fileDescriptor:descriptors[1],closeOnDealloc:true)
        let id=UUID();channels[id]=(parent,onStop)
        Task.detached { [weak self] in
            do {
                while true {
                    var request=Data()
                    while let byte=try parent.read(upToCount:1), !byte.isEmpty {
                        request.append(byte)
                        if request.count>1024 {throw SessionError.unavailable}
                        if byte.last==10 {break}
                    }
                    if request.isEmpty {break}
                    guard request.last==10,let self else {throw SessionError.unavailable}
                    var response=try await self.reply(request);response.append(10)
                    try parent.write(contentsOf:response)
                }
            } catch {}
            await self?.close(id)
        }
        return child
    }
    private func close(_ id:UUID) {
        if let channel=channels.removeValue(forKey:id) {
            shutdown(channel.0.fileDescriptor,SHUT_RDWR);try? channel.0.close()
        }
    }
    public func stop() {
        stopped=true
        for id in Array(channels.keys) {
            let callback=channels[id]?.1
            close(id);callback?()
        }
    }
}
