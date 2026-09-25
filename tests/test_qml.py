from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, QUrl, QPointF, Qt, QMetaObject, Q_ARG
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtTest import QTest
import shiboken6

from qt_support import QtCase
from splitrail_desktop import demo, sync
from splitrail_desktop.pricing import load_overrides

QML = Path(__file__).parents[1] / 'src' / 'splitrail_desktop' / 'qml'


class QmlTests(QtCase):
    def setUp(self):
        super().setUp()
        self.warnings = []
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda messages: self.warnings.extend(m.toString() for m in messages))
        self.engine.rootContext().setContextProperty('bridge', self.controller)
        self.engine.rootContext().setContextProperty('appFont', 'Sans Serif')
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
        for width, height in [(960, 700), (1280, 860)]:
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
            for page in ('Models', 'Tools', 'History', 'Quota', 'Settings', 'Overview'):
                dashboard.setProperty('page', page)
                QTest.qWait(60)
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
        for width in (960, 1280):
            self.window.resize(width, 700)
            QTest.qWait(30)
            for name in ('previousPeriod', 'periodTabs-0', 'periodTabs-4', 'nextPeriod', 'dateSelector'):
                item = self.item(name)
                end = item.mapToScene(QPointF(item.width(), item.height()))
                self.assertLessEqual(end.x(), width)
