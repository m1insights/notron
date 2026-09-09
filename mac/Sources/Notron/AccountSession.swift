import Foundation
import CryptoKit
import Security
import Combine
import Darwin

public enum SessionError: Error { case invalidCallback, cancelled, unavailable, invalidIdentity, signedOut }

/// Non-UI protocol permits tests with an in-memory vault; production uses Keychain.
public protocol SessionVault {
    func get(_ name: String) throws -> Data?
    func put(_ name: String, value: Data) throws
    func delete(_ name: String) throws
}

/// Non-secret state, deliberately separate from potentially unavailable Keychain.
public enum SessionBarrierState: String { case active, signedOut, cleanupPending }
public protocol SessionBarrier {
    func read() throws -> SessionBarrierState
    func write(_ state: SessionBarrierState) throws
}

/// Private metadata only: never writes a credential, account ID, or token hash.
/// Missing, malformed, inaccessible, or insecurely-permissioned state cannot
/// authorize automatic refresh. Explicit validated browser sign-in enrolls it.
public struct FileSessionBarrier: SessionBarrier {
    public let directory: URL
    public static let application = FileSessionBarrier(directory:
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
            "Library/Application Support/com.m1labs.notron/account"))
    public init(directory: URL) { self.directory = directory }
    private var file: URL { directory.appendingPathComponent("session-state") }

    private func checkDirectory() throws {
        var info = stat()
        guard lstat(directory.path, &info) == 0,
              info.st_mode & S_IFMT == S_IFDIR, info.st_uid == getuid(),
              info.st_mode & 0o077 == 0 else { throw SessionError.unavailable }
    }
    public func read() throws -> SessionBarrierState {
        var info = stat()
        if lstat(directory.path, &info) != 0 {
            if errno == ENOENT { return .signedOut }
            throw SessionError.unavailable
        }
        try checkDirectory()
        let fd = open(file.path, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else {
            if errno == ENOENT { return .signedOut }
            throw SessionError.unavailable
        }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close() }
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_mode & 0o077 == 0,
              info.st_size <= 64,
              let data = try handle.read(upToCount: 65),
              let text = String(data: data, encoding: .utf8),
              let state = SessionBarrierState(rawValue: text) else { throw SessionError.unavailable }
        return state
    }
    public func write(_ state: SessionBarrierState) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try checkDirectory()
        let temporary = directory.appendingPathComponent(UUID().uuidString)
        let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw SessionError.unavailable }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close(); try? FileManager.default.removeItem(at: temporary) }
        try handle.write(contentsOf: Data(state.rawValue.utf8))
        try handle.synchronize()
        try handle.close()
        guard rename(temporary.path, file.path) == 0 else { throw SessionError.unavailable }
        let parent = open(directory.path, O_RDONLY | O_NOFOLLOW)
        guard parent >= 0 else { throw SessionError.unavailable }
        defer { close(parent) }
        guard fsync(parent) == 0 else { throw SessionError.unavailable }
    }
}

public final class AuthorizationAttempt {
    public let url: URL
    public let verifier: String
    public let nonce: String
    private let state: String
    private let redirectURI: String
    private var consumed = false

    public init(authorizationURL: URL, clientID: String, audience: String, redirectURI: String) throws {
        guard authorizationURL.scheme == "https", authorizationURL.user == nil, authorizationURL.password == nil,
              authorizationURL.query == nil, authorizationURL.fragment == nil,
              let callback = URL(string: redirectURI), callback.scheme == "com.m1labs.notron",
              callback.absoluteString == "com.m1labs.notron:/callback" else { throw SessionError.unavailable }
        self.verifier = try Self.random()
        self.state = try Self.random()
        self.nonce = try Self.random()
        self.redirectURI = redirectURI
        var parts = URLComponents(url: authorizationURL, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name:"response_type",value:"code"), URLQueryItem(name:"client_id",value:clientID),
            URLQueryItem(name:"audience",value:audience), URLQueryItem(name:"redirect_uri",value:redirectURI),
            URLQueryItem(name:"scope",value:"openid email offline_access account:read infer embed search"),
            URLQueryItem(name:"state",value:state), URLQueryItem(name:"nonce",value:nonce),
            URLQueryItem(name:"code_challenge_method",value:"S256"),
            URLQueryItem(name:"code_challenge",value:Self.base64(Data(SHA256.hash(data:Data(verifier.utf8)))))]
        self.url = parts.url!
    }
    public func cancel() { consumed = true }
    public func consume(_ callback: URL) throws -> String {
        guard !consumed else { throw SessionError.invalidCallback }
        consumed = true
        guard var parts=URLComponents(url:callback,resolvingAgainstBaseURL:false),parts.fragment==nil else {throw SessionError.invalidCallback}
        let items=parts.queryItems ?? [];parts.query=nil
        guard parts.url?.absoluteString == redirectURI,
              Set(items.map(\.name)).count == items.count,
              items.first(where:{$0.name=="state"})?.value == state,
              items.first(where:{$0.name=="error"}) == nil,
              let code=items.first(where:{$0.name=="code"})?.value,!code.isEmpty else {throw SessionError.invalidCallback}
        return code
    }
    private static func base64(_ data:Data)->String {data.base64EncodedString().replacingOccurrences(of:"+",with:"-").replacingOccurrences(of:"/",with:"_").replacingOccurrences(of:"=",with:"")}
    private static func random() throws -> String {
        var bytes=[UInt8](repeating:0,count:32)
        guard SecRandomCopyBytes(kSecRandomDefault,bytes.count,&bytes)==errSecSuccess else {throw SessionError.unavailable}
        return base64(Data(bytes))
    }
}

public struct TokenSet {
    public let accessToken:String
    public let refreshToken:String?
    public let idToken:String?
    public let expiresIn:TimeInterval
    public init(accessToken:String,refreshToken:String?,idToken:String?,expiresIn:TimeInterval) {
        self.accessToken=accessToken;self.refreshToken=refreshToken;self.idToken=idToken;self.expiresIn=expiresIn
    }
}
public struct DeviceIdentity: Codable {
    public let accountID:String
    public let deviceID:String
    public init(accountID:String,deviceID:String){self.accountID=accountID;self.deviceID=deviceID}
    enum CodingKeys:String,CodingKey {case accountID="account_id",deviceID="device_id"}
}
@MainActor public protocol SessionTransport {
    func exchange(code:String,verifier:String) async throws -> TokenSet
    func refresh(_ token:String) async throws -> TokenSet
    func verify(access:String,idToken:String) async throws -> String
    func identify(access:String) async throws -> DeviceIdentity
    func revoke(access:String,deviceID:String) async throws
    func deleteAccount(access:String) async throws
    func revokeRefresh(_ token:String) async throws
}

@MainActor public final class AccountSession: ObservableObject {
    @Published public private(set) var isSignedIn=false
    @Published public private(set) var credentialCleanupFailed=false
    private var locallySignedOut=false
    @Published public private(set) var identity:DeviceIdentity?
    private let vault:SessionVault
    private let barrier:SessionBarrier
    private let transport:SessionTransport
    private let stopManaged:()->Void
    private var access:String?
    private var expiry=Date.distantPast
    private var generation=0
    private var refreshing=false
    public init(vault:SessionVault,transport:SessionTransport,barrier:SessionBarrier,stopManaged:@escaping ()->Void) {
        self.vault=vault;self.transport=transport;self.barrier=barrier;self.stopManaged=stopManaged
        do {
            let state=try barrier.read()
            locallySignedOut=state != .active
            credentialCleanupFailed=state == .cleanupPending
        } catch {
            locallySignedOut=true;credentialCleanupFailed=true
        }
    }
    public func complete(code:String,verifier:String,nonce:String) async throws {
        try Task.checkCancellation()
        let epoch=generation
        let tokens=try await transport.exchange(code:code,verifier:verifier)
        guard let id=tokens.idToken,try await transport.verify(access:tokens.accessToken,idToken:id)==nonce else {throw SessionError.invalidIdentity}
        let who=try await transport.identify(access:tokens.accessToken)
        guard epoch==generation,!Task.isCancelled else {throw SessionError.cancelled}
        try install(tokens,identity:who,explicitLogin:true)
    }
    /// Sole supplier for managed transport. Refresh material never crosses IPC.
    /// Caller may force exactly one refresh after a 401; do not retry paid work.
    public func accessToken(forceRefresh:Bool=false) async throws -> String {
        // Consult persistent state even for a cached access token, so another
        // session instance's sign-out also stops this supplier.
        guard !locallySignedOut,(try? barrier.read()) == .active else {
            locallySignedOut=true;access=nil;isSignedIn=false;stopManaged()
            throw SessionError.signedOut
        }
        if !forceRefresh,let access,expiry.timeIntervalSinceNow>30 {return access}
        guard !refreshing else {throw SessionError.unavailable}
        guard let bytes=try vault.get("managed-refresh"),let token=String(data:bytes,encoding:.utf8),!token.isEmpty else {throw SessionError.signedOut}
        refreshing=true;defer{refreshing=false}
        let epoch=generation
        do {
            let tokens=try await transport.refresh(token)
            let who=try await transport.identify(access:tokens.accessToken)
            guard epoch==generation else {throw SessionError.cancelled}
            // Rotation is mandatory, so an issuer cannot leave stale refresh
            // material silently usable after a successful refresh.
            try install(tokens,identity:who)
            return tokens.accessToken
        } catch {
            if epoch==generation {try? clear()}
            throw SessionError.unavailable
        }
    }
    private func install(_ tokens:TokenSet,identity:DeviceIdentity,explicitLogin:Bool=false) throws {
        guard !tokens.accessToken.isEmpty,tokens.expiresIn>0,tokens.expiresIn<=900,
              let refresh=tokens.refreshToken,!refresh.isEmpty else {throw SessionError.invalidIdentity}
        if explicitLogin {try barrier.write(.cleanupPending)}
        else {guard try barrier.read() == .active else {throw SessionError.signedOut}}
        try vault.put("managed-refresh",value:Data(refresh.utf8))
        // Only a completed, nonce-verified new browser login can reset a stop.
        // A failure storing the replacement credential leaves the stop intact.
        if explicitLogin {try barrier.write(.active)}
        self.access=tokens.accessToken;expiry=Date().addingTimeInterval(tokens.expiresIn)
        self.identity=identity;isSignedIn=true;locallySignedOut=false;credentialCleanupFailed=false
    }
    public func cancel() {generation+=1}
    private func clear() throws {
        locallySignedOut=true
        generation+=1;access=nil;expiry = .distantPast;identity=nil;isSignedIn=false
        stopManaged()
        // Try both independent protections. A full/unwritable metadata volume
        // must not prevent otherwise-working Keychain credential deletion.
        var cleanupFailed=false
        do {try barrier.write(.cleanupPending)}
        catch {cleanupFailed=true}
        var credentialDeleted=false
        do {
            try vault.delete("managed-refresh")
            credentialDeleted=true
        } catch {cleanupFailed=true}
        if credentialDeleted {
            do {try barrier.write(.signedOut)}
            catch {cleanupFailed=true}
        }
        credentialCleanupFailed=cleanupFailed
        if cleanupFailed {throw SessionError.unavailable}
    }
    /// Clear first, then best-effort remote revocation. No Notes deletion calls.
    public func signOut() async {
        let oldAccess=access,oldIdentity=identity,refresh=try? vault.get("managed-refresh")
        try? clear()
        if let oldAccess,let oldIdentity {try? await transport.revoke(access:oldAccess,deviceID:oldIdentity.deviceID)}
        if let refresh,let value=String(data:refresh,encoding:.utf8) {try? await transport.revokeRefresh(value)}
    }
    public func revokeDevice(_ deviceID:String) async throws {
        let token=try await accessToken()
        try await transport.revoke(access:token,deviceID:deviceID)
        if identity?.deviceID==deviceID {await signOut()}
    }
    public func requestDeletion() async throws {
        let token=try await accessToken()
        try await transport.deleteAccount(access:token)
        await signOut()
    }
}

public struct IdentityConfiguration {
    public let issuer:URL, service:URL
    public let clientID:String, audience:String
    public let redirectURI="com.m1labs.notron:/callback"
    public init(issuer:URL,service:URL,clientID:String,audience:String) throws {
        for url in [issuer,service] {
            guard url.scheme=="https",url.host != nil,url.user==nil,url.password==nil,url.query==nil,url.fragment==nil else {throw SessionError.unavailable}
        }
        guard !clientID.isEmpty,!audience.isEmpty,clientID != audience else {throw SessionError.unavailable}
        self.issuer=issuer;self.service=service;self.clientID=clientID;self.audience=audience
    }
}

/// No cookie store, URL cache, redirects, or content-bearing diagnostics.
private final class RefuseRedirects:NSObject,URLSessionTaskDelegate {
    func urlSession(_ session:URLSession,task:URLSessionTask,willPerformHTTPRedirection response:HTTPURLResponse,
                    newRequest request:URLRequest,completionHandler:@escaping (URLRequest?)->Void) {completionHandler(nil)}
}
@MainActor public final class OIDCTransport:SessionTransport {
    private let config:IdentityConfiguration
    private let session:URLSession
    private var endpoints:[String:URL]=[:]
    public init(config:IdentityConfiguration) {
        self.config=config
        let settings=URLSessionConfiguration.ephemeral
        settings.urlCache=nil;settings.httpCookieStorage=nil;settings.httpShouldSetCookies=false
        settings.timeoutIntervalForRequest=15;settings.timeoutIntervalForResource=20
        self.session=URLSession(configuration:settings,delegate:RefuseRedirects(),delegateQueue:nil)
    }
    private func request(_ url:URL,method:String="GET",body:Data?=nil,contentType:String?=nil,access:String?=nil) async throws -> Data {
        var request=URLRequest(url:url);request.httpMethod=method;request.httpBody=body
        request.setValue("application/json",forHTTPHeaderField:"Accept")
        if let contentType {request.setValue(contentType,forHTTPHeaderField:"Content-Type")}
        if let access {request.setValue("Bearer "+access,forHTTPHeaderField:"Authorization")}
        do {
            let (bytes,response)=try await session.bytes(for:request)
            guard let response=response as? HTTPURLResponse,(200..<300).contains(response.statusCode) else {throw SessionError.unavailable}
            var data=Data()
            for try await byte in bytes {guard data.count<262144 else {throw SessionError.unavailable};data.append(byte)}
            return data
        } catch {throw SessionError.unavailable}
    }
    public func authorizationURL() async throws -> URL {try await endpoint("authorization_endpoint")}
    private func endpoint(_ name:String) async throws -> URL {
        if let url=endpoints[name]{return url}
        let data=try await request(config.issuer.appendingPathComponent(".well-known/openid-configuration"))
        guard let object=try JSONSerialization.jsonObject(with:data) as? [String:Any],object["issuer"] as? String==config.issuer.absoluteString else {throw SessionError.invalidIdentity}
        var validated:[String:URL]=[:]
        for key in ["authorization_endpoint","token_endpoint","revocation_endpoint"] {
            guard let text=object[key] as? String,let url=URL(string:text),url.scheme=="https",url.host==config.issuer.host,
                  url.port==config.issuer.port,url.user==nil,url.password==nil,url.fragment==nil,url.query==nil else {throw SessionError.invalidIdentity}
            validated[key]=url
        }
        endpoints=validated
        return endpoints[name]!
    }
    private func form(_ fields:[String:String])->Data {
        // application/x-www-form-urlencoded; encode separators and '+' as data.
        let allowed=CharacterSet(charactersIn:"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
        return Data(fields.sorted(by:{$0.key<$1.key}).map {key,value in
            key.addingPercentEncoding(withAllowedCharacters:allowed)!+"="+value.addingPercentEncoding(withAllowedCharacters:allowed)!
        }.joined(separator:"&").utf8)
    }
    private func tokens(_ fields:[String:String]) async throws -> TokenSet {
        let data=try await request(endpoint("token_endpoint"),method:"POST",body:form(fields),contentType:"application/x-www-form-urlencoded")
        struct Response:Decodable {let access_token:String;let refresh_token:String?;let id_token:String?;let expires_in:Double;let token_type:String}
        let result=try JSONDecoder().decode(Response.self,from:data)
        guard result.token_type.lowercased()=="bearer" else {throw SessionError.invalidIdentity}
        return TokenSet(accessToken:result.access_token,refreshToken:result.refresh_token,idToken:result.id_token,expiresIn:result.expires_in)
    }
    public func exchange(code:String,verifier:String) async throws -> TokenSet {
        try await tokens(["grant_type":"authorization_code","code":code,"code_verifier":verifier,"redirect_uri":config.redirectURI,"client_id":config.clientID])
    }
    public func refresh(_ token:String) async throws -> TokenSet {
        try await tokens(["grant_type":"refresh_token","refresh_token":token,"client_id":config.clientID])
    }
    public func verify(access:String,idToken:String) async throws -> String {
        let data=try await request(config.service.appendingPathComponent("v1/oidc/verify"),method:"POST",
                                   body:JSONEncoder().encode(["id_token":idToken]),contentType:"application/json",access:access)
        guard let nonce=try JSONDecoder().decode([String:String].self,from:data)["nonce"] else {throw SessionError.invalidIdentity}
        return nonce
    }
    public func identify(access:String) async throws -> DeviceIdentity {
        try JSONDecoder().decode(DeviceIdentity.self,from:await request(config.service.appendingPathComponent("v1/me"),access:access))
    }
    public func revoke(access:String,deviceID:String) async throws {
        guard UUID(uuidString:deviceID) != nil else {throw SessionError.invalidIdentity}
        _=try await request(config.service.appendingPathComponent("v1/devices/\(deviceID)/revoke"),method:"POST",access:access)
    }
    public func deleteAccount(access:String) async throws {
        _=try await request(config.service.appendingPathComponent("v1/me/deletion"),method:"POST",access:access)
    }
    public func revokeRefresh(_ token:String) async throws {
        _=try await request(endpoint("revocation_endpoint"),method:"POST",body:form(["token":token,"token_type_hint":"refresh_token","client_id":config.clientID]),contentType:"application/x-www-form-urlencoded")
    }
}
