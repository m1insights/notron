import SwiftUI

/// Notron Mac companion design tokens.
/// Derived from docs/design/DESIGN.md — do not add a colour, size, radius,
/// or spacing value that isn't here. Extend the token set there first.
enum DS {

    /// Light = the default, everywhere except Skills & Plugins.
    /// Dark = the "Operator" skin, used only on the Skills & Plugins screen
    /// (it's Advanced-tier only — Simple-tier users never see it).
    enum Color {
        static let bg = SwiftUI.Color(light: "#FAFAFA", dark: "#0B0B0D")
        /// Dark values lifted 2026-09-02. `#131316` on `#0B0B0D` is a 2% step:
        /// on a real screen the search field, the list and the preview panel
        /// all dissolved into the window and the user could not tell one
        /// component from another. Elevation on the dark skin has to be seen.
        static let surface = SwiftUI.Color(light: "#FFFFFF", dark: "#1C1C21")
        static let surfaceAlt = SwiftUI.Color(light: "#EFEFF1", dark: "#2A2A31")
        static let text = SwiftUI.Color(light: "#1A1A1E", dark: "#F2F2F4")
        static let textDim = SwiftUI.Color(light: "#6B6B70", dark: "#9A9AA2")
        /// Section eyebrows only ("Core", "Model tiers"). Dark value fixed 2026-08-31 —
        /// #6B6B72 failed contrast on #0B0B0D; #9C9CA6 is the corrected value.
        static let textFaint = SwiftUI.Color(light: "#8A8A90", dark: "#9C9CA6")
        static let accent = SwiftUI.Color(hex: "#0FA3B1") // the ONE accent, both skins
        /// The border and divider colour, per skin — DESIGN.md always meant this
        /// to flip. Use this one. `hairlineLight` hard-coded black, so every
        /// border on the dark skin was black on near-black, i.e. absent: that,
        /// far more than the background, is what made the components blur.
        static let hairline = SwiftUI.Color(light: "#E4E4E8", dark: "#3A3A42")
        static let hairlineLight = SwiftUI.Color.black.opacity(0.08)
        static let hairlineDark = SwiftUI.Color.white.opacity(0.10)
        static let success = SwiftUI.Color(light: "#28A745", dark: "#3FD4E0")
    }

    enum Space {
        static let s1: CGFloat = 4
        static let s2: CGFloat = 8
        static let s3: CGFloat = 12
        static let s4: CGFloat = 16
        static let s5: CGFloat = 22
        static let s6: CGFloat = 28
        static let s7: CGFloat = 32
    }

    enum Radius {
        static let sm: CGFloat = 8    // dark-skin cards, buttons
        static let md: CGFloat = 10   // light-skin buttons, fields
        static let lg: CGFloat = 14   // light-skin cards
        static let pill: CGFloat = 999
    }

    /// SF Pro everywhere, including literal values (API keys, timestamps) — no
    /// monospace/code typeface anywhere in this app. Removed 2026-08-31 (user
    /// feedback: read as cold/technical). Don't reintroduce `.monospaced`.
    enum Font {
        static let headline = SwiftUI.Font.system(size: 24, weight: .bold)
        static let title = SwiftUI.Font.system(size: 20, weight: .bold)
        static let body = SwiftUI.Font.system(size: 15, weight: .semibold)
        static let caption = SwiftUI.Font.system(size: 13, weight: .regular)
        static let label = SwiftUI.Font.system(size: 11, weight: .semibold)
    }

    enum Motion {
        /// Light-skin transitions.
        static let standard = SwiftUI.Animation.easeOut(duration: 0.2)
        /// Dark-skin (Skills & Plugins) transitions — reads as instant.
        static let instant = SwiftUI.Animation.linear(duration: 0.1)
    }
}

extension SwiftUI.Color {
    /// A colour that resolves differently per colour scheme without an asset catalog entry.
    init(light: String, dark: String) {
        #if canImport(AppKit)
        self.init(NSColor(name: nil, dynamicProvider: { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(Color(hex: isDark ? dark : light))
        }))
        #else
        self.init(hex: light)
        #endif
    }

    init(hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var value: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&value)
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}

/// The segmented control DESIGN.md actually specifies: one filled `accent`
/// segment with `bg`-coloured text, the rest plain labels.
///
/// AppKit's `.segmented` picker style paints three grey pills instead. One is
/// fine; a list of two hundred is a wall of identical grey in which you have to
/// stop and read each row to find which of its three pills is the selected one.
/// That is most of why this screen read as mush — so the list now shows one
/// word per row and this control appears once, for the note being looked at.
struct Segmented<Value: Hashable>: View {
    let values: [Value]
    let selection: Value
    let label: (Value) -> String
    let choose: (Value) -> Void

    var body: some View {
        HStack(spacing: DS.Space.s1) {
            ForEach(values, id: \.self) { value in
                let on = value == selection
                Text(label(value))
                    .font(DS.Font.body)
                    .foregroundStyle(on ? DS.Color.bg : DS.Color.textDim)
                    .padding(.horizontal, DS.Space.s4)
                    .padding(.vertical, DS.Space.s2)
                    .background(on ? DS.Color.accent : DS.Color.surfaceAlt)
                    .clipShape(RoundedRectangle(cornerRadius: DS.Radius.sm))
                    .contentShape(Rectangle())
                    .onTapGesture { choose(value) }
            }
        }
        .animation(DS.Motion.standard, value: selection)
    }
}
