import SwiftUI
import AuthenticationServices
import NotronCore

extension KeychainStore: SessionVault {}

@MainActor final class AccountController:NSObject,ObservableObject,ASWebAuthenticationPresentationContextProviding {
    @Published var session:AccountSession?
    @Published var status="Managed sign-in will be available after account setup is validated."
    @Published var busy=false
    private var browser:ASWebAuthenticationSession?
    private var attempt:AuthorizationAttempt?
    private var operation:Task<Void,Never>?
    private var transport:OIDCTransport?
    private var config:IdentityConfiguration?
    override init() {
        super.init()
        // Only reviewed signed-bundle settings can enable an issuer. No user
        // supplied discovery URLs or preselected Apple identity connection.
        let info=Bundle.main.infoDictionary ?? [:]
        if info["NotronManagedIdentityValidated"] as? Bool == true,
           let issuerText=info["NotronOIDCIssuer"] as? String,let issuer=URL(string:issuerText),
           let serviceText=info["NotronServiceURL"] as? String,let service=URL(string:serviceText),
           let client=info["NotronOIDCClientID"] as? String,let audience=info["NotronOIDCAudience"] as? String,
           let config=try? IdentityConfiguration(issuer:issuer,service:service,clientID:client,audience:audience) {
            self.config=config
            let transport=OIDCTransport(config:config);self.transport=transport
            self.session=AccountSession(vault:KeychainStore(),transport:transport,barrier:FileSessionBarrier.application,stopManaged:{
                // Task 4's managed transport observes this and cancels pending
                // network work. P06 startup remains disabled independently.
                NotificationCenter.default.post(name: .notronManagedSessionStopped,object:nil)
            })
            if let session=self.session {Core.managedIPC=ManagedIPCSession(session:session,serviceURL:service)}
            status="Sign in to your Notron account."
        }
    }
    func presentationAnchor(for session:ASWebAuthenticationSession)->ASPresentationAnchor {
        NSApp.keyWindow ?? NSApp.windows.first ?? ASPresentationAnchor()
    }
    func signIn() {
        guard !busy,let config,let transport,let session else{return}
        busy=true;status="Opening secure sign-in…"
        operation=Task {
            do {
                let url=try await transport.authorizationURL()
                try Task.checkCancellation()
                let attempt=try AuthorizationAttempt(authorizationURL:url,clientID:config.clientID,audience:config.audience,redirectURI:config.redirectURI)
                self.attempt=attempt
                let browser=ASWebAuthenticationSession(url:attempt.url,callbackURLScheme:"com.m1labs.notron") { [weak self] callback,error in
                    Task { @MainActor in
                        guard let self else{return}
                        self.browser=nil
                        guard error==nil,let callback else {self.cancel();return}
                        self.operation=Task {
                            do {
                                let code=try attempt.consume(callback)
                                try await session.complete(code:code,verifier:attempt.verifier,nonce:attempt.nonce)
                                Core.managedIPC=ManagedIPCSession(session:session,serviceURL:config.service)
                                self.status="Signed in. Managed processing remains paused until setup is complete."
                            } catch {self.status="Sign-in could not be completed. Please try again."}
                            self.busy=false;self.attempt=nil
                        }
                    }
                }
                browser.presentationContextProvider=self
                browser.prefersEphemeralWebBrowserSession=true
                self.browser=browser
                guard browser.start() else {throw SessionError.unavailable}
            } catch {busy=false;status="Sign-in is unavailable. Please try again."}
        }
    }
    func cancel() {
        operation?.cancel();attempt?.cancel();attempt=nil;browser?.cancel();browser=nil
        session?.cancel();busy=false;status="Sign-in cancelled."
    }
    func signOut() {
        cancel()
        operation=Task {await session?.signOut();status=session?.credentialCleanupFailed == true ? "Processing is stopped for now, but sign-out could not be completed. Retry before closing Notron." : "Signed out. Managed processing is stopped. Your Notes are unchanged.";objectWillChange.send()}
    }
    func revokeThisDevice() {
        guard let session,let device=session.identity?.deviceID else{return}
        busy=true
        operation=Task {
            do {try await session.revokeDevice(device);status="This device is revoked. Your Notes are unchanged."}
            catch {status="Device revocation could not be confirmed. Please try again."}
            busy=false
        }
    }
    func deleteAccount() {
        guard let session else{return};busy=true
        operation=Task {
            do {try await session.requestDeletion();status="Account deletion requested. Managed processing is stopped. Your Notes are unchanged."}
            catch {status="Account deletion could not be confirmed. Please try again."}
            busy=false
        }
    }
}

extension Notification.Name {
    static let notronManagedSessionStopped=Notification.Name("com.m1labs.notron.managedSessionStopped")
}

struct AccountView:View {
    @StateObject private var account=AccountController()
    @State private var confirmDeletion=false
    var body:some View {
        VStack(alignment:.leading,spacing:DS.Space.s5) {
            Text("Your account").font(DS.Font.headline)
            Text(account.status).font(DS.Font.body)
            Text("Account access and billing are separate. Signing in does not start paid processing.").font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            if account.session?.credentialCleanupFailed == true {Button("Retry sign-out"){account.signOut()}}
            if account.session?.isSignedIn == true {
                Button("Sign out"){account.signOut()}
                Button("Revoke this device"){account.revokeThisDevice()}.disabled(account.busy)
                Button("Delete account…"){confirmDeletion=true}.disabled(account.busy)
            } else {
                Button("Sign in with email"){account.signIn()}.disabled(account.session==nil || account.busy)
            }
            if account.busy {Button("Cancel sign-in"){account.cancel()}}
        }
        .padding(DS.Space.s7).foregroundStyle(DS.Color.text).background(DS.Color.bg)
        .preferredColorScheme(.light)
        .confirmationDialog("Request account deletion?",isPresented:$confirmDeletion,titleVisibility:.visible) {
            Button("Request deletion",role:.destructive){account.deleteAccount()}
            Button("Cancel",role:.cancel){}
        } message: {Text("Managed access will stop on every device. Your Apple Notes will remain on your devices.")}
    }
}
