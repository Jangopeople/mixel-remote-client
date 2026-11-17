#if os(macOS)
import SwiftUI

/// The sign-in screen in the redesign: the red hero at the left (what Mixel Remote is), the form at the
/// right: Microsoft, Google, Apple (the methods the identity service offers), the e-mail way to Mixel ID,
/// "stay signed in", Quick Support for someone without an account, and the terms line. The steps after
/// the click (browser, name of a new account, second verification) are the same as on the Account page.
struct SignInScreen: View {
    @ObservedObject var app: AppModel
    @ObservedObject var settings: SettingsModel
    @State private var email = ""

    init(app: AppModel) {
        self.app = app
        settings = app.settings
    }

    private var inProgress: Bool { ["profile_setup", "starting", "waiting_browser", "verifying", "mfa_required"].contains(app.account.phase) }

    var body: some View {
        HStack(spacing: 0) {
            hero.frame(width: 440)
            form.frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.page)
        }
        .background(Theme.page)
        .task { if app.signInProviders.isEmpty { await app.loadSignInProviders() } }
    }

    // MARK: Hero

    private var hero: some View {
        ZStack(alignment: .topLeading) {
            LinearGradient(colors: [Color(light: 0xBA2034, dark: 0xA11B2D), Color(light: 0x7E1423, dark: 0x5E0F1B)], startPoint: .topLeading, endPoint: .bottomTrailing)
            VStack(alignment: .leading, spacing: 26) {
                HStack(spacing: 10) {
                    MixelMark(size: 32)
                    Text(verbatim: "Mixel Remote").font(.system(size: 15, weight: .bold))
                }
                .padding(.top, 46)
                VStack(alignment: .leading, spacing: 12) {
                    Text(verbatim: L("Remote support that feels local.")).font(.system(size: 30, weight: .bold)).fixedSize(horizontal: false, vertical: true)
                    Text(verbatim: L("Reach every company device in seconds, run several sessions side by side, and file the report without leaving the session."))
                        .font(.system(size: 14)).opacity(0.9).fixedSize(horizontal: false, vertical: true)
                }
                VStack(alignment: .leading, spacing: 10) {
                    feature("lock.shield", L("End-to-end encrypted, hosted in Switzerland"))
                    feature("macwindow.on.rectangle", L("Several sessions in tabs or windows"))
                    feature("doc.text", L("Session reports linked to Mixel ISM"))
                }
                Spacer(minLength: 0)
                preview
                Text(verbatim: "Mixel IT and Corporate Services GmbH").font(.system(size: 11)).opacity(0.75)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 36).padding(.bottom, 28)
            WindowDragArea().frame(height: 40)
        }
    }

    private func feature(_ symbol: String, _ text: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).frame(width: 18)
            Text(verbatim: text).font(.system(size: 13))
        }
        .accessibilityElement(children: .combine)
    }

    /// Three illustrative rows ("what you will see"); decoration only, hidden from VoiceOver.
    private var preview: some View {
        VStack(spacing: 8) {
            previewRow("Reception PC", Color(red: 0.35, green: 0.85, blue: 0.55), L("Unattended"))
            previewRow("Design MacBook Pro", Color(red: 0.35, green: 0.85, blue: 0.55), L("%d fps", 60))
            previewRow("Accounting Mac", Color(red: 1, green: 0.84, blue: 0.04), L("Connecting"))
        }
        .padding(12)
        .background(.white.opacity(0.14), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityHidden(true)
    }

    private func previewRow(_ name: String, _ dot: Color, _ detail: String) -> some View {
        HStack(spacing: 8) {
            Circle().fill(dot).frame(width: 8, height: 8)
            Text(verbatim: name).font(.system(size: 12, weight: .medium))
            Spacer()
            Text(verbatim: detail).font(.system(size: 12)).opacity(0.8)
        }
    }

    // MARK: Form

    private var form: some View {
        ZStack(alignment: .top) {
            WindowDragArea().frame(height: 40).frame(maxWidth: .infinity)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    MixelMark(size: 44)
                    Text(verbatim: L("Sign in to Mixel Remote")).font(.system(size: 24, weight: .bold)).accessibilityAddTraits(.isHeader)
                    if inProgress {
                        SignInProgressView(app: app)
                    } else {
                        Text(verbatim: L("Use your Mixel ID. Your company decides which sign-in methods its members may use.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        if app.account.phase == "error" { MessageLine(Translator.shared.translate(app.account.message)) }
                        providers
                        if !app.otherSignInProviders.isEmpty { orRule }
                        emailBlock
                        Toggle(isOn: $settings.staySignedIn) { Text(verbatim: L("Stay signed in on this computer")) }
                            .toggleStyle(.checkbox)
                            .padding(.top, 2)
                        quickSupport
                        terms
                        Button(L("Language and appearance")) { app.section = .settings }.linkButton().font(.caption)
                    }
                    MessageLine(app.error)
                }
                .padding(.horizontal, 48).padding(.top, 44).padding(.bottom, 28)
                .frame(maxWidth: 440)
                .frame(maxWidth: .infinity)
            }
        }
    }

    @ViewBuilder private var providers: some View {
        ForEach(app.otherSignInProviders.sorted { order($0) < order($1) }, id: \.self) { provider in
            Button { app.signIn(provider: provider) } label: {
                HStack(spacing: 10) {
                    brand(provider)
                    Text(verbatim: AppModel.providerTitle(provider)).font(.system(size: 14, weight: .medium))
                    Spacer()
                }
                .padding(.horizontal, 14).frame(height: 42)
                .background(Theme.page, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(app.busy)
            .accessibilityIdentifier("signin-\(provider)")
            .accessibilityLabel(Text(verbatim: AppModel.providerTitle(provider)))
        }
    }

    private func order(_ provider: String) -> Int { ["azure": 0, "google": 1, "apple": 2][provider] ?? 3 }

    @ViewBuilder private func brand(_ provider: String) -> some View {
        switch provider {
        case "azure":
            VStack(spacing: 2) {
                HStack(spacing: 2) { Rectangle().fill(Color(red: 0.95, green: 0.31, blue: 0.13)); Rectangle().fill(Color(red: 0.5, green: 0.73, blue: 0)) }
                HStack(spacing: 2) { Rectangle().fill(Color(red: 0, green: 0.64, blue: 0.94)); Rectangle().fill(Color(red: 1, green: 0.73, blue: 0)) }
            }
            .frame(width: 18, height: 18)
        case "google":
            Text(verbatim: "G").font(.system(size: 17, weight: .bold, design: .rounded)).foregroundStyle(Color(red: 0.26, green: 0.52, blue: 0.96)).frame(width: 18, height: 18)
        default:
            Image(systemName: "apple.logo").font(.system(size: 16)).foregroundStyle(.primary).frame(width: 18, height: 18)
        }
    }

    private var orRule: some View {
        HStack(spacing: 10) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
            Text(verbatim: L("or")).font(.caption).foregroundStyle(.secondary)
            Rectangle().fill(Theme.hairline).frame(height: 1)
        }
        .accessibilityHidden(true)
    }

    private var emailBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            FieldRow(L("Work e-mail")) {
                TextField(text: $email, prompt: Text(verbatim: "name@company.ch")) { Text(verbatim: L("Work e-mail")) }
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { app.signIn() }
            }
            Button { app.signIn() } label: { Text(verbatim: L("Sign in with Mixel ID")).frame(maxWidth: .infinity).frame(height: 28) }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(app.busy)
                .keyboardShortcut(.defaultAction)
            Text(verbatim: L("Your browser opens; you confirm your e-mail address there.")).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 14) {
                Button(L("Use another browser")) { app.signIn(showLink: true) }.linkButton().disabled(app.busy)
                Button(L("Create Mixel ID")) {
                    if let url = URL(string: "https://auth.mixel.ch/if/flow/mixel-remote-enrollment/") { app.openURL(url) }
                }.linkButton()
            }
            .font(.callout)
        }
    }

    private var quickSupport: some View {
        HStack(spacing: 12) {
            Image(systemName: "bolt.fill").foregroundStyle(Theme.brand).frame(width: 34, height: 34)
                .background(Theme.brandSoft, in: RoundedRectangle(cornerRadius: 9, style: .continuous)).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(verbatim: L("Need help, no account?")).font(.system(size: 13, weight: .semibold))
                Text(verbatim: L("Get a support ID for your technician")).font(.system(size: 12)).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Button(L("Quick Support")) { if let url = URL(string: "https://ism.mixel.ch/remote/support/") { app.openURL(url) } }
        }
        .padding(12)
        .background(Theme.chrome, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
        .padding(.top, 4)
    }

    private var terms: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(verbatim: L("By continuing you accept the")).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 4) {
                Button(L("Terms of Service")) { settings.openLegal("terms") }.linkButton()
                Text(verbatim: "·").foregroundStyle(.secondary)
                Button(L("Data Processing Agreement")) { settings.openLegal("dpa") }.linkButton()
                Text(verbatim: "·").foregroundStyle(.secondary)
                Button(L("Privacy Policy")) { settings.openLegal("privacy") }.linkButton()
            }
            .font(.caption)
        }
    }
}
#endif
