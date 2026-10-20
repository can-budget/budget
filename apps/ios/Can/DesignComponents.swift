import SwiftUI

struct ExpenseSubmitButton: View {
    let enabled: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            VStack {
                Spacer()
                Image(systemName: "checkmark").font(.system(size: 26, weight: .medium)).padding(.bottom, 22)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .foregroundStyle(.black)
            .background(CanStyle.accent, in: RoundedRectangle(cornerRadius: 11))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .opacity(enabled ? 1 : 0.35)
        .disabled(!enabled)
        .accessibilityLabel("Сохранить сумму").accessibilityIdentifier("expense.save")
    }
}

struct CanPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var destructive = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.body.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: 52)
            .foregroundStyle(Color.black)
            .background(destructive ? CanStyle.danger : CanStyle.accent, in: RoundedRectangle(cornerRadius: 14))
            .opacity(enabled ? (configuration.isPressed ? 0.72 : 1) : 0.35)
    }
}

struct CanSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.body.weight(.medium))
            .frame(maxWidth: .infinity, minHeight: 52)
            .foregroundStyle(.primary)
            .background(CanStyle.panel, in: RoundedRectangle(cornerRadius: 14))
            .opacity(configuration.isPressed ? 0.65 : 1)
    }
}

extension View {
    func canScreen() -> some View {
        self.scrollContentBackground(.hidden)
            .background(CanStyle.background.ignoresSafeArea())
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(CanStyle.background, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar(.visible, for: .navigationBar)
    }
}

struct BudgetDatePicker: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var selection: Date
    let currentDay: String
    let range: ClosedRange<Date>
    let selected: () -> Void
    private var dates: [Date] {
        (0..<31).compactMap { BusinessTime.calendar.date(byAdding: .day, value: $0, to: BusinessTime.date(currentDay)) }
            .filter { range.contains($0) }
    }
    var body: some View {
        NavigationStack {
            List {
                ForEach(dates, id: \.self) { date in
                    let count = (BusinessTime.calendar.dateComponents([.day], from: BusinessTime.date(currentDay), to: date).day ?? 0) + 1
                    Button {
                        selection = date; selected(); dismiss()
                    } label: {
                        HStack(spacing: 14) {
                            Image(systemName: "checkmark").font(.footnote.weight(.semibold))
                                .opacity(BusinessTime.day(selection) == BusinessTime.day(date) ? 1 : 0).frame(width: 18)
                            Text("\(count == 1 ? "Сегодня" : BudgetDisplay.date(BusinessTime.day(date))) — \(BudgetDisplay.days(count))")
                                .foregroundStyle(.primary)
                        }.frame(minHeight: 30)
                    }.listRowBackground(Color.clear)
                }
                Section {
                    DatePicker("Другая дата", selection: $selection, in: range, displayedComponents: .date)
                        .environment(\.timeZone, BusinessTime.calendar.timeZone)
                        .onChange(of: selection) { _, _ in selected() }
                }
            }.listStyle(.plain).canScreen().navigationTitle("Выберите срок")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Готово") { dismiss() } } }
        }.presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
    }
}
