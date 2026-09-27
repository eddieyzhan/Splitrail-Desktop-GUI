from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import threading

from qt_support import QtCase
from splitrail_desktop import demo, preferences, sync
from splitrail_desktop.desktop import shifted_range
from splitrail_desktop.domain import aggregate_period, UsageDataset
from splitrail_desktop.pricing import load_overrides
from splitrail_desktop.refresh import NORMAL_REFRESH_SECONDS, QUOTA_REFRESH_SECONDS, QUOTA_RETRY_SECONDS
from splitrail_desktop.quota import BankedResetStatus, format_local_reset
from splitrail_desktop.runner import CostDiagnostics, QuotaCommandResult, StatsCommandResult
from splitrail_desktop.processes import CodexProcess, StopResult
from test_quota_guard import snapshot as guard_snapshot
from test_combined import collector_fixture, event
from splitrail_desktop.combined import add_imported_usage


class DesktopTests(QtCase):
    def prepare_guard(self):
        c = self.controller
        c._state['onboarding'] = False
        now = datetime.now(timezone.utc)
        c.quota_snapshot = guard_snapshot(now=now, reset=now + timedelta(days=3))
        c._processes = (CodexProcess(123, 1000, '/example/codex', '/example/a', 'CLI session', 'sleeping'),
                        CodexProcess(456, 1001, '/example/codex', '/example/b', 'CLI session', 'sleeping'))
        return c, now

    def test_guard_stops_only_selected_processes_and_latches_for_new_starts(self):
        c, now = self.prepare_guard()
        c.protectProcess(c._processes[1].key, False)
        with patch.object(c, 'refreshProcesses'), \
             patch('splitrail_desktop.desktop.stop_codex_processes', return_value=StopResult((123,), ())) as stop:
            c.armQuotaGuard('95', '5', True)
            self.assertTrue(c.guardState['armed'])
            stop.assert_not_called()
            c._finished('quota', QuotaCommandResult(guard_snapshot(6, now + timedelta(seconds=1), now + timedelta(days=3)), 0), None, -1)
            self.wait_for(lambda: 'guard-stop' not in c._tasks)
            self.assertEqual([p.pid for p in stop.call_args.args[0]], [123])
            self.assertTrue(c.guardState['afterReset'])
            self.assertTrue(c.guardState['blocked'])
            self.assertEqual(c.guardState['stoppedCount'], 1)
            new = replace(c._processes[0], pid=789, started=1002)
            c._finished('processes', (*c._processes, new), None, -1)
            self.wait_for(lambda: 'guard-stop' not in c._tasks)
            self.assertEqual([p.pid for p in stop.call_args.args[0]], [789])
            c.disarmQuotaGuard()
            self.assertFalse(c.guardState['armed'])
            self.assertTrue(stop.call_args.args[1].is_set())
        saved = preferences.load()
        self.assertEqual(saved['quota_limits'], {'current': 95, 'after_reset': 5})
        self.assertNotIn('processes', saved)
        self.assertNotIn('armed', saved)

    def test_selected_scope_does_not_adopt_restarted_process(self):
        c, now = self.prepare_guard()
        with patch.object(c, 'refreshProcesses'):
            c.armQuotaGuard('95', '5', False)
        original = c._processes[0]
        self.assertTrue(c._is_protected(original))
        self.assertFalse(c._is_protected(replace(original, started=2000)))
        c.disarmQuotaGuard()

    def test_guard_rejects_bad_limits_and_stale_quota_without_stopping(self):
        c, now = self.prepare_guard()
        for current, fresh in (('NaN', True), ('101', True), ('95', False)):
            c.quota_snapshot = replace(c.quota_snapshot, stale=not fresh)
            c.armQuotaGuard(current, '5', True)
            self.assertFalse(c.guardState['armed'])
            self.assertTrue(c.guardState['error'])
        self.assertNotIn('guard-stop', c._tasks)

    def test_guard_watchdog_stops_after_failed_refresh_and_can_disarm_pending_scan(self):
        c, now = self.prepare_guard()
        with patch.object(c, 'refreshProcesses'), \
             patch('splitrail_desktop.desktop.stop_codex_processes', return_value=StopResult((123, 456), ())) as stop:
            c.armQuotaGuard('95', '5', True)
            c._finished('quota', None, RuntimeError('offline'), -1)
            stop.assert_not_called()
            c._guard.last_snapshot = guard_snapshot(now=now-timedelta(seconds=121), reset=now+timedelta(days=3))
            c.tick()
            self.wait_for(lambda: 'guard-stop' not in c._tasks)
            stop.assert_called_once()
            c.disarmQuotaGuard()
            c._finished('processes', (), None, -1)
            self.assertFalse(c.guardState['blocked'])

    def test_process_polling_is_dormant_off_page_unless_armed_and_stops_on_close(self):
        c, _ = self.prepare_guard()
        c.auto_refresh = True
        with patch.object(c, 'refreshProcesses'):
            self.assertFalse(c._process_timer.isActive())
            c.setProcessViewVisible(True)
            self.assertTrue(c._process_timer.isActive())
            c.setProcessViewVisible(False)
            self.assertFalse(c._process_timer.isActive())
            c.armQuotaGuard('95', '5', True)
            self.assertTrue(c._process_timer.isActive())
            c.close()
            self.assertFalse(c._process_timer.isActive())
            self.assertTrue(c._guard_cancel.is_set())

    def test_demo_guard_never_scans_or_stops_real_processes(self):
        c = self.controller
        c.demo = True
        with patch('splitrail_desktop.desktop.discover_codex_processes') as scan, \
             patch('splitrail_desktop.desktop.stop_codex_processes') as stop:
            c.refreshProcesses()
            c.armQuotaGuard('0', '0', True)
            self.assertEqual(len(c.guardState['processes']), 2)
            self.assertFalse(c.guardState['armed'])
            scan.assert_not_called()
            stop.assert_not_called()

    def test_banked_expiry_display_distinguishes_missing_nonexpiring_and_stale(self):
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(days=5)
        banks = BankedResetStatus('fresh', count=3, expiries=(expiry, None), observed_at=now)
        self.controller.quota_snapshot = replace(demo.quota(), banked_resets=banks)
        self.controller.tick()
        quota = self.controller.clock['quota']
        self.assertEqual(quota['bankExpiryDetails'],
                         f'Reset 1: Expires {format_local_reset(expiry)}\n'
                         'Reset 2: Does not expire\nExpiry details: 2 of 3 provided by Codex')
        self.assertFalse(quota['banksStale'])
        self.controller.quota_snapshot = replace(demo.quota(), banked_resets=replace(
            banks, status='stale', expiries=()))
        self.controller.tick()
        self.assertTrue(self.controller.clock['quota']['banksStale'])
        self.assertEqual(self.controller.clock['quota']['bankExpiryDetails'], 'Expiry times not provided by Codex')
        self.controller.quota_snapshot = replace(demo.quota(), banked_resets=replace(
            banks, count=0, expiries=(), expiry_details_complete=True))
        self.controller.tick()
        self.assertEqual(self.controller.clock['quota']['bankExpiryDetails'], '')

    def test_local_onboarding_never_contacts_github(self):
        c = self.controller
        self.assertTrue(c.state['onboarding'])
        with patch.object(c, '_run') as worker:
            c.refresh()
            worker.assert_not_called()
            c.finishSetup()
            worker.assert_not_called()
        self.assertTrue(preferences.load()['onboarded'])
        self.assertIsNone(sync.settings())

    def test_account_lookup_requires_get_started_and_create_requires_explicit_action(self):
        c = self.controller
        with patch('splitrail_desktop.desktop.shutil.which', return_value='/example/gh'), \
             patch('splitrail_desktop.sync.account', return_value='example-user') as account, \
             patch('splitrail_desktop.sync.create_private_repo') as create:
            account.assert_not_called()
            c.setupContinue()
            self.wait_for(lambda: not c.state['setupBusy'])
            self.assertEqual(c.state['setupStage'], 'account')
            self.assertEqual(c.state['setupLogin'], 'example-user')
            c.setupContinue()
            self.assertEqual(c.state['setupStage'], 'repository')
            create.assert_not_called()

    def test_auth_code_cancel_and_restart_ignore_stale_result(self):
        c = self.controller
        cancellations = []
        released = threading.Event()
        def authenticate(on_code, cancel):
            cancellations.append(cancel)
            on_code('ABCD-1234')
            cancel.wait(2)
            released.set()
            raise sync.SyncError('Cancelled')
        with patch('splitrail_desktop.sync.sign_in', side_effect=authenticate):
            c.signIn()
            self.wait_for(lambda: c.state['setupCode'] == 'ABCD-1234')
            old_generation = c._setup_generation
            c.setupBack()
            self.assertTrue(cancellations[0].is_set())
            self.wait_for(released.is_set)
            c.signIn()
            self.wait_for(lambda: len(cancellations) == 2)
            self.assertIsNot(cancellations[0], cancellations[1])
            c._finished('auth', 'old-account', None, old_generation)
            self.assertEqual(c.state['setupStage'], 'auth')
            self.assertEqual(c._tasks['auth'], c._setup_generation)
            c.finishSetup()
            self.wait_for(lambda: 'auth' not in c._tasks)
            self.assertFalse(c.state['onboarding'])
            self.assertEqual(c.state['setupLogin'], '')

    def test_create_configure_then_disconnect(self):
        c = self.controller
        c._state['syncScope'] = 'codex'
        with patch('splitrail_desktop.sync.create_private_repo', return_value='example/private') as create, \
             patch('splitrail_desktop.sync._private_repo', return_value={'private': True}):
            c.connectRepository('splitrail-usage', False, True)
            self.wait_for(lambda: not c.state['setupBusy'])
            create.assert_called_once_with('splitrail-usage')
        config = sync.settings()
        self.assertEqual(config['repository'], 'example/private')
        self.assertEqual(config['scope'], 'codex')
        self.assertTrue(config['automatic'])
        self.assertEqual(c.state['setupStage'], 'ready')
        c.disconnect()
        self.wait_for(lambda: not c.state['syncBusy'])
        self.assertIsNone(sync.settings())
        self.assertFalse(c.state['connected'])

    def test_created_repo_is_retained_after_configuration_failure(self):
        c = self.controller
        with patch('splitrail_desktop.sync.create_private_repo', return_value='example/private') as create, \
             patch('splitrail_desktop.sync.configure', side_effect=sync.SyncError('Temporary failure')):
            c.connectRepository('private', False, False)
            self.wait_for(lambda: not c.state['setupBusy'])
            self.assertTrue(c.state['repoExisting'])
            self.assertEqual(c.state['repoName'], 'example/private')
            c.connectRepository(c.state['repoName'], c.state['repoExisting'], False)
            self.wait_for(lambda: not c.state['setupBusy'])
            create.assert_called_once()
            self.assertIn('Temporary failure', c.state['setupError'])

    def test_corrupt_sync_settings_can_be_reset(self):
        (self.data / sync.SETTINGS_FILE).write_text('{invalid')
        c = self.controller
        c._load_settings()
        self.assertIn('Disconnect', c.state['settingsError'])
        c.disconnect()
        self.wait_for(lambda: not c.state['syncBusy'])
        self.assertIsNone(sync.settings())
        self.assertEqual(c.state['settingsError'], '')

    def test_manual_sync_shows_missing_collector_remedy(self):
        from splitrail_desktop.runner import MissingCommandError
        c = self.controller
        with patch('splitrail_desktop.sync._private_repo', return_value={'private': True}), \
             patch('splitrail_desktop.runner.run_splitrail', side_effect=MissingCommandError('missing')):
            sync.configure('example/private', scope='all')
            c._load_settings()
            c.syncNow()
            self.wait_for(lambda: not c.state['syncBusy'])
        self.assertIn('All tools sync needs the Splitrail collector', c.state['settingsError'])
        self.assertIn('Codex', c.state['settingsError'])
        self.assertTrue(c.state['connected'])
        self.assertEqual(c.state['notifications'][0]['detail'], c.state['settingsError'])

    def test_display_uses_combined_data_for_metrics_tables_and_chart(self):
        c = self.controller
        combined = add_imported_usage(collector_fixture(), [event('laptop')])
        c._accept_usage(StatsCommandResult(combined, CostDiagnostics(('unknown',), 0, 0, ()), 0))
        c.chooseDates('2026-09-01', '2026-09-02')
        total = aggregate_period(combined, date(2026, 9, 1), date(2026, 9, 2)).total
        self.assertEqual(c.state['summary']['exactTokens'], f'{total.tokens.total:,}')
        self.assertEqual(c.state['summary']['cost'], f'${total.cost:,.2f}')
        self.assertEqual(len(c.state['tools']), 4)
        self.assertEqual(sum(row['tokens'] for row in c.state['chart']), total.tokens.total)
        self.assertEqual(c.state['notifications'][0]['action'], 'pricing')

    def test_failed_scope_change_preserves_previous_data_and_explains_it(self):
        c = self.controller
        c._accept_usage(demo.usage())
        dataset = c.dataset
        c.setUsageMode('local')
        c._finished('usage', None, RuntimeError('Collector unavailable'), c._usage_generation)
        self.assertIs(c.dataset, dataset)
        self.assertEqual(c.state['displayedMode'], 'combined')
        self.assertIn('previous All devices', c.state['notifications'][0]['detail'])

    def test_background_refresh_preserves_custom_range_and_independent_errors(self):
        c = self.controller
        c.chooseDates('2026-09-20', '2026-09-22')
        c._notice('quota', 'Quota offline')
        c._finished('usage', demo.usage(), None, c._usage_generation)
        self.assertEqual((c.state['start'], c.state['end']), ('2026-09-20', '2026-09-22'))
        self.assertEqual(c.state['notifications'][0]['key'], 'quota')

    def test_custom_date_validation_and_equal_day_range(self):
        c = self.controller
        for start, end in [('2026-02-30', '2026-03-01'), ('2026-03-01', '2026-02-28'),
                           ('1969-12-31', '2026-01-01'), ('2026-01-01', '2200-01-01')]:
            self.assertFalse(c.chooseDates(start, end))
        self.assertTrue(c.chooseDates('2026-02-28', '2026-02-28'))
        c.shiftPeriod(1)
        self.assertEqual(c.state['start'], '2026-03-01')
        c.choosePeriod('Month')
        self.assertEqual(c.state['preset'], 'Month')
        self.assertTrue(c.state['canNext'])

    def test_calendar_periods_handle_leap_year_and_year_boundary(self):
        empty = UsageDataset((), {})
        self.assertEqual(shifted_range('Month', -1, date(2024, 3, 31), empty), (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(shifted_range('Month', -1, date(2026, 1, 1), empty), (date(2025, 12, 1), date(2025, 12, 31)))
        self.assertEqual(shifted_range('Year', -1, date(2026, 1, 1), empty), (date(2025, 1, 1), date(2025, 12, 31)))

    def test_prices_save_validate_restore_and_schedule_recalculation(self):
        c = self.controller
        with patch.object(c, 'refresh') as refresh:
            self.assertTrue(c.savePrice('unknown-model', '1', '2', '.1', '0'))
            refresh.assert_called_once()
        self.assertEqual(load_overrides()['unknown-model']['input'], 1)
        self.assertFalse(c.savePrice('unknown-model', 'NaN', '2', '', ''))
        self.assertEqual(load_overrides()['unknown-model']['input'], 1)
        self.assertIn('finite number', c.state['priceError'])
        c._state['busy'] = True
        c.savePrice('unknown-model', '3', '4', '', '')
        self.assertTrue(c._pricing_pending)
        with patch.object(c, 'refresh') as refresh:
            c._finished('usage', demo.usage(), None, c._usage_generation)
            refresh.assert_called_once()
        self.assertFalse(c._pricing_pending)
        c.removePrice('unknown-model')
        self.assertEqual(load_overrides(), {})

    def test_unknown_models_are_available_in_price_editor(self):
        c = self.controller
        c._accept_usage(replace(demo.usage(), cost_diagnostics=CostDiagnostics(('unknown-model', 'gpt-5.3-codex-spark'), 0, 0, ())))
        c.loadPrices()
        self.assertTrue(next(row for row in c.state['prices'] if row['name'] == 'unknown-model')['missing'])
        spark = next(row for row in c.state['prices'] if row['name'] == 'gpt-5.3-codex-spark')
        self.assertTrue(spark['missing'])
        self.assertIn('public rate unavailable', spark['priceNote'])

    def test_refresh_age_ticks_and_quota_failure_retains_last_good_snapshot(self):
        c = self.controller
        now = datetime.now(timezone.utc)
        c._last_refresh = now - timedelta(seconds=12)
        c.quota_snapshot = replace(demo.quota(), refreshed_at=now - timedelta(seconds=20))
        c.tick()
        self.assertIn('12s ago', c.state['refreshAge'])
        used = c.state['quota']['used']
        c._finished('quota', None, RuntimeError('Offline'), -1)
        self.assertEqual(c.state['quota']['used'], used)
        self.assertTrue(c.state['quota']['stale'])

    def test_theme_persists_without_discarding_usage(self):
        c = self.controller
        c._accept_usage(demo.usage())
        dataset = c.dataset
        for name in ('Nord', 'Pearl'):
            c.setTheme(name)
            self.assertEqual(preferences.load()['theme'], name)
            self.assertIs(c.dataset, dataset)

    def test_demo_cannot_authenticate_create_sync_export_or_refresh(self):
        c = self.controller
        c.demo = True
        with patch.object(c, '_run') as worker:
            c.signIn()
            c.connectRepository('example/private', True, True)
            c.syncNow()
            c.transfer('export')
            c.removeImports()
            c.refresh()
            worker.assert_not_called()

    def test_refresh_routes_sources_and_ignores_duplicate_calls(self):
        c = self.controller
        c.finishSetup()
        result = demo.usage()
        with patch('splitrail_desktop.combined.run_combined_usage', return_value=result) as combined, \
             patch('splitrail_desktop.portable.run_portable_usage', return_value=result) as codex, \
             patch('splitrail_desktop.desktop.run_splitrail', return_value=result) as local:
            for mode, runner in [('combined', combined), ('local', local), ('codex', codex)]:
                c._state['usageMode'] = mode
                c.refresh()
                c.refresh()
                self.wait_for(lambda: not c.state['busy'] and not c.state['quotaBusy'])
                runner.assert_called_once_with()

    def test_timer_stops_during_setup_and_close(self):
        c = self.controller
        c.auto_refresh = True
        c._finished('usage', demo.usage(), None, c._usage_generation)
        self.assertEqual(c._timer.interval(), NORMAL_REFRESH_SECONDS * 1000)
        self.assertTrue(c._timer.isActive())
        c._finished('quota', QuotaCommandResult(demo.quota(), 0), None, -1)
        self.assertEqual(c._quota_timer.interval(), QUOTA_REFRESH_SECONDS * 1000)
        self.assertTrue(c._quota_timer.isActive())
        c._finished('quota', None, RuntimeError('Offline'), -1)
        self.assertEqual(c._quota_timer.interval(), QUOTA_RETRY_SECONDS * 1000)
        c.beginSetup()
        self.assertFalse(c._timer.isActive())
        self.assertFalse(c._quota_timer.isActive())
        c.close()
        self.assertFalse(c._clock.isActive())

    def test_quota_timer_refreshes_quota_without_scanning_usage(self):
        c = self.controller
        c.finishSetup()
        with patch('splitrail_desktop.desktop.run_quota_axi', return_value=QuotaCommandResult(demo.quota(), 0)) as quota, \
             patch('splitrail_desktop.desktop.run_splitrail') as usage:
            c.refreshQuota()
            self.wait_for(lambda: not c.state['quotaBusy'])
            quota.assert_called_once_with()
            usage.assert_not_called()

    def test_clock_tick_does_not_reset_table_models(self):
        c = self.controller
        changes, ticks = [], []
        c.changed.connect(lambda: changes.append(True))
        c.clockChanged.connect(lambda: ticks.append(True))
        c.tick()
        self.assertEqual(changes, [])
        self.assertEqual(ticks, [True])

    def test_preset_arrows_cycle_and_wrap_in_both_directions(self):
        c = self.controller
        c.choosePeriod('Day')
        for expected in ('Week', 'Month', 'Year', 'All time', 'Day'):
            c.cyclePeriod(1)
            self.assertEqual(c.state['preset'], expected)
        c.cyclePeriod(-1)
        self.assertEqual(c.state['preset'], 'All time')
        c.chooseDates('2026-02-03', '2026-02-05')
        c.cyclePeriod(1)
        self.assertEqual(c.state['preset'], 'Day')
        self.assertEqual(c.state['start'], date.today().isoformat())

    def test_today_is_hourly_and_other_presets_remain_daily(self):
        c = self.controller
        c._accept_usage(demo.usage())
        c.choosePeriod('Day')
        self.assertEqual(len(c.state['chart']), 24)
        self.assertEqual(c.state['chart'][0]['label'], '00:00')
        self.assertEqual(c.state['chart'][-1]['label'], '23:00')
        daily = aggregate_period(c.dataset, date.today(), date.today()).total
        self.assertEqual(sum(p['tokens'] for p in c.state['chart']), daily.tokens.total)
        self.assertAlmostEqual(sum(p['cost'] for p in c.state['chart']), daily.cost)
        self.assertFalse(any(n['key']=='hourly' for n in c.state['notifications']))
        c.choosePeriod('Month')
        self.assertNotIn(':', c.state['chart'][0]['label'])

    def test_daily_only_history_is_never_fabricated_into_hours(self):
        c = self.controller
        sample = demo.usage()
        c._accept_usage(replace(sample, dataset=replace(sample.dataset, hours=())))
        c.choosePeriod('Day')
        self.assertTrue(c.state['summary']['exactTokens'])
        self.assertEqual(sum(p['tokens'] for p in c.state['chart']), 0)
        self.assertIn('isn’t available', c.state['chartEmpty'])
        self.assertTrue(any(n['key']=='hourly' for n in c.state['notifications']))
