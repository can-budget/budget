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
                guard [2, 3].contains(remote.calculation_version) else { throw WidgetAccessError.unavailable }
                if (local?.pendingCount ?? 0) == 0,
                   remote.cabinet_revision >= (local?.revision ?? 0), remote.business_date >= (local?.businessDate ?? "") {
                    // A remote revision may also change the end date, which the brief API omits.
                    let sameBasis = remote.cabinet_revision == local?.revision && remote.business_date == local?.businessDate
                    let daysRemaining = sameBasis ? local?.daysRemaining : nil
                    local = SharedWidgetState(cabinetID: local?.cabinetID ?? "", revision: remote.cabinet_revision,
                                              businessDate: remote.business_date, calculatedAt: now, lastSyncAt: now,
                                              todayMinor: remote.today_remaining_minor, bankMinor: remote.bank_minor,
                                              pendingCount: 0, carryPending: remote.carry_pending, status: remote.status,
                                              daysRemaining: daysRemaining, isOffline: false)
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
                local?.isOffline = true
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
    @Environment(\.widgetFamily) private var family
    let entry: CanWidgetEntry
    private var presentation: WidgetPresentation {
        WidgetPresentation(state: entry.state, date: entry.date, expired: entry.expired, noAccess: entry.noAccess)
    }
    private var amount: String { presentation.amount }
    private var isAccessory: Bool { family == .accessoryInline || family == .accessoryRectangular }
    var body: some View {
        Group {
            if family == .accessoryInline {
                Text("\(presentation.title) \(amount)\(presentation.inlineForecast)").privacySensitive()
            } else if family == .accessoryRectangular {
                lockScreen
            } else { homeScreen }
        }.containerBackground(isAccessory ? Color.clear : CanStyle.background, for: .widget)
            .widgetURL(URL(string: "can://expense"))
            .accessibilityElement(children: .combine)
    }
    private var homeScreen: some View {
        VStack(alignment: .leading, spacing: family == .systemSmall ? 5 : 2) {
            Text(presentation.title).font(family == .systemSmall ? .subheadline.weight(.medium) : .title3.weight(.medium))
            Text(amount).font(.system(size: family == .systemSmall ? 35 : 58, weight: .medium))
                .tracking(family == .systemSmall ? -1 : -2).monospacedDigit().minimumScaleFactor(0.3).lineLimit(1)
                .privacySensitive()
            Spacer(minLength: 0)
            HStack(alignment: .bottom, spacing: 4) {
                if !presentation.status.isEmpty {
                    Text(presentation.status).font(.system(size: family == .systemSmall ? 10 : 11)).foregroundStyle(.secondary).lineLimit(2)
                }
                if family != .systemSmall { Spacer(minLength: 4) }
                if family != .systemSmall {
                    Text("Можно").font(.caption.weight(.semibold)).foregroundStyle(CanStyle.accent).widgetAccentable()
                }
            }
            if family == .systemSmall {
                Text("Можно").font(.system(size: 11, weight: .semibold)).foregroundStyle(CanStyle.accent)
                    .frame(maxWidth: .infinity, alignment: .trailing).widgetAccentable()
            }
        }.foregroundStyle(.white).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }
    private var lockScreen: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(presentation.title).font(.caption.weight(.medium)).lineLimit(1)
            Text(amount).font(.system(size: 30, weight: .semibold)).monospacedDigit()
                .minimumScaleFactor(0.35).lineLimit(1).privacySensitive()
            Text(presentation.accessoryStatus).font(.system(size: 10)).lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading)
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
            .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular, .accessoryInline])
    }
}
#if compiler(>=6.2)
@available(iOS 26.0, *)
struct CanPushBudgetWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "CanBudget", provider: CanWidgetProvider()) { entry in CanWidgetView(entry: entry) }
            .configurationDisplayName("Можно сегодня").description("Общий дневной остаток и быстрый переход ко вводу.")
            .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular, .accessoryInline]).pushHandler(CanWidgetPushHandler.self)
    }
}
#endif
@main
struct CanWidgets: WidgetBundle {
    var body: some Widget {
        #if compiler(>=6.2) && !CAN_SIDELOAD
        if #available(iOS 26.0, *) { return CanPushBudgetWidget() }
        #endif
        return CanBudgetWidget()
    }
}

#if DEBUG
private enum WidgetPreviewData {
    static var active: CanWidgetEntry {
        let now = Date()
        let state = SharedWidgetState(cabinetID: "preview", revision: 1, businessDate: BusinessTime.day(now),
                                      calculatedAt: now, lastSyncAt: now, todayMinor: 55_200, bankMinor: 842_000,
                                      pendingCount: 0, carryPending: false, status: "active")
        return CanWidgetEntry(date: now, state: state)
    }
    static var pending: CanWidgetEntry {
        let current = active
        var state = current.state!
        state.todayMinor = 50_200
        state.pendingCount = 1
        return CanWidgetEntry(date: current.date, state: state)
    }
}

#Preview("Home · Example", as: .systemMedium) {
    CanBudgetWidget()
} timeline: {
    WidgetPreviewData.active
    WidgetPreviewData.pending
    CanWidgetEntry(date: Date(), state: nil, noAccess: true)
}

#Preview("Lock Screen · Example", as: .accessoryRectangular) {
    CanBudgetWidget()
} timeline: {
    WidgetPreviewData.active
    WidgetPreviewData.pending
    CanWidgetEntry(date: Date(), state: nil, noAccess: true)
}

#Preview("Compact · Example", as: .systemSmall) {
    CanBudgetWidget()
} timeline: {
    WidgetPreviewData.active
    WidgetPreviewData.pending
}

#Preview("Inline · Example", as: .accessoryInline) {
    CanBudgetWidget()
} timeline: {
    WidgetPreviewData.active
}
#endif
