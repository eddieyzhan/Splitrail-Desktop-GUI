import AppKit

// This file owns presentation only. Collection, permissions, persistence, and
// stopping a verified visible turn remain behind NativeActions.
final class DashboardViewController: NSViewController, NSTextFieldDelegate {
    weak var actions: NativeActions?

    private let surface = DashboardSurface()
    private var selectedPage = -1
    private var preset = 2
    private var customRange: (String, String)?
    private var datePopover: NSPopover?
    private var dateEditor: DashboardDateEditor?
    private var localMessage = ""
    private var limitsDirty = false
    private var settingsDirty = false
    private var submittedSettings: DashboardSyncDraft?
    private var limitsBuilt = false, settingsBuilt = false
    private var overviewRevision = ""

    private let overview = DashboardColumn()
    private let limits = DashboardColumn()
    private let settings = DashboardColumn()
    private let presets = NSSegmentedControl(labels: ["Day", "Week", "Month", "Year", "All"], trackingMode: .selectOne, target: nil, action: nil)
    private let calendarButton = NSButton()
    private let costValue = DashboardLabel(size: 27)
    private let tokenValue = DashboardLabel(size: 27)
    private let conversationValue = DashboardLabel(size: 27)
    private let quotaRows = DashboardColumn()
    private let modelRows = DashboardColumn()
    private let quotaRule = DashboardRule()
    private let modelRule = DashboardRule()

    private let limitStatus = DashboardLabel(size: 15)
    private let currentLimit = NSTextField()
    private let resetLimit = NSTextField()
    private let armButton = NSButton()
    private let disarmButton = NSButton()
    private let permissionButton = NSButton()
    private let recoveryToggle = NSButton(checkboxWithTitle: DesktopRecoveryAvailability.title, target: nil, action: nil)
    private let recoveryStatus = DashboardLabel(DesktopRecoveryAvailability.detail, size: 11, secondary: true, wraps: true)
    private let recoveryTestButton = NSButton()

    private let repository = NSTextField()
    private let connectionStatus = DashboardLabel(size: 11, secondary: true)
    private let configureButton = NSButton()
    private let disconnectButton = NSButton()
    private let automatic = NSButton(checkboxWithTitle: "Automatic sync", target: nil, action: nil)
    private let receiveOnly = NSButton(checkboxWithTitle: "Receive only", target: nil, action: nil)
    private let scope = NSPopUpButton()
    private let interval = NSTextField()
    private let idleInterval = NSTextField()
    private let usageSource = NSPopUpButton()
    private let themes = NSSegmentedControl(labels: ["Pearl", "Nord"], trackingMode: .selectOne, target: nil, action: nil)
    private let automaticPrices = NSButton(checkboxWithTitle: "Update public prices automatically", target: nil, action: nil)
    private var workingButtons: [NSButton] = []

    init(actions: NativeActions) {
        self.actions = actions
        preset = actions.rangePreset
        customRange = actions.selectedRange
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { nil }

    override func loadView() {
        view = surface
        buildOverview()
        for (index, button) in surface.navigation.enumerated() {
            button.tag = index
            button.target = self
            button.action = #selector(changePage(_:))
        }
        prepareButton(surface.refreshButton, title: "Refresh", action: #selector(refreshPressed))
        prepareButton(surface.syncButton, title: "Sync", action: #selector(syncPressed))
        surface.refreshButton.toolTip = "Refresh local usage and quota"
        surface.syncButton.toolTip = "Sync usage with the connected private repository"
        refresh()
    }

    /// Called by the parent when state changes; no clocks, polling, or animations.
    func refresh() {
        guard isViewLoaded, let actions else { return }
        let page = min(2, max(0, actions.page))
        let pageChanged = page != selectedPage
        selectedPage = page
        let palette = DashboardPalette(nord: actions.preferences["theme"] as? String == "Nord")
        surface.palette = palette
        surface.appearance = NSAppearance(named: palette.nord ? .darkAqua : .aqua)
        for (index, button) in surface.navigation.enumerated() {
            button.state = index == page ? .on : .off
            button.palette = palette
        }

        if page == 0 { updateOverview(actions) }
        if page == 1, !limitsBuilt { buildLimits(); limitsBuilt = true }
        if page == 2, !settingsBuilt { buildSettings(); settingsBuilt = true }
        if limitsBuilt { updateLimits(actions) }
        if settingsBuilt { updateSettings(actions) }
        surface.body = [overview, limits, settings][page]
        surface.applyPalette()
        surface.refreshButton.isEnabled = !actions.busy
        surface.syncButton.isEnabled = !actions.busy && connected(actions.syncInfo)
        for button in workingButtons { button.isEnabled = !actions.busy }
        configureButton.isEnabled = !actions.busy
        disconnectButton.isEnabled = !actions.busy && connected(actions.syncInfo)
        disarmButton.isEnabled = actions.guardState.armed
        themes.isEnabled = !actions.busy
        usageSource.isEnabled = !actions.busy
        automaticPrices.isEnabled = !actions.busy
        if actions.cache.timestamp.isFinite, actions.cache.timestamp > 0 {
            surface.timestamp.stringValue = Dates.clock.string(from: Date(timeIntervalSince1970: actions.cache.timestamp))
        } else {
            surface.timestamp.stringValue = ""
        }
        surface.timestamp.toolTip = "Last collected usage"
        surface.timestamp.setAccessibilityLabel("Last collected usage")
        updateMessage()
        if pageChanged {
            datePopover?.close()
            surface.scrollToTop()
            updatePreferredSize()
        }
        surface.needsLayout = true
    }

    private func buildOverview() {
        let presets = self.presets
        presets.controlSize = .small
        presets.font = .systemFont(ofSize: 12, weight: .regular)
        presets.segmentStyle = .rounded
        presets.selectedSegment = preset
        presets.target = self
        presets.action = #selector(changePreset(_:))
        presets.setAccessibilityLabel("Usage date range")
        overview.add(DashboardRow { row in
            presets.frame = row.bounds
            for index in 0..<presets.segmentCount {
                presets.setWidth(max(40, (row.bounds.width - 8) / 5), forSegment: index)
            }
        }.containing(presets), height: 24, after: 5)

        prepareButton(calendarButton, title: "", action: #selector(showDates(_:)))
        calendarButton.isBordered = false
        calendarButton.image = NSImage(systemSymbolName: "calendar", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 12, weight: .regular))
        calendarButton.imagePosition = .imageLeading
        calendarButton.setAccessibilityLabel("Choose start and end dates")
        overview.add(centered(calendarButton, width: 230), height: 24, after: 16)

        let values = [costValue, tokenValue, conversationValue]
        for (field, name) in zip(values, ["Cost", "Tokens", "Conversations"]) {
            field.alignment = .center
            field.toolTip = name
            field.setAccessibilityLabel(name)
        }
        overview.add(DashboardRow { row in
            let widths = [row.bounds.width * 0.40, row.bounds.width * 0.34, row.bounds.width * 0.26]
            var x: CGFloat = 0
            for (field, width) in zip(values, widths) {
                field.frame = NSRect(x: x, y: 0, width: width, height: row.bounds.height)
                x += width
            }
        }.containing(values), height: 37, after: 19)
        overview.add(quotaRule, height: 1, after: 14)
        overview.add(quotaRows, after: 14)
        overview.add(modelRule, height: 1, after: 12)
        overview.add(modelRows)
    }

    private func buildLimits() {
        limits.add(limitStatus, height: 23, after: 12)
        prepareField(currentLimit, label: "Current quota cap", numeric: true)
        prepareField(resetLimit, label: "Quota cap after reset", numeric: true)
        limits.add(fieldRow("Current cap", currentLimit, suffix: "%"), height: 28, after: 12)
        limits.add(fieldRow("After reset", resetLimit, suffix: "%"), height: 28, after: 18)

        let explanation = DashboardLabel("Stops the visible Codex turn; hidden chats and terminal sessions are not protected.", size: 12, secondary: true, wraps: true)
        limits.add(explanation, after: 8)
        let access = DashboardLabel("Arming requires Accessibility access and a verified, visible Stop control.", size: 11, secondary: true, wraps: true)
        limits.add(access, after: 12)
        prepareButton(armButton, title: "Arm", action: #selector(armPressed))
        prepareButton(disarmButton, title: "Disarm", action: #selector(disarmPressed))
        limits.add(buttonRow([armButton, disarmButton]), height: 26, after: 8)
        prepareButton(permissionButton, title: "Accessibility access…", action: #selector(permissionPressed))
        permissionButton.toolTip = "Allow Splitrail to press the Codex generation Stop control"
        limits.add(buttonRow([permissionButton]), height: 26, after: 14)
        limits.add(DashboardRule(), height: 1, after: 12)
        recoveryToggle.font = .systemFont(ofSize: 12, weight: .regular)
        recoveryToggle.controlSize = .small
        recoveryToggle.isEnabled = false
        recoveryToggle.state = .off
        recoveryToggle.toolTip = "Recovery needs verified conversation identity, capacity errors, Retry, Fast, manual Stop and approval state. This build has no live adapter."
        limits.add(recoveryToggle, height: 22, after: 6)
        limits.add(recoveryStatus, after: 8)
        prepareButton(recoveryTestButton, title: "Test logic", action: #selector(testRecoveryPressed))
        recoveryTestButton.toolTip = "Run synthetic retry, Fast timeout, cutoff, Stop, draft and approval checks. Does not control Codex or consume allowance."
        limits.add(buttonRow([recoveryTestButton]), height: 26)
        workingButtons.append(armButton)
    }

    private func buildSettings() {
        let connectionStatus = self.connectionStatus
        let repoLabel = DashboardLabel("Private repository", size: 12)
        connectionStatus.alignment = .right
        settings.add(DashboardRow { row in
            repoLabel.frame = NSRect(x: 0, y: 0, width: row.bounds.width - 100, height: 18)
            connectionStatus.frame = NSRect(x: row.bounds.width - 100, y: 0, width: 100, height: 18)
        }.containing(repoLabel, connectionStatus), height: 18, after: 6)
        prepareField(repository, label: "Private repository")
        repository.placeholderString = "owner/private-repository"
        settings.add(repository, height: 25, after: 10)
        prepareButton(configureButton, title: "Connect", action: #selector(configurePressed))
        prepareButton(disconnectButton, title: "Disconnect", action: #selector(disconnectPressed))
        settings.add(buttonRow([configureButton, disconnectButton]), height: 26, after: 13)
        settings.add(DashboardRule(), height: 1, after: 13)

        for checkbox in [automatic, receiveOnly] {
            checkbox.font = .systemFont(ofSize: 12, weight: .regular)
            checkbox.controlSize = .small
            checkbox.target = self
            checkbox.action = #selector(syncOptionChanged)
            settings.add(checkbox, height: 22, after: 6)
        }
        receiveOnly.toolTip = "Download shared usage without uploading this device's usage"
        scope.addItems(withTitles: ["All tools", "Codex"])
        scope.item(at: 0)?.representedObject = "all"
        scope.item(at: 1)?.representedObject = "codex"
        scope.controlSize = .small
        scope.font = .systemFont(ofSize: 12, weight: .regular)
        scope.target = self
        scope.action = #selector(syncOptionChanged)
        scope.setAccessibilityLabel("Usage to share")
        settings.add(controlRow("Share", scope, width: 165), height: 26, after: 9)

        prepareField(interval, label: "Sync interval in hours", numeric: true)
        prepareField(idleInterval, label: "Sync interval if unchanged, in hours", numeric: true)
        settings.add(fieldRow("Every", interval, suffix: "hours"), height: 26, after: 8)
        settings.add(fieldRow("If unchanged", idleInterval, suffix: "hours"), height: 26, after: 14)
        settings.add(DashboardRule(), height: 1, after: 14)

        usageSource.addItems(withTitles: ["Codex", "All devices", "This device"])
        for (index, mode) in ["codex", "combined", "local"].enumerated() {
            usageSource.item(at: index)?.representedObject = mode
        }
        usageSource.controlSize = .small
        usageSource.font = .systemFont(ofSize: 12, weight: .regular)
        usageSource.target = self
        usageSource.action = #selector(usageSourceChanged)
        usageSource.setAccessibilityLabel("Usage source")
        settings.add(controlRow("Usage source", usageSource, width: 165), height: 26, after: 9)

        themes.controlSize = .small
        themes.font = .systemFont(ofSize: 12, weight: .regular)
        themes.segmentStyle = .rounded
        themes.setWidth(78, forSegment: 0)
        themes.setWidth(78, forSegment: 1)
        themes.target = self
        themes.action = #selector(preferencesChanged)
        themes.setAccessibilityLabel("Theme")
        settings.add(controlRow("Theme", themes, width: 165), height: 26, after: 9)
        automaticPrices.controlSize = .small
        automaticPrices.font = .systemFont(ofSize: 12, weight: .regular)
        automaticPrices.target = self
        automaticPrices.action = #selector(preferencesChanged)
        settings.add(automaticPrices, height: 22, after: 9)
        let prices = NSButton()
        prepareButton(prices, title: "Prices…", action: #selector(pricesPressed))
        settings.add(buttonRow([prices]), height: 26, after: 14)
        settings.add(DashboardRule(), height: 1, after: 13)
        let importButton = NSButton(), exportButton = NSButton()
        prepareButton(importButton, title: "Import…", action: #selector(importPressed))
        prepareButton(exportButton, title: "Export…", action: #selector(exportPressed))
        settings.add(buttonRow([importButton, exportButton]), height: 26)
        workingButtons += [prices, importButton, exportButton]
    }

    private func updateOverview(_ actions: NativeActions) {
        let range = customRange ?? Dates.range(preset)
        let stale = Date().timeIntervalSince1970 - (actions.cache.quota?.timestamp ?? 0) > 360
        let revision = "\(actions.cache.timestamp)|\(actions.cache.quota?.timestamp ?? 0)|\(range.0)|\(range.1)|\(customRange == nil)|\(stale)"
        guard revision != overviewRevision else { return }
        overviewRevision = revision
        let totals = Totals(days: actions.cache.days, start: range.0, end: range.1)
        costValue.stringValue = money(totals.cost)
        tokenValue.stringValue = compact(totals.tokens)
        conversationValue.stringValue = compact(totals.conversations)
        tokenValue.toolTip = "\(totals.tokens) tokens"
        conversationValue.toolTip = "\(totals.conversations) conversations"
        costValue.toolTip = "\(money(totals.cost)) total cost"
        calendarButton.title = rangeDescription(range)
        calendarButton.toolTip = "Choose dates · \(range.0) – \(range.1)"
        presets.selectedSegment = customRange == nil ? preset : -1

        quotaRows.removeAll()
        for window in actions.cache.quota?.windows ?? [] {
            let row = DashboardQuotaRow(window: window)
            quotaRows.add(row, height: 39, after: 11)
        }
        for expiry in actions.cache.quota?.expiries ?? [] where expiry.isFinite && expiry > Date().timeIntervalSince1970 {
            let date = DashboardLabel(Dates.reset.string(from: Date(timeIntervalSince1970: expiry)), size: 11, secondary: true)
            date.alignment = .right
            if let stamp = actions.cache.quota?.expiryTimestamp {
                date.toolTip = "Banked reset expires · checked " + Dates.reset.string(from: Date(timeIntervalSince1970: stamp))
            } else { date.toolTip = "Banked reset expires" }
            date.setAccessibilityLabel("Banked reset expiry")
            quotaRows.add(date, height: 17, after: 2)
        }
        if actions.cache.quota?.expiryStatus == "unavailable" {
            quotaRows.add(DashboardLabel("Reset dates unavailable", size: 11, secondary: true), height: 17, after: 2)
        }
        if let quota = actions.cache.quota, quota.status != "fresh" || Date().timeIntervalSince1970 - quota.timestamp > 360 {
            quotaRows.add(DashboardLabel("Cached", size: 11, secondary: true), height: 17, after: 2)
        }
        quotaRule.isHidden = quotaRows.isEmpty
        quotaRows.isHidden = quotaRows.isEmpty

        modelRows.removeAll()
        let sorted = totals.models.sorted {
            if $0.value.cost != $1.value.cost { return $0.value.cost > $1.value.cost }
            return $0.key.localizedStandardCompare($1.key) == .orderedAscending
        }
        for (name, model) in sorted {
            let title = DashboardLabel(name, size: 13)
            title.lineBreakMode = .byTruncatingTail
            title.toolTip = model.names.sorted().joined(separator: "\n")
            title.setAccessibilityLabel(model.names.sorted().joined(separator: ", "))
            let amount = DashboardLabel(money(model.cost), size: 13, secondary: true)
            amount.alignment = .right
            amount.toolTip = "\(name): \(money(model.cost))"
            modelRows.add(DashboardRow { row in
                let costWidth = max(80, amount.intrinsicContentSize.width + 8)
                title.frame = NSRect(x: 0, y: 0, width: max(0, row.bounds.width - costWidth - 12), height: row.bounds.height)
                amount.frame = NSRect(x: row.bounds.width - costWidth, y: 0, width: costWidth, height: row.bounds.height)
            }.containing(title, amount), height: 23, after: 4)
        }
        modelRows.isHidden = modelRows.isEmpty
        modelRule.isHidden = modelRows.isEmpty
    }

    private func updateLimits(_ actions: NativeActions) {
        let state = actions.guardState
        limitStatus.stringValue = state.blocked ? "Cutoff reached" : state.armed ? "Armed · \(percent(state.limit))" : "Cutoff off"
        limitStatus.toolTip = state.afterReset ? "The after-reset cap is active" : "The current cap is active when armed"
        if !limitsDirty {
            currentLimit.stringValue = number(state.currentLimit)
            resetLimit.stringValue = number(state.resetLimit)
        }
        armButton.title = state.armed ? "Apply limits" : "Arm"
        recoveryStatus.stringValue = actions.recoveryTestReport?.summary ?? DesktopRecoveryAvailability.detail
        recoveryStatus.toolTip = actions.recoveryTestReport?.detail ?? recoveryToggle.toolTip
    }

    private func updateSettings(_ actions: NativeActions) {
        let saved = DashboardSyncDraft(actions.syncInfo)
        if submittedSettings == saved {
            settingsDirty = false
            submittedSettings = nil
        }
        if !settingsDirty {
            repository.stringValue = saved.repository
            automatic.state = saved.automatic ? .on : .off
            receiveOnly.state = saved.receiveOnly ? .on : .off
            scope.selectItem(at: saved.scope == "all" ? 0 : 1)
            interval.stringValue = String(saved.intervalHours)
            idleInterval.stringValue = String(saved.idleHours)
        }
        scope.isEnabled = receiveOnly.state != .on
        let isConnected = connected(actions.syncInfo)
        let devices = actions.syncInfo["devices"] as? Int ?? 0
        connectionStatus.stringValue = isConnected ? (devices > 0 ? "\(devices) devices" : "Connected") : "Not connected"
        if let stamp = actions.syncInfo["lastSync"] as? Double {
            connectionStatus.toolTip = Dates.reset.string(from: Date(timeIntervalSince1970: stamp))
        }
        configureButton.title = isConnected ? "Save" : "Connect"
        themes.selectedSegment = actions.preferences["theme"] as? String == "Nord" ? 1 : 0
        let usageMode = actions.preferences["usage_mode"] as? String ?? "codex"
        usageSource.selectItem(at: ["codex", "combined", "local"].firstIndex(of: usageMode) ?? 0)
        automaticPrices.state = actions.preferences["automatic_prices"] as? Bool == false ? .off : .on
    }

    private func updateMessage() {
        guard let actions else { return }
        var messages: [String] = []
        for message in [actions.message, actions.guardState.reason, localMessage] where !message.isEmpty {
            if !messages.contains(where: { $0.contains(message) }) { messages.append(message) }
        }
        surface.message.stringValue = messages.joined(separator: "\n")
        surface.message.toolTip = surface.message.stringValue
        surface.needsLayout = true
    }

    private func updatePreferredSize() {
        let size = NSSize(width: 450, height: selectedPage == 2 ? 560 : selectedPage == 1 ? 480 : 370)
        surface.preferredSize = size
        preferredContentSize = size
        // Preserve the parent's chosen width and the panel's top edge.
        if let window = view.window, window.contentViewController === self {
            let contentHeight = window.contentLayoutRect.height
            let targetHeight: CGFloat = selectedPage == 2 ? 560 : selectedPage == 1 ? 480 : 370
            if abs(contentHeight - targetHeight) > 1 {
                var frame = window.frame
                let extra = targetHeight - contentHeight
                frame.origin.y -= extra
                frame.size.height += extra
                if let screen = window.screen, frame.minY < screen.visibleFrame.minY {
                    frame.origin.y = screen.visibleFrame.minY
                }
                window.setFrame(frame, display: true, animate: false)
            }
        }
    }

    private func connected(_ info: [String: Any]) -> Bool {
        info["connected"] as? Bool ?? !(info["repository"] as? String ?? "").isEmpty
    }

    private func prepareButton(_ button: NSButton, title: String, action: Selector) {
        button.title = title
        button.bezelStyle = .rounded
        button.controlSize = .small
        button.font = .systemFont(ofSize: 12, weight: .regular)
        button.target = self
        button.action = action
    }

    private func prepareField(_ field: NSTextField, label: String, numeric: Bool = false) {
        field.font = .systemFont(ofSize: 12, weight: .regular)
        field.controlSize = .small
        field.bezelStyle = .roundedBezel
        field.delegate = self
        field.setAccessibilityLabel(label)
        field.toolTip = label
        if numeric { field.alignment = .right }
    }

    private func centered(_ control: NSView, width: CGFloat) -> DashboardRow {
        DashboardRow { row in
            let naturalWidth = (control as? NSButton).map { $0.intrinsicContentSize.width + 10 } ?? width
            let width = min(width, naturalWidth, row.bounds.width)
            control.frame = NSRect(x: (row.bounds.width - width) / 2, y: 0, width: width, height: row.bounds.height)
        }.containing(control)
    }

    private func controlRow(_ title: String, _ control: NSView, width: CGFloat) -> DashboardRow {
        let label = DashboardLabel(title, size: 12)
        return DashboardRow { row in
            label.frame = NSRect(x: 0, y: 5, width: max(0, row.bounds.width - width - 12), height: 19)
            control.frame = NSRect(x: row.bounds.width - width, y: 0, width: width, height: row.bounds.height)
        }.containing(label, control)
    }

    private func fieldRow(_ title: String, _ field: NSTextField, suffix: String) -> DashboardRow {
        let label = DashboardLabel(title, size: 12)
        let unit = DashboardLabel(suffix, size: 12, secondary: true)
        return DashboardRow { row in
            let suffixWidth: CGFloat = suffix == "%" ? 17 : 38
            label.frame = NSRect(x: 0, y: 5, width: max(0, row.bounds.width - 115), height: 19)
            field.frame = NSRect(x: row.bounds.width - suffixWidth - 72, y: 0, width: 64, height: row.bounds.height)
            unit.frame = NSRect(x: row.bounds.width - suffixWidth, y: 5, width: suffixWidth, height: 19)
        }.containing(label, field, unit)
    }

    private func buttonRow(_ buttons: [NSButton]) -> DashboardRow {
        DashboardRow { row in
            var x: CGFloat = 0
            for button in buttons {
                let width = max(66, button.intrinsicContentSize.width + 6)
                button.frame = NSRect(x: x, y: 0, width: width, height: row.bounds.height)
                x += width + 8
            }
        }.containing(buttons)
    }

    private func number(_ value: Double) -> String {
        decimal(value)
    }

    private func percent(_ value: Double) -> String { "\(number(value))%" }

    private func rangeDescription(_ range: (String, String)) -> String {
        if customRange == nil, preset == 4 { return "All dates" }
        guard let start = Dates.day.date(from: range.0), let end = Dates.day.date(from: range.1) else { return "Choose dates" }
        let year = Calendar.current.component(.year, from: Date())
        let format = Calendar.current.component(.year, from: start) == year && Calendar.current.component(.year, from: end) == year ? Dates.short : Dates.long
        if range.0 == range.1 { return format.string(from: start) }
        return "\(format.string(from: start)) – \(format.string(from: end))"
    }

    func controlTextDidChange(_ notification: Notification) {
        guard let field = notification.object as? NSTextField else { return }
        if field === currentLimit || field === resetLimit { limitsDirty = true }
        if field === repository || field === interval || field === idleInterval {
            settingsDirty = true
            submittedSettings = nil
        }
        localMessage = ""
        updateMessage()
    }

    @objc private func changePage(_ sender: NSButton) {
        view.window?.makeFirstResponder(nil)
        actions?.page = sender.tag
        localMessage = ""
        refresh()
    }

    @objc private func changePreset(_ sender: NSSegmentedControl) {
        preset = sender.selectedSegment
        customRange = nil
        actions?.rangePreset = preset
        actions?.selectedRange = nil
        refresh()
    }

    @objc private func showDates(_ sender: NSButton) {
        if datePopover?.isShown == true { datePopover?.close(); return }
        if datePopover == nil {
            let editor = DashboardDateEditor()
            editor.apply = { [weak self] start, end in
                guard let self else { return }
                self.customRange = (Dates.day.string(from: start), Dates.day.string(from: end))
                self.actions?.selectedRange = self.customRange
                self.datePopover?.close()
                self.refresh()
            }
            editor.cancel = { [weak self] in self?.datePopover?.close() }
            let popover = NSPopover()
            popover.behavior = .transient
            popover.animates = false
            popover.contentViewController = editor
            dateEditor = editor
            datePopover = popover
        }
        let range = customRange ?? Dates.range(preset)
        let earliest = actions?.cache.days.map(\.day).min().flatMap { Dates.day.date(from: $0) }
        dateEditor?.setDates(start: range.0 == "0000-01-01" ? earliest ?? Date() : Dates.day.date(from: range.0) ?? Date(), end: Dates.day.date(from: range.1) ?? Date())
        dateEditor?.view.appearance = surface.appearance
        datePopover?.show(relativeTo: sender.bounds, of: sender, preferredEdge: .maxY)
    }

    @objc private func armPressed() {
        view.window?.makeFirstResponder(nil)
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.isLenient = false
        let values = [currentLimit, resetLimit].map { field -> Double? in
            let text = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            let separator = formatter.decimalSeparator ?? "."
            let allowed = CharacterSet.decimalDigits.union(CharacterSet(charactersIn: separator))
            guard !text.isEmpty, text.unicodeScalars.allSatisfy({ allowed.contains($0) }),
                  text.components(separatedBy: separator).count <= 2 else { return nil }
            return formatter.number(from: text)?.doubleValue
        }
        guard let current = values[0], let reset = values[1], current.isFinite, reset.isFinite,
              (0...100).contains(current), (0...100).contains(reset) else {
            localMessage = "Enter limits from 0 to 100%."
            updateMessage()
            return
        }
        limitsDirty = false
        send("arm", options: ["currentLimit": current, "resetLimit": reset])
    }

    @objc private func syncOptionChanged() {
        settingsDirty = true
        submittedSettings = nil
        scope.isEnabled = receiveOnly.state != .on
        localMessage = ""
        updateMessage()
    }

    @objc private func configurePressed() {
        view.window?.makeFirstResponder(nil)
        guard let every = Int(interval.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)),
              let idle = Int(idleInterval.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)),
              (1...168).contains(every), (every...168).contains(idle) else {
            localMessage = "Use 1–168 hours; “If unchanged” must be at least “Every”."
            updateMessage()
            return
        }
        let repo = repository.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !repo.isEmpty else {
            localMessage = "Enter a private repository as owner/name."
            updateMessage()
            return
        }
        let draft = DashboardSyncDraft(repository: repo, automatic: automatic.state == .on,
            receiveOnly: receiveOnly.state == .on, scope: scope.selectedItem?.representedObject as? String ?? "codex",
            intervalHours: every, idleHours: idle)
        submittedSettings = draft
        send("configure", options: draft.options)
    }

    @objc private func preferencesChanged() {
        send("preferences", options: ["theme": themes.selectedSegment == 1 ? "Nord" : "Pearl",
                                       "automatic_prices": automaticPrices.state == .on])
    }

    @objc private func usageSourceChanged() {
        send("preferences", options: ["usage_mode": usageSource.selectedItem?.representedObject as? String ?? "codex"])
    }

    private func send(_ name: String, options: [String: Any] = [:]) {
        localMessage = ""
        updateMessage()
        actions?.action(name, options: options)
    }

    @objc private func refreshPressed() { send("refresh") }
    @objc private func syncPressed() { send("sync") }
    @objc private func disarmPressed() { limitsDirty = false; send("disarm") }
    @objc private func permissionPressed() { send("permission") }
    @objc private func testRecoveryPressed() { send("testRecovery") }
    @objc private func disconnectPressed() { settingsDirty = false; submittedSettings = nil; send("disconnect") }
    @objc private func pricesPressed() { send("prices") }
    @objc private func importPressed() { send("import") }
    @objc private func exportPressed() { send("export") }
}

private struct DashboardSyncDraft: Equatable {
    var repository: String
    var automatic: Bool
    var receiveOnly: Bool
    var scope: String
    var intervalHours: Int
    var idleHours: Int

    init(repository: String, automatic: Bool, receiveOnly: Bool, scope: String, intervalHours: Int, idleHours: Int) {
        self.repository = repository; self.automatic = automatic; self.receiveOnly = receiveOnly
        self.scope = scope; self.intervalHours = intervalHours; self.idleHours = idleHours
    }

    init(_ info: [String: Any]) {
        repository = info["repository"] as? String ?? ""
        automatic = info["automatic"] as? Bool ?? false
        receiveOnly = info["receiveOnly"] as? Bool ?? false
        scope = info["scope"] as? String ?? "codex"
        intervalHours = info["intervalHours"] as? Int ?? 1
        idleHours = info["idleHours"] as? Int ?? 3
    }

    var options: [String: Any] {
        ["repository": repository, "automatic": automatic, "receiveOnly": receiveOnly,
         "scope": scope, "intervalHours": intervalHours, "idleHours": idleHours]
    }
}

private struct DashboardPalette {
    var nord = false
    var background: NSColor { nord ? color(0x2E3440) : color(0xFAF9F6) }
    var rail: NSColor { nord ? color(0x292F3A) : color(0xF0EFEB) }
    var text: NSColor { nord ? color(0xECEFF4) : color(0x303A40) }
    var secondary: NSColor { nord ? color(0xAFBACB) : color(0x6C7377) }
    var selected: NSColor { nord ? color(0x434C5E) : color(0xDFE5E5) }
    var accent: NSColor { nord ? color(0x88C0D0) : color(0x456875) }
    var rule: NSColor { nord ? color(0x434C5E) : color(0xE3E3DE) }
    private func color(_ hex: UInt32) -> NSColor {
        NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
                blue: CGFloat(hex & 255) / 255, alpha: 1)
    }
}

private class DashboardFlippedView: NSView {
    override var isFlipped: Bool { true }
}

private final class DashboardLabel: NSTextField {
    let secondary: Bool
    let wraps: Bool
    init(_ text: String = "", size: CGFloat = 12, secondary: Bool = false, wraps: Bool = false) {
        self.secondary = secondary
        self.wraps = wraps
        super.init(frame: .zero)
        stringValue = text
        font = .systemFont(ofSize: size, weight: .regular)
        isEditable = false; isSelectable = true; isBordered = false; drawsBackground = false
        lineBreakMode = wraps ? .byWordWrapping : .byClipping
        maximumNumberOfLines = wraps ? 0 : 1
        cell?.wraps = wraps
        cell?.isScrollable = !wraps
    }
    required init?(coder: NSCoder) { nil }
    func height(for width: CGFloat) -> CGFloat {
        let rect = (stringValue as NSString).boundingRect(with: NSSize(width: max(1, width - 6), height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: font ?? NSFont.systemFont(ofSize: 12)])
        return ceil(rect.height) + 4
    }
}

private final class DashboardRow: DashboardFlippedView {
    private let arrange: (DashboardRow) -> Void
    init(_ arrange: @escaping (DashboardRow) -> Void) { self.arrange = arrange; super.init(frame: .zero) }
    required init?(coder: NSCoder) { nil }
    func containing(_ children: NSView...) -> DashboardRow { containing(children) }
    func containing(_ children: [NSView]) -> DashboardRow { children.forEach(addSubview); return self }
    override func layout() { super.layout(); arrange(self) }
}

private final class DashboardColumn: DashboardFlippedView {
    private struct Item { var view: NSView; var height: CGFloat?; var after: CGFloat }
    private var items: [Item] = []
    var isEmpty: Bool { items.isEmpty }
    func add(_ view: NSView, height: CGFloat? = nil, after: CGFloat = 0) {
        items.append(Item(view: view, height: height, after: after)); addSubview(view)
    }
    func removeAll() { subviews.forEach { $0.removeFromSuperview() }; items.removeAll() }
    private func height(_ item: Item, width: CGFloat) -> CGFloat {
        if let height = item.height { return height }
        if let column = item.view as? DashboardColumn { return column.height(for: width) }
        if let label = item.view as? DashboardLabel { return label.height(for: width) }
        return max(0, item.view.intrinsicContentSize.height)
    }
    func height(for width: CGFloat) -> CGFloat {
        let visible = items.filter { !$0.view.isHidden }
        return visible.enumerated().reduce(0) { total, pair in
            total + height(pair.element, width: width) + (pair.offset < visible.count - 1 ? pair.element.after : 0)
        }
    }
    override func layout() {
        super.layout()
        var y: CGFloat = 0
        for item in items where !item.view.isHidden {
            let height = height(item, width: bounds.width)
            item.view.frame = NSRect(x: 0, y: y, width: bounds.width, height: height)
            item.view.needsLayout = true
            y += height + item.after
        }
    }
}

private final class DashboardRule: NSView {
    var color = DashboardPalette().rule { didSet { needsDisplay = true } }
    override func draw(_ dirtyRect: NSRect) { color.setFill(); bounds.fill() }
    override func isAccessibilityElement() -> Bool { false }
}

private final class DashboardNavigationButton: NSButton {
    var palette = DashboardPalette() {
        didSet {
            contentTintColor = state == .on ? palette.accent : palette.secondary
            needsDisplay = true
        }
    }
    override func draw(_ dirtyRect: NSRect) {
        if state == .on || isHighlighted {
            palette.selected.setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 6, yRadius: 6).fill()
        }
        super.draw(dirtyRect)
    }
}

private final class DashboardSurface: DashboardFlippedView {
    var palette = DashboardPalette() { didSet { needsDisplay = true } }
    var preferredSize = NSSize(width: 450, height: 370) { didSet { invalidateIntrinsicContentSize() } }
    override var intrinsicContentSize: NSSize { preferredSize }
    override var isOpaque: Bool { true }
    let timestamp = DashboardLabel(size: 11, secondary: true)
    let refreshButton = NSButton(), syncButton = NSButton()
    let message = DashboardLabel(size: 11, secondary: true, wraps: true)
    let navigation: [DashboardNavigationButton]
    private let scroll = NSScrollView(), messageScroll = NSScrollView()
    private let messageDocument = DashboardFlippedView()
    var body: DashboardColumn? {
        didSet {
            guard body !== oldValue else { return }
            scroll.documentView = body
            needsLayout = true
        }
    }

    init() {
        navigation = [("square.grid.2x2", "Overview"), ("hand.raised", "Limits"), ("gearshape", "Settings")].map { symbol, label in
            let button = DashboardNavigationButton()
            button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: 15, weight: .regular))
            button.imagePosition = .imageOnly
            button.isBordered = false
            button.setButtonType(.toggle)
            button.title = ""
            button.toolTip = label
            button.setAccessibilityLabel(label)
            (button.cell as? NSButtonCell)?.highlightsBy = []
            (button.cell as? NSButtonCell)?.showsStateBy = []
            return button
        }
        super.init(frame: NSRect(x: 0, y: 0, width: 450, height: 370))
        autoresizingMask = [.width, .height]
        for scrollView in [scroll, messageScroll] {
            scrollView.drawsBackground = false
            scrollView.borderType = .noBorder
            scrollView.hasVerticalScroller = true
            scrollView.hasHorizontalScroller = false
            scrollView.autohidesScrollers = true
            scrollView.scrollerStyle = .overlay
            addSubview(scrollView)
        }
        messageDocument.addSubview(message)
        messageScroll.documentView = messageDocument
        for child in navigation + [timestamp, refreshButton, syncButton] as [NSView] { addSubview(child) }
    }
    required init?(coder: NSCoder) { nil }

    override func draw(_ dirtyRect: NSRect) {
        palette.background.setFill(); bounds.fill()
        palette.rail.setFill(); NSRect(x: 0, y: 0, width: 36, height: bounds.height).fill()
        palette.rule.setFill(); NSRect(x: 35, y: 0, width: 1, height: bounds.height).fill()
    }

    func applyPalette() {
        func apply(_ view: NSView) {
            if let label = view as? DashboardLabel { label.textColor = label.secondary ? palette.secondary : palette.text }
            if let rule = view as? DashboardRule { rule.color = palette.rule }
            if let button = view as? NSButton, !button.isBordered, !(button is DashboardNavigationButton) {
                button.contentTintColor = palette.secondary
            }
            view.subviews.forEach(apply)
        }
        subviews.forEach(apply)
    }

    func scrollToTop() { scroll.contentView.scroll(to: .zero); scroll.reflectScrolledClipView(scroll.contentView) }

    override func layout() {
        super.layout()
        let x: CGFloat = 53
        let width = max(1, bounds.width - x - 18)
        for (index, button) in navigation.enumerated() {
            button.frame = NSRect(x: 4, y: 15 + CGFloat(index) * 38, width: 28, height: 28)
        }
        let footerY = bounds.height - 35
        timestamp.frame = NSRect(x: x, y: footerY + 4, width: max(75, width - 150), height: 18)
        refreshButton.frame = NSRect(x: bounds.width - 155, y: footerY, width: 76, height: 25)
        syncButton.frame = NSRect(x: bounds.width - 74, y: footerY, width: 57, height: 25)
        let messageHeight = message.stringValue.isEmpty ? 0 : min(92, message.height(for: width))
        let messageY = footerY - (messageHeight > 0 ? messageHeight + 9 : 0)
        messageScroll.isHidden = messageHeight == 0
        messageScroll.frame = NSRect(x: x, y: messageY, width: width, height: messageHeight)
        messageDocument.frame = NSRect(x: 0, y: 0, width: width, height: message.height(for: width))
        message.frame = messageDocument.bounds
        scroll.frame = NSRect(x: x, y: 16, width: width, height: max(1, messageY - 28))
        if let body {
            body.frame = NSRect(x: 0, y: 0, width: width, height: max(scroll.bounds.height, body.height(for: width)))
            body.needsLayout = true
        }
    }
}

private final class DashboardQuotaRow: DashboardFlippedView {
    private let percentage = DashboardLabel(size: 13)
    private let reset = DashboardLabel(size: 11, secondary: true)
    private let progress = NSProgressIndicator()
    init(window: QuotaWindow) {
        super.init(frame: .zero)
        if let percent = window.percent, percent.isFinite, (0...100).contains(percent) {
            percentage.stringValue = "\(decimal(percent))%"
            progress.doubleValue = percent
        } else {
            percentage.stringValue = "—"
            progress.doubleValue = 0
        }
        if let timestamp = window.reset, timestamp.isFinite, timestamp > 0 {
            reset.stringValue = Dates.reset.string(from: Date(timeIntervalSince1970: timestamp))
        }
        percentage.toolTip = "\(window.label) quota used"
        percentage.setAccessibilityLabel("\(window.label) quota used")
        reset.toolTip = "\(window.label) resets"
        reset.setAccessibilityLabel("\(window.label) reset date")
        reset.alignment = .right
        progress.style = .bar
        progress.controlSize = .small
        progress.isIndeterminate = false
        progress.usesThreadedAnimation = false
        progress.minValue = 0
        progress.maxValue = 100
        progress.toolTip = window.label
        progress.setAccessibilityLabel("\(window.label) quota used")
        for child in [percentage, reset, progress] as [NSView] { addSubview(child) }
    }
    required init?(coder: NSCoder) { nil }
    override func layout() {
        super.layout()
        percentage.frame = NSRect(x: 0, y: 0, width: 85, height: 20)
        reset.frame = NSRect(x: 86, y: 2, width: max(0, bounds.width - 86), height: 18)
        progress.frame = NSRect(x: 0, y: 26, width: bounds.width, height: 8)
    }
}

private final class DashboardDateEditor: NSViewController {
    var apply: ((Date, Date) -> Void)?
    var cancel: (() -> Void)?
    private let start = NSDatePicker(), end = NSDatePicker()
    override func loadView() {
        let root = DashboardFlippedView(frame: NSRect(x: 0, y: 0, width: 278, height: 144))
        view = root
        preferredContentSize = root.frame.size
        for (index, pair) in [("From", start), ("Through", end)].enumerated() {
            let label = DashboardLabel(pair.0, size: 12)
            label.frame = NSRect(x: 16, y: 21 + CGFloat(index) * 37, width: 62, height: 20)
            let picker = pair.1
            picker.datePickerStyle = .textFieldAndStepper
            picker.datePickerElements = .yearMonthDay
            picker.font = .systemFont(ofSize: 12, weight: .regular)
            picker.controlSize = .small
            picker.frame = NSRect(x: 87, y: 16 + CGFloat(index) * 37, width: 174, height: 26)
            picker.target = self
            picker.action = #selector(dateChanged(_:))
            picker.setAccessibilityLabel(pair.0 == "From" ? "Start date" : "End date")
            root.addSubview(label); root.addSubview(picker)
        }
        let cancelButton = NSButton(title: "Cancel", target: self, action: #selector(cancelPressed))
        let applyButton = NSButton(title: "Apply", target: self, action: #selector(applyPressed))
        for (index, button) in [cancelButton, applyButton].enumerated() {
            button.bezelStyle = .rounded
            button.controlSize = .small
            button.font = .systemFont(ofSize: 12, weight: .regular)
            button.frame = NSRect(x: 99 + CGFloat(index) * 82, y: 104, width: 78, height: 26)
            root.addSubview(button)
        }
        applyButton.keyEquivalent = "\r"
        cancelButton.keyEquivalent = "\u{1b}"
    }
    func setDates(start: Date, end: Date) {
        _ = view
        self.start.dateValue = start
        self.end.dateValue = max(start, end)
    }
    @objc private func dateChanged(_ sender: NSDatePicker) {
        if start.dateValue > end.dateValue {
            if sender === start { end.dateValue = start.dateValue } else { start.dateValue = end.dateValue }
        }
    }
    @objc private func applyPressed() { apply?(start.dateValue, end.dateValue) }
    @objc private func cancelPressed() { cancel?() }
}
