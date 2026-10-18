import WidgetKit
import SwiftUI

struct CanWidgetEntry: TimelineEntry {
    let date: Date
    let state: SharedWidgetState?
    var expired = false
    var noAccess = false
}
struct CanWidgetProvider: TimelineProvider {
    func placeholder(in context: Context) -> CanWidgetEntry { CanWidgetEntry(date: Date(), state: nil) }
    func getSnapshot(in context: Context, completion: @escaping (CanWidgetEntry) -> Void) {
        let state = SharedWidgetStorage.load()
        let token: WidgetToken? = try? SecureStorage.load("widget-read", shared: true)
        completion(CanWidgetEntry(date: Date(), state: state, expired: state?.businessDate != BusinessTime.day(Date()),
                                  noAccess: token == nil || (BusinessTime.parse(token?.expires_at ?? "") ?? .distantPast) <= Date()))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<CanWidgetEntry>) -> Void) {
        Task {
            let now = Date()
            var local = SharedWidgetStorage.load()
            var noAccess = false
            do {
                guard let token: WidgetToken = try SecureStorage.load("widget-read", shared: true),
                      let expiry = BusinessTime.parse(token.expires_at), expiry > now,
                      let base = AppConfiguration.baseURL else { throw WidgetAccessError.unavailable }
                var request = URLRequest(url: base.appendingPathComponent("v1/widget/snapshot"))
                request.timeoutInterval = 10
                request.setValue("Bearer \(token.token)", forHTTPHeaderField: "Authorization")
                let (data, response) = try await URLSession.shared.data(for: request)
                // The main app may enqueue, leave, or switch accounts while this request is in flight.
                let latestToken: WidgetToken? = try SecureStorage.load("widget-read", shared: true)
                let latestLocal = SharedWidgetStorage.load()
                let priorCabinet = local?.cabinetID
                local = latestLocal
                guard latestToken?.token == token.token, latestLocal?.cabinetID == priorCabinet,
                      latestLocal != nil else {
                    noAccess = latestToken == nil || latestLocal == nil
                    throw WidgetAccessError.changed
                }
                guard let http = response as? HTTPURLResponse else { throw WidgetAccessError.network }
                if http.statusCode == 401 || http.statusCode == 403 { throw WidgetAccessError.unavailable }
                guard http.statusCode == 200 else { throw WidgetAccessError.network }
                let remote = try JSONDecoder().decode(WidgetSnapshot.self, from: data)
                guard remote.calculation_version == 2 else { throw WidgetAccessError.unavailable }
                if (local?.pendingCount ?? 0) == 0,
                   remote.cabinet_revision >= (local?.revision ?? 0), remote.business_date >= (local?.businessDate ?? "") {
                    local = SharedWidgetState(cabinetID: local?.cabinetID ?? "", revision: remote.cabinet_revision,
                                              businessDate: remote.business_date, calculatedAt: now, lastSyncAt: now,
                                              todayMinor: remote.today_remaining_minor, bankMinor: remote.bank_minor,
                                              pendingCount: 0, carryPending: remote.carry_pending, status: remote.status)
                    // Extension cache is separate and never mutates the app's pending projection.
                    if let local, let url = SharedWidgetStorage.url("widget-network-cache.json") {
                        try? JSONEncoder().encode(local).write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
                    }
                }
            } catch WidgetAccessError.unavailable { noAccess = true }
            catch WidgetAccessError.changed { }
            catch {
                local = SharedWidgetStorage.load()
                let latestToken: WidgetToken? = try? SecureStorage.load("widget-read", shared: true)
                noAccess = latestToken == nil || local == nil || (BusinessTime.parse(latestToken?.expires_at ?? "") ?? .distantPast) <= Date()
                if !noAccess, (local?.pendingCount ?? 0) == 0, let url = SharedWidgetStorage.url("widget-network-cache.json"),
                   let bytes = try? Data(contentsOf: url), let cached = try? JSONDecoder().decode(SharedWidgetState.self, from: bytes),
                   cached.cabinetID == local?.cabinetID, cached.revision >= (local?.revision ?? 0), cached.businessDate >= (local?.businessDate ?? "") {
                    local = cached
                }
            }
            let staleDay = local?.businessDate != BusinessTime.day(now)
            let first = CanWidgetEntry(date: now, state: local, expired: staleDay, noAccess: noAccess)
            let midnight = BusinessTime.calendar.date(byAdding: .day, value: 1, to: BusinessTime.calendar.startOfDay(for: now))!
            let nextDay = CanWidgetEntry(date: midnight, state: local, expired: true, noAccess: noAccess)
            var entries = [first]
            if let staleAt = local?.lastSyncAt.addingTimeInterval(901), staleAt > now, staleAt < midnight {
                entries.append(CanWidgetEntry(date: staleAt, state: local, expired: staleDay, noAccess: noAccess))
            }
            entries.append(nextDay)
            completion(Timeline(entries: entries, policy: .after(min(now.addingTimeInterval(900), midnight))))
        }
    }
}
enum WidgetAccessError: Error { case unavailable, network, changed }

struct CanWidgetView: View {
    let entry: CanWidgetEntry
    private var hasAmount: Bool {
        !entry.noAccess && !entry.expired && entry.state?.status == "active" && entry.state?.todayMinor != nil
    }
    private var status: String {
        guard !entry.noAccess else { return "Откройте приложение" }
        guard let state = entry.state else { return "Откройте Can, чтобы начать" }
        if entry.expired { return "Новый день · откройте приложение" }
        if state.status == "completed" { return "Спринт завершён" }
        if state.status != "active" { return "Начните спринт в Can" }
        if state.pendingCount > 0 { return "Предварительно · \(state.pendingCount) на телефоне" }
        if entry.date.timeIntervalSince(state.lastSyncAt) > 900 { return "Данные могли устареть" }
        if state.carryPending { return "Предварительно · выберите перенос" }
        return "Нажмите, чтобы записать расход"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(hasAmount ? "Сегодня" : "Можно").font(.subheadline.weight(.medium))
                Spacer()
                Text(entry.state?.businessDate ?? "CAN").font(.caption).foregroundStyle(.secondary)
            }
            Text(hasAmount ? Money.format(entry.state!.todayMinor!) : "—")
                .font(.system(size: 44, weight: .semibold, design: .rounded)).minimumScaleFactor(0.35).lineLimit(1)
                .foregroundStyle(Color(red: 0.74, green: 0.91, blue: 0.43)).widgetAccentable()
            Text(status).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }.containerBackground(Color(red: 0.07, green: 0.08, blue: 0.09), for: .widget)
            .widgetURL(URL(string: "can://expense"))
            .accessibilityElement(children: .combine)
    }
}

#if compiler(>=6.2)
@available(iOS 26.0, *)
struct CanWidgetPushHandler: WidgetPushHandler {
    func pushTokenDidChange(_ pushInfo: WidgetPushInfo, widgets: [WidgetInfo]) {
        // Share only the push registration, never the app's full session credential.
        // The app binds it to its authenticated installation at the next foreground sync.
        try? SecureStorage.save(pushInfo.token.map { String(format: "%02x", $0) }.joined(), account: "widget-push", shared: true)
    }
}
#endif

struct CanBudgetWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "CanBudget", provider: CanWidgetProvider()) { entry in CanWidgetView(entry: entry) }
            .configurationDisplayName("Можно сегодня").description("Общий дневной остаток и быстрый переход ко вводу.")
            .supportedFamilies([.systemMedium])
    }
}
#if compiler(>=6.2)
@available(iOS 26.0, *)
struct CanPushBudgetWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "CanBudget", provider: CanWidgetProvider()) { entry in CanWidgetView(entry: entry) }
            .configurationDisplayName("Можно сегодня").description("Общий дневной остаток и быстрый переход ко вводу.")
            .supportedFamilies([.systemMedium]).pushHandler(CanWidgetPushHandler.self)
    }
}
#endif
@main
struct CanWidgets: WidgetBundle {
    var body: some Widget {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) { return CanPushBudgetWidget() }
        #endif
        return CanBudgetWidget()
    }
}
