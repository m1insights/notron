import XCTest
@testable import NotronCore

final class ManagedIPCSessionTests:XCTestCase {
    @MainActor func testAccessOnlyProtocolRefreshAndStop() async throws {
        let vault=MemoryVault();let upstream=FakeTransport()
        let session=AccountSession(vault:vault,transport:upstream,barrier:MemoryBarrier(),stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        let ipc=ManagedIPCSession(session:session,serviceURL:URL(string:"https://service.example.test")!)
        let response=try await ipc.reply(Data(#"{"operation":"access_token","force_refresh":false}"#.utf8))
        let object=try JSONSerialization.jsonObject(with:response) as! [String:String]
        XCTAssertEqual(object,["access_token":"access"])
        XCTAssertFalse(String(decoding:response,as:UTF8.self).contains("refresh"))
        _=try await ipc.reply(Data(#"{"operation":"access_token","force_refresh":true}"#.utf8))
        XCTAssertEqual(upstream.refreshCalls,1)
        for value in [#"{"operation":"managed-refresh"}"#,#"{"operation":"access_token","force_refresh":false,"name":"managed-refresh"}"#] {
            do {_=try await ipc.reply(Data(value.utf8));XCTFail("forbidden")} catch {}
        }
        ipc.stop()
        do {_=try await ipc.reply(Data(#"{"operation":"access_token","force_refresh":false}"#.utf8));XCTFail("stopped")}catch{}
    }
    @MainActor func testInheritedSocketRoundtrip() async throws {
        let vault=MemoryVault();let upstream=FakeTransport()
        let session=AccountSession(vault:vault,transport:upstream,barrier:MemoryBarrier(),stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        let ipc=ManagedIPCSession(session:session,serviceURL:URL(string:"https://service.example.test")!)
        let endpoint=try ipc.makeChannel(onStop:{})
        let reply=try await Task.detached { () -> Data in
            try endpoint.write(contentsOf:Data((#"{"operation":"access_token","force_refresh":false}"#+"\n").utf8))
            var result=Data()
            while let byte=try endpoint.read(upToCount:1), !byte.isEmpty {result.append(byte);if byte.last==10 {break}}
            return result
        }.value
        XCTAssertEqual(try JSONSerialization.jsonObject(with:reply) as? [String:String],["access_token":"access"])
        ipc.stop();try? endpoint.close()
    }
}

extension ManagedIPCSessionTests {
    @MainActor func testSignoutNotificationStopsEveryChildChannel() async throws {
        let session=AccountSession(vault:MemoryVault(),transport:FakeTransport(),barrier:MemoryBarrier(),stopManaged:{})
        let ipc=ManagedIPCSession(session:session,serviceURL:URL(string:"https://service.example.test")!)
        var stops=0
        let first=try ipc.makeChannel(onStop:{stops+=1})
        let second=try ipc.makeChannel(onStop:{stops+=1})
        NotificationCenter.default.post(name:Notification.Name("com.m1labs.notron.managedSessionStopped"),object:nil)
        XCTAssertEqual(stops,2)
        XCTAssertThrowsError(try ipc.makeChannel(onStop:{}))
        try? first.close();try? second.close()
    }
}
