import SwiftUI

/// The same `PinStep`, outside onboarding — because the notes that get buried
/// get buried in month six, not on day one, and a user who pressed "I'll do
/// this later" would otherwise never see this screen again.
struct PinNotesView: View {
    @StateObject private var model = OnboardingModel()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        PinStep(model: model, finish: { dismiss() })
            .padding(DS.Space.s6)
            .frame(minWidth: 620, idealWidth: 620, minHeight: 480, idealHeight: 520)
            .background(DS.Color.bg)
    }
}
