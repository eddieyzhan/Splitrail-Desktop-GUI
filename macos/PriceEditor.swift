import AppKit

/// Instantiated only from Settings; no pricing catalogue remains in the menu-bar process.
final class PriceEditorController: NSObject, NSWindowDelegate {
    private let worker = Worker()
    private var window: NSWindow?
    private var models: [[String: Any]] = []
    private let picker = NSPopUpButton()
    private let fields = (0..<4).map { _ in NSTextField() }
    private let notice = NSTextField(wrappingLabelWithString: "")
    func show() {
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 360, height: 300), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        panel.title = "Pricing"; panel.delegate = self; panel.isReleasedWhenClosed = false
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        panel.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: panel.contentView!.leadingAnchor, constant: 20),
                                     stack.trailingAnchor.constraint(equalTo: panel.contentView!.trailingAnchor, constant: -20),
                                     stack.topAnchor.constraint(equalTo: panel.contentView!.topAnchor, constant: 20)])
        picker.target = self; picker.action = #selector(selectModel); stack.addArrangedSubview(picker)
        stack.addArrangedSubview(NSTextField(labelWithString: "USD per million"))
        for (index, name) in ["Input", "Output", "Cache read", "Cache write"].enumerated() {
            let row = NSStackView(views: [NSTextField(labelWithString: name), fields[index]])
            row.distribution = .fillEqually; row.spacing = 8; fields[index].setAccessibilityLabel(name + " rate")
            stack.addArrangedSubview(row); row.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
        let save = NSButton(title: "Save", target: self, action: #selector(save)), reset = NSButton(title: "Restore", target: self, action: #selector(restore))
        stack.addArrangedSubview(NSStackView(views: [save, reset])); stack.addArrangedSubview(notice)
        window = panel; panel.center(); panel.makeKeyAndOrderFront(nil)
        worker.run("prices") { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let value):
                self.models = value["models"] as? [[String: Any]] ?? []
                self.picker.addItems(withTitles: self.models.compactMap { $0["name"] as? String })
                self.selectModel()
            case .failure(let error): self.notice.stringValue = error.localizedDescription
            }
        }
    }
    @objc private func selectModel() {
        guard models.indices.contains(picker.indexOfSelectedItem) else { return }
        let model = models[picker.indexOfSelectedItem]
        for (index, key) in ["input", "output", "cache_read", "cache_write"].enumerated() {
            fields[index].stringValue = (model[key] as? NSNumber).map { String(format: "%.8g", $0.doubleValue) } ?? ""
        }
    }
    @objc private func save() {
        guard let model = picker.titleOfSelectedItem else { return }
        var rates: [String: Any] = [:]
        for (index, key) in ["input", "output", "cache_read", "cache_write"].enumerated() {
            if index >= 2 && fields[index].stringValue.isEmpty { rates[key] = NSNull(); continue }
            guard let rate = Double(fields[index].stringValue), rate.isFinite, rate >= 0 else { notice.stringValue = "Enter nonnegative rates."; return }
            rates[key] = rate
        }
        write(["model": model, "rates": rates])
    }
    @objc private func restore() { if let model = picker.titleOfSelectedItem { write(["model": model, "rates": NSNull()]) } }
    private func write(_ options: [String: Any]) {
        worker.run("price", options: options) { [weak self] result in
            switch result {
            case .success: self?.notice.stringValue = "Saved. Refresh usage to recalculate."
            case .failure(let error): self?.notice.stringValue = error.localizedDescription
            }
        }
    }
    func windowWillClose(_ notification: Notification) { window = nil; models = []; worker.cancel() }
}
