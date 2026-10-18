import SwiftUI
import AVFoundation
import CoreImage.CIFilterBuiltins

enum InvitationCode {
    static func normalize(_ value: String) throws -> String {
        let code = value.uppercased().filter { !$0.isWhitespace && $0 != "-" }
        let allowed = Set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        guard code.count == 10, code.allSatisfy({ allowed.contains($0) }) else {
            throw APIError.local("Код должен содержать 10 символов приглашения.")
        }
        return code
    }
    static func fromQR(_ value: String) throws -> String {
        let parts = value.split(separator: ":", omittingEmptySubsequences: false)
        guard value.utf8.count < 80, parts.count == 4, parts[0] == "can-invite", parts[1] == "v1",
              parts[2] == "production" else { throw APIError.local("Это не приглашение в Can production. Ссылки из QR не открываются.") }
        guard parts[3].count == 10 else { throw APIError.local("Некорректный QR приглашения.") }
        return try normalize(String(parts[3]))
    }
}
struct InvitationView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    let invitation: Invitation
    @State private var brightness: CGFloat?
    @State private var sharing = false
    @State private var busy = false
    @State private var copied = false
    private var image: UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(invitation.qr.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 10, y: 10)),
              let cg = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return UIImage(cgImage: cg)
    }
    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Text("Вместе можно").font(.largeTitle.bold())
                Text("У всех участников одинаковые права").foregroundStyle(.secondary)
                if let image {
                    Image(uiImage: image).interpolation(.none).resizable().scaledToFit().padding(20)
                        .background(.white, in: RoundedRectangle(cornerRadius: 24)).frame(maxWidth: 300)
                        .accessibilityLabel("QR приглашения")
                }
                Text(invitation.code).font(.system(.title, design: .monospaced).bold()).textSelection(.enabled)
                Text("Действует до \(invitation.expires_at)").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button(copied ? "Скопировано" : "Скопировать код") { UIPasteboard.general.string = invitation.code; copied = true }
                    Button("Поделиться", systemImage: "square.and.arrow.up") { restoreBrightness(); sharing = true }
                }.buttonStyle(.bordered)
                AsyncButton("Отозвать приглашение", busy: $busy) {
                    _ = try await store.onlineCommand("revoke_invitation", payload: ["invitation_id": .string(invitation.id)])
                    dismiss()
                }.foregroundStyle(.red)
                Spacer()
            }.padding(24).navigationTitle("Приглашение")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
        }
        .onAppear { boostBrightness() }.onDisappear { restoreBrightness() }
        .onChange(of: scenePhase) { _, phase in if phase == .active && !sharing { boostBrightness() } else { restoreBrightness() } }
        .sheet(isPresented: $sharing, onDismiss: { if scenePhase == .active { boostBrightness() } }) {
            ShareSheet(items: (image.map { [$0 as Any] } ?? []) + ["Приглашение в Can. Код: \(invitation.code). Действует 24 часа."])
        }
    }
    private func boostBrightness() {
        guard brightness == nil else { return }
        brightness = UIScreen.main.brightness; UIScreen.main.brightness = 1
    }
    private func restoreBrightness() { if let value = brightness { UIScreen.main.brightness = value; brightness = nil } }
}
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: items, applicationActivities: nil) }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

struct QRScannerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let scanned: (String) -> Void
    @State private var permitted = false
    @State private var denied = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Group {
                if permitted { CameraScanner(scanned: scanned, failed: { error = $0 }) }
                else if denied {
                    ContentUnavailableView("Камера недоступна", systemImage: "camera.fill", description: Text("Разрешите камеру в настройках iPhone или закройте сканер и введите код вручную."))
                } else { ProgressView("Подключаем камеру") }
            }.overlay(alignment: .bottom) { if let error { Text(error).padding().background(.ultraThinMaterial) } }
                .navigationTitle("Сканировать приглашение")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Ввести код вручную") { dismiss() } } }
                .task {
                    switch AVCaptureDevice.authorizationStatus(for: .video) {
                    case .authorized: permitted = true
                    case .notDetermined: permitted = await AVCaptureDevice.requestAccess(for: .video); denied = !permitted
                    default: denied = true
                    }
                }
        }
    }
}
final class CameraController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    let capture = AVCaptureSession()
    private let queue = DispatchQueue(label: "can.camera")
    private var preview: AVCaptureVideoPreviewLayer?
    private var handled = false
    var scanned: ((String) -> Void)?
    var failed: ((String) -> Void)?
    override func viewDidLoad() {
        super.viewDidLoad()
        let layer = AVCaptureVideoPreviewLayer(session: capture)
        layer.videoGravity = .resizeAspectFill; view.layer.addSublayer(layer); preview = layer
        queue.async { [weak self] in
            guard let self else { return }
            do {
                guard let device = AVCaptureDevice.default(for: .video) else { throw APIError.local("Камера не найдена. Введите код вручную.") }
                let input = try AVCaptureDeviceInput(device: device)
                let output = AVCaptureMetadataOutput()
                guard self.capture.canAddInput(input), self.capture.canAddOutput(output) else { throw APIError.local("Не удалось включить камеру.") }
                self.capture.beginConfiguration(); self.capture.addInput(input); self.capture.addOutput(output)
                output.setMetadataObjectsDelegate(self, queue: .main); output.metadataObjectTypes = [.qr]
                self.capture.commitConfiguration(); self.capture.startRunning()
            } catch { DispatchQueue.main.async { self.failed?(error.localizedDescription) } }
        }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); preview?.frame = view.bounds }
    override func viewDidDisappear(_ animated: Bool) { super.viewDidDisappear(animated); stop() }
    func stop() { queue.async { [weak self] in self?.capture.stopRunning() } }
    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        guard !handled, let value = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
        do {
            _ = try InvitationCode.fromQR(value)
            handled = true; stop(); scanned?(value)
        } catch { failed?(error.localizedDescription) }
    }
}
struct CameraScanner: UIViewControllerRepresentable {
    let scanned: (String) -> Void
    let failed: (String) -> Void
    func makeUIViewController(context: Context) -> CameraController {
        let c = CameraController(); c.scanned = scanned; c.failed = failed; return c
    }
    func updateUIViewController(_ controller: CameraController, context: Context) {}
    static func dismantleUIViewController(_ controller: CameraController, coordinator: ()) { controller.stop() }
}
