from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, QUrl, QPointF, Qt, QMetaObject, Q_ARG
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtTest import QTest
import shiboken6

from qt_support import QtCase, APP
from splitrail_desktop import demo, sync
from splitrail_desktop.pricing import load_overrides
from splitrail_desktop.quota import BankedResetStatus, format_local_reset
from splitrail_desktop.processes import CodexProcess
from test_quota_guard import snapshot as guard_snapshot

QML = Path(__file__).parents[1] / 'src' / 'splitrail_desktop' / 'qml'


class QmlTests(QtCase):
    def setUp(self):
        super().setUp()
        self.warnings = []
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda messages: self.warnings.extend(m.toString() for m in messages))
        self.engine.rootContext().setContextProperty('bridge', self.controller)
        self.engine.rootContext().setContextProperty('appFont', APP.font().family())
        self.engine.load(QUrl.fromLocalFile(str(QML / 'Main.qml')))
        self.assertTrue(self.engine.rootObjects(), self.warnings)
        self.window = self.engine.rootObjects()[0]
        QTest.qWait(30)
        self.addCleanup(self.destroy_engine)

    def destroy_engine(self):
        self.controller.close()
        shiboken6.delete(self.engine)
        self.assertEqual(self.warnings, [])

    def item(self, name):
        result = self.window.findChild(QObject, name)
        if result is None:
            pending = [self.window.contentItem()]
            while pending:
                candidate = pending.pop()
                if candidate.objectName() == name:
                    result = candidate
                    break
                pending.extend(candidate.childItems())
        self.assertIsNotNone(result, name)
        return result

    def click(self, item):
        if isinstance(item, str):
            item = self.item(item)
        self.assertTrue(item.property('visible'))
        self.assertTrue(item.property('enabled'))
        pos = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        self.assertGreaterEqual(pos.y(), 0)
        self.assertLess(pos.y(), self.window.height())
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, pos)
        QTest.qWait(30)

    def expression(self, obj, code):
        expr = QQmlExpression(self.engine.contextForObject(obj), obj, code)
        value, undefined = expr.evaluate()
        self.assertFalse(expr.hasError(), expr.error().toString())
        return value

    def dashboard(self):
        self.controller.finishSetup()
        self.controller._accept_usage(demo.usage())
        self.controller.quota_snapshot = demo.quota()
        self.controller.tick()
        QTest.qWait(30)
        return self.item('dashboardView')

    def test_menu_bar_close_unloads_dashboard_and_reopen_restores_page(self):
        dashboard = self.dashboard()
        dashboard.setProperty('page', 'History')
        self.controller._state['menuBarMode'] = True
        self.controller._emit()
        self.window.close()
        QTest.qWait(30)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.controller.closed)
        self.assertFalse(self.controller._clock.isActive())
        self.assertIsNone(self.window.findChild(QObject, 'dashboardView'))
        self.window.show()
        QTest.qWait(30)
        self.assertEqual(self.item('dashboardView').property('page'), 'History')
        self.assertTrue(self.controller._clock.isActive())

    def test_sync_intervals_save_and_reload_with_validated_hours(self):
        dashboard = self.dashboard()
        with patch('splitrail_desktop.sync._private_repo', return_value={'private': True}):
            sync.configure('example/private', automatic=True)
        self.controller._load_settings()
        self.controller._emit()
        dashboard.setProperty('page', 'Settings')
        QTest.qWait(30)
        self.assertEqual(self.item('syncIntervalHours').property('text'), '1')
        self.assertEqual(self.item('syncIdleHours').property('text'), '3')
        self.assertFalse(self.item('saveSyncTiming').property('enabled'))
        self.item('syncIntervalHours').setProperty('text', '2')
        self.item('syncIdleHours').setProperty('text', '6')
        with patch.object(self.controller, 'refresh'):
            self.click('saveSyncTiming')
            self.wait_for(lambda: not self.controller.state['syncBusy'])
        self.assertEqual(sync.settings()['interval_hours'], 2)
        self.assertEqual(sync.settings()['idle_hours'], 6)
        dashboard.setProperty('page', 'Overview')
        QTest.qWait(30)
        dashboard.setProperty('page', 'Settings')
        QTest.qWait(30)
        self.assertEqual(self.item('syncIntervalHours').property('text'), '2')
        self.assertEqual(self.item('syncIdleHours').property('text'), '6')
        for value in ('', '0', '1', '169'):
            self.item('syncIdleHours').setProperty('text', value)
            self.assertFalse(self.item('saveSyncTiming').property('enabled'))

    def test_weekly_pace_marker_appears_on_both_quota_bars(self):
        self.dashboard()
        now = datetime.now(timezone.utc)
        weekly = replace(self.controller.quota_snapshot.base_weekly, resets_at=now + timedelta(days=3, hours=12))
        self.controller.quota_snapshot = replace(self.controller.quota_snapshot, windows=(weekly,))
        self.controller.tick()
        QTest.qWait(30)
        for bar_name in ('overviewQuotaBar', 'quotaPageBar'):
            if bar_name == 'quotaPageBar':
                self.item('dashboardView').setProperty('page', 'Quota')
                QTest.qWait(30)
            bar = self.item(bar_name)
            marker = bar.findChild(QObject, 'paceMarker')
            self.assertIsNotNone(marker)
            self.assertTrue(marker.property('visible'))
            self.assertAlmostEqual(marker.property('x') + marker.property('width') / 2,
                                   bar.property('width') / 2, delta=2)

        self.controller.quota_snapshot = replace(self.controller.quota_snapshot,
                                                 windows=(replace(weekly, resets_at=now - timedelta(minutes=1)),))
        self.controller.tick()
        QTest.qWait(30)
        self.assertFalse(self.item('quotaPageBar').findChild(QObject, 'paceMarker').property('visible'))

    def test_compact_quota_fits_small_windows_and_restores_dashboard(self):
        dashboard = self.dashboard()
        self.click('navigationLimits')
        for theme in ('Pearl', 'Nord'):
            self.controller.setTheme(theme)
            for width, height in ((280, 110), (600, 110), (370, 270), (900, 230)):
                self.window.resize(width, height)
                QTest.qWait(30)
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                self.assertTrue(self.item('compactQuotaView').property('visible'))
                self.assertFalse(self.item('fullDashboard').property('visible'))
                self.assertFalse(self.controller._process_view_visible)
                for name in ('compactQuotaUsage', 'compactQuotaBar', 'compactQuotaAge'):
                    item = self.item(name)
                    start = item.mapToScene(QPointF())
                    end = item.mapToScene(QPointF(item.width(), item.height()))
                    self.assertGreaterEqual(start.x(), 0, name)
                    self.assertGreaterEqual(start.y(), 0, name)
                    self.assertLessEqual(end.x(), width, name)
                    self.assertLessEqual(end.y(), height, name)
                self.assertFalse(self.item('compactQuotaUsage').property('truncated'))
                self.assertFalse(self.item('compactQuotaAge').property('truncated'))
            self.window.resize(960, 680)
            QTest.qWait(30)
            self.assertFalse(self.item('compactQuotaView').property('visible'))
            self.assertTrue(self.item('fullDashboard').property('visible'))
            self.assertEqual(dashboard.property('page'), 'Limits')
            self.assertTrue(self.controller._process_view_visible)

    def test_compact_quota_handles_missing_cached_and_live_data(self):
        self.controller.finishSetup()
        self.window.resize(280, 110)
        QTest.qWait(30)
        self.assertEqual(self.item('compactQuotaUsage').property('text'), 'Unavailable')
        self.assertEqual(self.item('compactQuotaAge').property('text'), 'No weekly quota data')
        self.assertFalse(self.item('compactQuotaBar').findChild(QObject, 'paceMarker').property('visible'))

        snapshot = replace(demo.quota(), refreshed_at=datetime.now(timezone.utc)-timedelta(seconds=90))
        for used, stale in ((38, True), (0, False), (100, False)):
            weekly = replace(snapshot.base_weekly, percent_used=used, percent_remaining=100-used)
            self.controller.quota_snapshot = replace(snapshot, windows=(weekly,), stale=stale)
            self.controller.tick()
            QTest.qWait(30)
            self.assertEqual(self.item('compactQuotaUsage').property('text'), f'{used}%')
            self.assertEqual(self.item('compactQuotaBar').property('used'), used)
            self.assertEqual(self.item('compactQuotaAge').property('text').startswith('Cached'), stale)
            self.assertIn('Updated 1m ago', self.item('compactQuotaAge').property('text'))
            self.assertFalse(self.item('compactQuotaAge').property('truncated'))

        with patch.object(self.controller, '_start_quota_refresh') as refresh:
            QTest.keyClick(self.window, Qt.Key_R, Qt.ControlModifier)
            QTest.qWait(30)
            refresh.assert_called_once()

    def test_dashboard_keeps_navigation_while_shedding_optional_sections(self):
        self.dashboard()
        stages = (
            (1280, 860, True, True, True, True),
            (960, 680, True, True, True, True),
            (720, 440, True, True, True, True),
            (640, 420, True, True, True, True),
            (620, 420, True, True, False, True),
            (580, 400, True, True, False, False),
            (460, 320, True, True, False, False),
            (360, 280, False, False, False, False),
            (280, 700, False, False, False, False),
            (280, 280, False, False, False, False),
            (960, 240, False, False, False, False),
        )
        for theme in ('Pearl', 'Nord'):
            self.controller.setTheme(theme)
            for width, height, header, dates, activity, models in stages:
                with self.subTest(theme=theme, size=(width, height)):
                    self.window.resize(width, height)
                    QTest.qWait(30)
                    self.assertFalse(self.item('compactQuotaView').property('visible'))
                    self.assertTrue(self.item('fullDashboard').property('visible'))
                    self.assertTrue(self.item('dashboardSidebar').property('visible'))
                    self.assertEqual(self.item('dashboardSidebar').width(), 48)
                    for name, visible in (('dashboardHeader', header), ('dashboardDates', dates),
                                          ('activityChartLoader', activity), ('overviewModelsLoader', models)):
                        self.assertEqual(self.item(name).property('visible'), visible, name)
                    self.assertEqual(self.item('activityChartLoader').property('item') is not None, activity)
                    self.assertEqual(self.item('overviewModelsLoader').property('item') is not None, models)
                    for name in ('overviewQuotaCard', 'overviewCostCard', 'overviewTokensCard'):
                        item = self.item(name)
                        self.assertTrue(item.property('visible'), name)
                        start = item.mapToScene(QPointF())
                        end = item.mapToScene(QPointF(item.width(), item.height()))
                        self.assertGreaterEqual(start.x(), 0, name)
                        self.assertGreaterEqual(start.y(), 0, name)
                        self.assertLessEqual(end.x(), width, name)
                        self.assertLessEqual(end.y(), height, name)
                    for name in ('overviewCostValue', 'overviewTokensValue'):
                        self.assertFalse(self.item(name).property('truncated'), name)

    def test_quota_guard_fields_arm_and_disarm_without_signalling_real_processes(self):
        dashboard = self.dashboard()
        now = datetime.now(timezone.utc)
        self.controller.quota_snapshot = guard_snapshot(now=now, reset=now+timedelta(days=3))
        dashboard.setProperty('page', 'Limits')
        QTest.qWait(50)
        self.item('quotaCurrentLimit').setProperty('text', '92.5')
        self.item('quotaResetLimit').setProperty('text', '4')
        with patch.object(self.controller, 'refreshProcesses'):
            QMetaObject.invokeMethod(self.item('armQuotaGuard'), 'clicked')
            QTest.qWait(30)
            self.assertTrue(self.controller.guardState['armed'])
            self.assertEqual(self.controller.guardState['activeLimit'], 92.5)
            self.assertFalse(self.item('quotaCurrentLimit').property('enabled'))
            self.assertEqual(self.item('armQuotaGuard').property('text'), 'Disable cutoff')
            QMetaObject.invokeMethod(self.item('armQuotaGuard'), 'clicked')
            QTest.qWait(30)
            self.assertFalse(self.controller.guardState['armed'])

    def test_process_rows_show_identity_and_selection(self):
        dashboard = self.dashboard()
        dashboard.setProperty('page', 'Limits')
        QTest.qWait(50)
        c = self.controller
        c._processes = (CodexProcess(123, 1000, '/example/bin/codex', '/example/project', 'CLI session', 'sleeping'),)
        c._update_guard()
        QTest.qWait(30)
        control = self.item('protectProcess-0')
        self.assertTrue(control.property('checked'))
        control.setProperty('checked', False)
        QMetaObject.invokeMethod(control, 'clicked')
        self.assertFalse(c.guardState['processes'][0]['protected'])
        details = self.item('processInfo-0').property('explanation')
        self.assertIn('/example/bin/codex', details)
        self.assertIn('sleeping', details)

    def test_quota_page_opens_before_first_quota_read(self):
        self.controller.finishSetup()
        self.assertIsNone(self.controller.quota_snapshot)
        self.item('dashboardView').setProperty('page', 'Quota')
        QTest.qWait(50)
        self.assertEqual(self.item('bankedResetCount').property('text'), 'Unavailable')
        self.assertIsNone(self.window.findChild(QObject, 'armQuotaGuard'))
        self.assertFalse(self.controller._process_view_visible)
        self.click('navigationLimits')
        self.assertEqual(self.item('quotaCurrentLimit').property('text'), '95')
        self.assertFalse(self.item('dateSelector').property('visible'))
        self.assertTrue(self.controller._process_view_visible)
        self.click('navigationQuota')
        self.assertFalse(self.controller._process_view_visible)

    def test_limits_tooltips_work_on_hover_and_keyboard_in_both_themes(self):
        self.dashboard()
        self.click('navigationLimits')
        for theme in ('Pearl', 'Nord'):
            self.controller.setTheme(theme)
            for width in (960, 1280):
                self.window.resize(width, 700)
                QTest.qWait(30)
                button = self.item('resetLimitInfo')
                pos = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
                QTest.mouseMove(self.window, pos)
                QTest.qWait(400)
                tooltip = self.item('resetLimitInfoTooltip')
                self.assertTrue(tooltip.property('visible'))
                self.assertIn('5% of the fresh allowance', tooltip.property('text'))
                QTest.mouseMove(self.window, QPointF(10, 10).toPoint())
                QTest.qWait(200)
                self.assertFalse(tooltip.property('visible'))
                button.forceActiveFocus()
                QTest.qWait(30)
                self.assertTrue(tooltip.property('visible'))
                QTest.keyClick(self.window, Qt.Key_Escape)
                QTest.qWait(200)
                self.assertFalse(tooltip.property('visible'))
                action = self.item('armQuotaGuard')
                corner = action.mapToScene(QPointF(action.width(), action.height()))
                self.assertLessEqual(corner.x(), width)
                self.assertLessEqual(corner.y(), 700)

    def test_quota_page_names_codex_and_shows_exact_banked_expiries(self):
        self.dashboard()
        expiry = datetime(2026, 10, 1, 4, 5, 6, tzinfo=timezone.utc)
        banks = BankedResetStatus('fresh', count=2, expiries=(expiry, None),
                                  expiry_details_complete=True, observed_at=datetime.now(timezone.utc))
        self.controller.quota_snapshot = replace(self.controller.quota_snapshot, banked_resets=banks)
        self.controller.tick()
        self.item('dashboardView').setProperty('page', 'Quota')
        QTest.qWait(30)
        self.assertEqual(self.item('quotaProviderLabel').property('text'), 'Codex weekly allowance')
        self.assertEqual(self.item('bankedResetCount').property('text'), '2 available')
        self.assertEqual(self.item('bankExpiry-0').property('text'), 'Expires ' + format_local_reset(expiry))
        self.assertEqual(self.item('bankExpiry-1').property('text'), 'Does not expire')

        self.controller.quota_snapshot = replace(self.controller.quota_snapshot,
                                                 banked_resets=BankedResetStatus('unavailable'))
        self.controller.tick()
        QTest.qWait(30)
        self.assertEqual(self.item('bankedResetCount').property('text'), 'Unavailable')
        self.assertIn('unavailable', self.item('bankedResetNotice').property('text'))

    def test_onboarding_exclusively_owns_window_and_skip_opens_dashboard(self):
        self.item('onboardingView')
        self.assertIsNone(self.window.findChild(QObject, 'dashboardView'))
        self.assertEqual(self.item('setupPrimary').property('text'), 'Get started')
        self.click('setupSkip')
        self.item('dashboardView')
        self.assertIsNone(self.window.findChild(QObject, 'onboardingView'))

    def test_setup_steps_only_create_after_explicit_sync_button(self):
        with patch('splitrail_desktop.sync.account', return_value='example-user'), \
             patch('splitrail_desktop.desktop.shutil.which', return_value='/example/gh'), \
             patch('splitrail_desktop.sync.create_private_repo', return_value='example/private') as create, \
             patch('splitrail_desktop.sync._private_repo', return_value={'private': True}):
            self.click('setupPrimary')
            self.wait_for(lambda: not self.controller.state['setupBusy'])
            self.assertEqual(self.controller.state['setupStage'], 'account')
            self.click('setupPrimary')
            self.assertEqual(self.controller.state['setupStage'], 'repository')
            create.assert_not_called()
            self.item('repositoryField').setProperty('text', 'private')
            self.click('setupPrimary')
            self.wait_for(lambda: not self.controller.state['setupBusy'])
            create.assert_called_once_with('private')
            self.assertEqual(self.controller.state['setupStage'], 'ready')
            self.click('setupPrimary')
            self.item('dashboardView')

    def test_failed_configuration_keeps_created_destination_in_form(self):
        c = self.controller
        c._state.update(setupStage='repository', setupLogin='example-user')
        c._emit()
        with patch('splitrail_desktop.sync.create_private_repo', return_value='example/private') as create, \
             patch('splitrail_desktop.sync.configure', side_effect=sync.SyncError('Temporary failure')):
            self.item('repositoryField').setProperty('text', 'private')
            self.click('setupPrimary')
            self.wait_for(lambda: not c.state['setupBusy'])
            self.assertTrue(self.item('onboardingView').property('existing'))
            self.assertEqual(self.item('repositoryField').property('text'), 'example/private')
            self.click('setupPrimary')
            self.wait_for(lambda: not c.state['setupBusy'])
            create.assert_called_once()

    def test_setup_primary_and_skip_fit_small_window_at_each_stage(self):
        self.window.resize(660, 680)
        for stage in ('welcome', 'account', 'repository', 'auth', 'ready'):
            self.controller._state.update(setupStage=stage, setupLogin='example-user', setupCode='ABCD-1234')
            self.controller._emit()
            QTest.qWait(30)
            for name in ('setupPrimary', 'setupSkip'):
                button = self.item(name)
                if button.property('visible'):
                    bottom = button.mapToScene(QPointF(button.width(), button.height()))
                    self.assertLessEqual(bottom.y(), self.window.height(), (stage, name))
                    self.assertLessEqual(bottom.x(), self.window.width(), (stage, name))

    def test_both_themes_have_readable_primary_text_and_no_dashboard_during_setup(self):
        for theme, foreground in [('Pearl', '#ffffff'), ('Nord', '#212838')]:
            self.controller.setTheme(theme)
            QTest.qWait(30)
            value = self.expression(self.window, 'AppStyle.accentInk')
            self.assertTrue(value.isValid())
            self.assertEqual(value.name(), foreground)
            primary = self.item('setupPrimary')
            labels = [o for o in primary.findChildren(QObject) if o.property('text') == 'Get started']
            self.assertTrue(labels)
            self.assertEqual(labels[0].property('color').name(), foreground)
            self.assertIsNone(self.window.findChild(QObject, 'dashboardView'))

    def test_header_controls_align_at_both_sizes(self):
        self.dashboard()
        for width, height in [(720, 440), (960, 700), (1280, 860)]:
            self.window.resize(width, height)
            QTest.qWait(30)
            refresh, bell = self.item('refreshButton'), self.item('notificationsButton')
            a, b = [i.mapToScene(QPointF()) for i in (refresh, bell)]
            self.assertAlmostEqual(a.y(), b.y(), delta=1)
            self.assertEqual(refresh.height(), bell.height())
            self.assertGreater(a.x(), width - 180)
            for item in (refresh, bell, self.item('dateSelector')):
                bottom = item.mapToScene(QPointF(item.width(), item.height()))
                self.assertLessEqual(bottom.x(), width)

    def test_pages_notifications_and_popups_work_in_both_themes(self):
        dashboard = self.dashboard()
        for theme in ('Pearl', 'Nord'):
            self.controller.setTheme(theme)
            for page in ('Models', 'Tools', 'History', 'Quota', 'Limits', 'Settings', 'Overview'):
                dashboard.setProperty('page', page)
                QTest.qWait(60)
                # Offscreen macOS does not reliably schedule a render frame in
                # 60 ms. Finish delegates before destroying their page context.
                incubator = self.engine.incubationController()
                if incubator:
                    incubator.incubateFor(100)
                    self.assertEqual(incubator.incubatingObjectCount(), 0)
            for name in ('datePicker', 'priceEditor', 'notificationsPopup'):
                popup = self.item(name)
                QMetaObject.invokeMethod(popup, 'open')
                QTest.qWait(30)
                self.assertTrue(popup.property('opened'))
                QMetaObject.invokeMethod(popup, 'close')

    def test_calendar_range_accepts_reverse_click_order_and_single_day(self):
        self.dashboard()
        self.click('dateSelector')
        popup = self.item('datePicker')
        self.expression(popup, 'pick("2026-02-28"); pick("2026-02-20")')
        self.assertEqual(popup.property('first'), '2026-02-20')
        self.assertEqual(popup.property('last'), '2026-02-28')
        self.expression(popup, 'pick("2026-03-01")')
        self.assertEqual(popup.property('first'), '2026-03-01')
        self.assertEqual(popup.property('last'), '')
        self.click('applyDateRange')
        self.assertEqual(self.controller.state['start'], '2026-03-01')
        self.assertEqual(self.controller.state['end'], '2026-03-01')

    def test_model_price_form_saves_and_reports_validation(self):
        self.dashboard()
        QMetaObject.invokeMethod(self.item('priceEditor'), 'open')
        QTest.qWait(30)
        self.item('priceModel').setProperty('text', 'unknown-model')
        self.item('priceInput').setProperty('text', '1')
        self.item('priceOutput').setProperty('text', '2')
        with patch.object(self.controller, 'refresh'):
            self.click('savePrice')
        self.assertEqual(load_overrides()['unknown-model']['output'], 2)
        self.item('priceInput').setProperty('text', '-1')
        self.click('savePrice')
        self.assertIn('finite number', self.controller.state['priceError'])
        self.assertEqual(load_overrides()['unknown-model']['input'], 1)

    def test_visible_preset_tabs_and_arrows_switch_time_frames(self):
        self.dashboard()
        self.click('periodTabs-0')
        self.assertEqual(self.controller.state['preset'], 'Day')
        self.assertEqual(len(self.controller.state['chart']), 24)
        self.click('previousPeriod')
        self.assertEqual(self.controller.state['preset'], 'All time')
        self.click('nextPeriod')
        self.assertEqual(self.controller.state['preset'], 'Day')
        self.click('periodTabs-2')
        self.assertEqual(self.controller.state['preset'], 'Month')
        for width in (720, 960, 1280):
            self.window.resize(width, 440)
            QTest.qWait(30)
            for name in ('previousPeriod', 'periodTabs-0', 'periodTabs-4', 'nextPeriod', 'dateSelector'):
                item = self.item(name)
                end = item.mapToScene(QPointF(item.width(), item.height()))
                self.assertLessEqual(end.x(), width)

    def test_default_dashboard_is_small_and_calendar_icon_is_centered(self):
        self.dashboard()
        self.assertEqual((self.window.width(), self.window.height()), (720, 440))
        page = self.item('overviewPage')
        self.assertEqual(page.property('contentHeight'), page.height())
        for theme in ('Pearl', 'Nord'):
            self.controller.setTheme(theme)
            for width in (640, 720, 960, 1280):
                self.window.resize(width, 440)
                QTest.qWait(30)
                button, icon = self.item('dateSelector'), self.item('dateSelectorIcon')
                center = button.mapToScene(QPointF(button.width()/2, button.height()/2))
                icon_center = icon.mapToScene(QPointF(icon.width()/2, icon.height()/2))
                self.assertAlmostEqual(center.x(), icon_center.x(), delta=.5)
                self.assertAlmostEqual(center.y(), icon_center.y(), delta=.5)

    def test_metrics_keep_accessible_meaning_without_visible_labels_or_symbols(self):
        self.dashboard()
        for name, word in (('Cost', 'API'), ('Tokens', 'Tokens'), ('Conversations', 'messages')):
            card = self.item('overview' + name + 'Card')
            self.assertIn(name, self.expression(card, 'Accessible.name'))
            self.assertIn(word, self.expression(card, 'Accessible.description'))
            self.assertFalse(any(o.inherits('QQuickCanvasItem') for o in card.findChildren(QObject)))
            pos = card.mapToScene(QPointF(card.width()/2, card.height()/2)).toPoint()
            QTest.mouseMove(self.window, pos)
            QTest.qWait(400)
            tooltip = self.item(card.objectName() + 'Tooltip')
            self.assertTrue(tooltip.property('visible'))
            self.assertIn(word, tooltip.property('text'))
            QTest.mouseMove(self.window, QPointF(2, 2).toPoint())
            card.forceActiveFocus()
            QTest.qWait(30)
            self.assertTrue(tooltip.property('visible'))
            QTest.keyClick(self.window, Qt.Key_Escape)
            QTest.qWait(200)
            self.assertFalse(tooltip.property('visible'))
        self.assertTrue(self.item('overviewCostValue').property('text').startswith('$'))
        forbidden = {'Overview', 'Estimated cost', 'USD  /  API equivalent', 'Conversations',
                     'Top models', 'Activity', 'Hourly', 'Next reset', 'Weekly quota'}
        for obj in self.item('overviewPage').findChildren(QObject):
            if obj.property('visible'):
                self.assertNotIn(obj.property('text'), forbidden)
        # The remaining card icons are action buttons to open the detail pages.
        for name in ('overviewQuotaCard', 'overviewModelsCard', 'activityChart'):
            icons = [o for o in self.item(name).findChildren(QObject) if o.inherits('QQuickCanvasItem')]
            self.assertTrue(all(o.property('name') == 'right' for o in icons))

    def test_sidebar_has_accessible_icon_navigation_at_small_and_large_sizes(self):
        self.dashboard()
        for size in ((720, 440), (1280, 860), (580, 400)):
            self.window.resize(*size)
            QTest.qWait(30)
            for title in ('Overview', 'Models', 'Tools', 'History', 'Quota', 'Limits'):
                button = self.item('navigation' + title)
                self.assertEqual(self.expression(button, 'Accessible.name'), title)
                self.assertEqual(button.property('text'), '')
                pos = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
                QTest.mouseMove(self.window, pos)
                QTest.qWait(400)
                self.assertTrue(self.expression(button, 'ToolTip.visible'))
                self.assertIn(title, self.expression(button, 'ToolTip.text'))
        self.click('navigationModels')
        self.assertEqual(self.item('dashboardView').property('page'), 'Models')
        self.window.resize(460, 320)
        QTest.qWait(30)
        self.assertEqual(self.item('dashboardView').property('page'), 'Models')
        self.assertIsNotNone(self.item('usageTable'))

    def test_pages_and_popup_contents_are_created_only_on_demand(self):
        dashboard = self.dashboard()
        for name in ('settingsPage', 'limitsPage', 'usageTable', 'priceModel', 'applyDateRange'):
            self.assertIsNone(self.window.findChild(QObject, name), name)
        for name in ('datePicker', 'priceEditor', 'notifications'):
            loader = self.item(name + 'ContentLoader')
            self.assertIsNone(loader.property('item'))
            popup = self.item('notificationsPopup' if name == 'notifications' else name)
            QMetaObject.invokeMethod(popup, 'open')
            QTest.qWait(50)
            self.assertIsNotNone(loader.property('item'))
            QMetaObject.invokeMethod(popup, 'close')
            QTest.qWait(200)
            self.assertIsNone(loader.property('item'))
        for page, item in (('Settings', 'settingsPage'), ('Models', 'usageTable'), ('Limits', 'limitsPage')):
            dashboard.setProperty('page', page)
            QTest.qWait(50)
            self.item(item)
            self.window.resize(280, 110)
            QTest.qWait(50)
            self.assertIsNone(self.item('dashboardPageLoader').property('item'))
            self.assertFalse(self.controller._process_view_visible)
            self.window.resize(720, 440)
            QTest.qWait(50)
            self.item(item)
        dashboard.setProperty('page', 'Overview')
        QTest.qWait(50)
        self.assertIsNone(self.window.findChild(QObject, 'limitsPage'))
        self.assertFalse(self.controller._process_view_visible)

    def test_quota_values_dates_and_stale_status_survive_compact_design(self):
        self.dashboard()
        expiry = datetime(2026, 10, 27, 4, 5, 6, tzinfo=timezone.utc)
        banks = BankedResetStatus('fresh', count=2, expiries=(expiry, None),
                                  expiry_details_complete=True, observed_at=datetime.now(timezone.utc))
        self.controller.quota_snapshot = replace(self.controller.quota_snapshot, banked_resets=banks, stale=True)
        self.controller.tick()
        QTest.qWait(30)
        self.assertEqual(self.item('overviewBankedResets').property('text'), '2')
        self.assertIn('banked resets', self.expression(self.item('overviewBankedResets'), 'Accessible.name'))
        self.assertIn('Next weekly reset', self.expression(self.item('overviewResetTime'), 'Accessible.name'))
        self.assertIn('Cached', self.item('overviewQuotaAge').property('text'))
        expiry_rows = [self.item('overviewBankExpiry-' + str(i)).property('text') for i in range(2)]
        self.assertIn('Does not expire', expiry_rows)
        self.assertTrue(any('27 Oct' in text for text in expiry_rows), expiry_rows)
        self.assertLessEqual(self.item('activityChart').mapToScene(QPointF(0, self.item('activityChart').height())).y(), 440)
        self.click('quotaDetailsButton')
        self.assertEqual(self.item('bankedResetCount').property('text'), '2 available')

    def test_chart_metric_switches_without_activity_heading(self):
        self.dashboard()
        self.click('chartMetric-1')
        self.assertEqual(self.item('activityChart').property('metric'), 1)
        self.click('chartMetric-0')
        self.assertEqual(self.item('activityChart').property('metric'), 0)
