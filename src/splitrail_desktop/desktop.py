"""Qt Quick desktop shell over the existing local usage and private sync engines."""
from __future__ import annotations

import calendar
import os
import shutil
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QFileDialog

from . import preferences, sync
from .domain import UsageDataset, aggregate_period, daily_data_rows, period_for_preset
from .presentation import (build_chart_buckets, chart_series, hourly_chart_buckets,
                           format_compact, format_currency, format_date_range)
from .pricing import CATALOG, RATE_FIELDS, load_overrides, resolve_rates, save_override
from .quota import (format_refresh_age, format_countdown, format_local_reset,
                    preserve_banked_resets, is_banked_reset_status_stale, weekly_pace_percent)
from .refresh import AdaptiveRefreshPolicy, QUOTA_REFRESH_SECONDS, QUOTA_RETRY_SECONDS
from .runner import run_splitrail, run_quota_axi, SPLITRAIL_FALLBACK


def quota_timestamp_parts(value: datetime | None) -> dict[str, str]:
    if value is None:
        return {'date': 'Time unavailable', 'time': ''}
    local = value.astimezone()
    date_format = '%d %b' if local.year == datetime.now().year else '%d %b %Y'
    return {'date': local.strftime(date_format).lstrip('0'),
            'time': local.strftime('%I:%M %p').lstrip('0')}


def shifted_range(preset: str, offset: int, today: date, dataset: UsageDataset) -> tuple[date, date]:
    if preset == 'Day':
        day = today + timedelta(days=offset)
        return day, day
    if preset == 'Week':
        day = today + timedelta(weeks=offset)
        return period_for_preset('Week', day, dataset)
    if preset == 'Month':
        index = today.year * 12 + today.month - 1 + offset
        year, month = divmod(index, 12)
        first = date(year, month + 1, 1)
        return first, first.replace(day=calendar.monthrange(year, month + 1)[1])
    if preset == 'Year':
        year = today.year + offset
        return date(year, 1, 1), today if offset == 0 else date(year, 12, 31)
    return period_for_preset('All time', today, dataset)


class DesktopController(QObject):
    changed = Signal()
    clockChanged = Signal()
    completed = Signal(str, object, object, int)
    setupEvent = Signal(str, str, int)

    def __init__(self, *, demo=False, onboarding=False, auto_refresh=True, codex_usage=False):
        super().__init__()
        self.demo = demo
        self.closed = False
        self.auto_refresh = auto_refresh
        self.dataset = UsageDataset((), {})
        self.quota_snapshot = None
        self.usage_result = None
        self._tasks = {}
        self._notifications = {}
        self._setup_generation = 0
        self._auth_cancel = threading.Event()
        self._pricing_pending = False
        self._usage_generation = 0
        self._preset = 'Month'
        self._offset = 0
        self._custom_range = None
        self._last_refresh = None
        self._policy = AdaptiveRefreshPolicy()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        self._quota_timer = QTimer(self)
        self._quota_timer.setSingleShot(True)
        self._quota_timer.timeout.connect(self.refreshQuota)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self.tick)
        self._clock.start(1000)
        prefs = preferences.load()
        collector = bool(os.environ.get('SPLITRAIL_BIN') or shutil.which('splitrail') or SPLITRAIL_FALLBACK.exists())
        mode = 'codex' if codex_usage or not collector else prefs.get('usage_mode', 'combined')
        if mode not in ('combined', 'codex', 'local'):
            mode = 'combined' if collector else 'codex'
        self._state = {
            'theme': prefs.get('theme', 'Pearl'), 'onboarding': bool(onboarding or (not prefs.get('onboarded') and not demo)),
            'setupStage': 'welcome', 'setupError': '', 'setupBusy': False, 'setupLogin': '', 'setupCode': '',
            'ghAvailable': bool(shutil.which('gh')), 'repoName': 'splitrail-usage', 'repoExisting': False,
            'busy': False, 'quotaBusy': False, 'syncBusy': False, 'usageMode': mode,
            'displayedMode': mode, 'hasUsage': False, 'refreshAge': 'Ready when you are',
            'notifications': [], 'prices': [], 'priceError': '', 'priceNotice': '',
            'connected': False, 'repository': '', 'automatic': False, 'receiveOnly': False,
            'syncScope': 'all' if collector else 'codex', 'syncStatus': 'Only on this device',
            'period': 'This month', 'range': '', 'start': '', 'end': '', 'preset': 'Month',
            'canNext': True, 'canPrevious': True, 'summary': {}, 'chart': [],
            'chartDetail': '', 'chartEmpty': '',
            'models': [], 'tools': [], 'days': [], 'quota': {'available': False, 'age': ''},
            'demo': demo, 'transferBusy': False, 'settingsError': '',
        }
        self.completed.connect(self._finished)
        self.setupEvent.connect(self._setup_event)
        self._load_settings()
        self.loadPrices()
        self._render()
        if demo:
            from . import demo as sample
            self._accept_usage(sample.usage())
            self.quota_snapshot = sample.quota()
            self.tick()
        elif auto_refresh and not self._state['onboarding']:
            QTimer.singleShot(80, self.refresh)

    @Property('QVariantMap', notify=changed)
    def state(self):
        return self._state

    @Property("QVariantMap", notify=clockChanged)
    def clock(self):
        return {"refreshAge": self._state["refreshAge"], "quota": self._state["quota"]}

    def _emit(self):
        if not self.closed:
            self._state['notifications'] = list(self._notifications.values())
            self.changed.emit()

    def _notice(self, key, title, detail='', action=''):
        self._notifications[key] = {'key': key, 'title': title, 'detail': detail, 'action': action}

    def _load_settings(self):
        try:
            config = sync.settings()
            self._state.update(connected=bool(config), repository=config['repository'] if config else '',
                               automatic=config['automatic'] if config else False,
                               receiveOnly=config['receive_only'] if config else False,
                               syncScope=config['scope'] if config else self._state['syncScope'],
                               syncStatus=sync.status_text())
        except (ValueError, OSError, sync.SyncError, TypeError):
            self._state['settingsError'] = 'Sync settings need attention. Disconnect to reset them.'

    def _run(self, kind, callback, generation=-1):
        if self.closed or (kind in self._tasks and not (kind in ('account', 'auth') and self._tasks[kind] != generation)):
            return False
        self._tasks[kind] = generation
        def worker():
            try:
                value, error = callback(), None
            except Exception as exc:
                value, error = None, exc
            if not self.closed:
                self.completed.emit(kind, value, error, generation)
        threading.Thread(target=worker, daemon=True, name='splitrail-'+kind).start()
        return True

    @Slot()
    def refresh(self):
        if self.demo or self._state['onboarding'] or self._state['syncBusy']:
            return
        if 'usage' not in self._tasks:
            self._timer.stop()
            self._state['busy'] = True
            mode = self._state['usageMode']
            generation = self._usage_generation
            def collect():
                if mode == 'combined':
                    from .combined import run_combined_usage
                    return run_combined_usage()
                if mode == 'codex':
                    from .portable import run_portable_usage
                    return run_portable_usage()
                return run_splitrail()
            self._run('usage', collect, generation)
        self._start_quota_refresh()
        self._emit()

    def _start_quota_refresh(self) -> bool:
        if self.closed or self.demo or self._state['onboarding'] or 'quota' in self._tasks:
            return False
        self._quota_timer.stop()
        self._state['quotaBusy'] = True
        self._run('quota', run_quota_axi)
        return True

    @Slot()
    def refreshQuota(self):
        if self._start_quota_refresh():
            self._emit()

    def _accept_usage(self, result):
        self.usage_result = result
        self.dataset = result.dataset
        self._last_refresh = datetime.now(timezone.utc)
        self._state.update(hasUsage=True, displayedMode=self._state['usageMode'])
        self._policy.record_success(self.dataset)
        self._notifications.pop('usage', None)
        diag = result.cost_diagnostics
        if diag.unknown_models:
            self._notice('pricing', f'{len(diag.unknown_models)} models need a price',
                         ', '.join(diag.unknown_models), 'pricing')
        else:
            self._notifications.pop('pricing', None)
        if diag.fallback_session_count or diag.other_warning_count:
            self._notice('diagnostics', 'Usage details', '\n'.join(diag.lines))
        else:
            self._notifications.pop('diagnostics', None)
        self._render()
        self._load_settings()

    @Slot(str, object, object, int)
    def _finished(self, kind, value, error, generation):
        if self._tasks.get(kind) == generation:
            self._tasks.pop(kind, None)
        if self.closed:
            return
        if kind in ('account', 'auth', 'connect') and generation != self._setup_generation:
            return
        if kind == 'usage':
            self._state['busy'] = False
            if generation != self._usage_generation:
                self.refresh()
                return
            if error:
                self._policy.record_failure()
                source = {'combined': 'All devices', 'local': 'This device', 'codex': 'Codex'}
                detail = str(error)
                if self._state['hasUsage']:
                    detail += '\nShowing the previous ' + source[self._state['displayedMode']] + ' snapshot.'
                self._notice('usage', 'Usage could not refresh', detail)
            else:
                self._accept_usage(value)
            if self.auto_refresh:
                self._timer.start(self._policy.interval_seconds * 1000)
            if self._pricing_pending:
                self._pricing_pending = False
                self.refresh()
        elif kind == 'quota':
            self._state['quotaBusy'] = False
            if error:
                self._notice('quota', 'Quota is unavailable', str(error))
            else:
                self._notifications.pop('quota', None)
                self.quota_snapshot = preserve_banked_resets(value.snapshot, self.quota_snapshot)
            self.tick()
            if self.auto_refresh:
                interval = QUOTA_RETRY_SECONDS if error else QUOTA_REFRESH_SECONDS
                self._quota_timer.start(interval * 1000)
        elif kind in ('account', 'auth', 'connect'):
            self._state['setupBusy'] = False
            if error:
                if kind == 'account':
                    self._state['setupStage'] = 'account'
                    self._state['setupError'] = ''
                else:
                    self._state['setupError'] = str(error) if isinstance(error, sync.SyncError) else 'Unable to finish. Please try again.'
                if kind == 'auth':
                    self._state['setupStage'] = 'account'
                    self._state['setupCode'] = ''
            elif kind in ('account', 'auth'):
                self._state.update(setupLogin=value, setupStage='account', setupCode='')
            else:
                self._load_settings()
                self._state.update(setupStage='ready', setupError='')
        elif kind in ('sync', 'sync-settings', 'disconnect'):
            self._state['syncBusy'] = False
            if error:
                self._state['settingsError'] = str(error) if isinstance(error, sync.SyncError) else 'Unable to sync. Check your connection and local usage tools.'
                self._notice('sync', 'Sync needs attention', self._state['settingsError'])
            else:
                self._state['settingsError'] = ''
                self._notifications.pop('sync', None)
                self._load_settings()
                if kind == 'disconnect':
                    self._state.update(connected=False, repository='', automatic=False, receiveOnly=False)
                self.refresh()
        elif kind in ('export', 'import', 'remove-imports'):
            self._state['transferBusy'] = False
            if error:
                self._notice('transfer', 'Transfer could not finish', str(error))
            else:
                self._notice('transfer', 'Export saved' if kind == 'export' else 'Imported usage updated', value or '')
                if kind == 'import' and self._state['usageMode'] == 'local':
                    self._state['usageMode'] = 'combined'
                    self._usage_generation += 1
                if kind != 'export':
                    self.refresh()
        self._emit()

    @Slot()
    def tick(self):
        if self.closed:
            return
        if self._last_refresh:
            self._state['refreshAge'] = format_refresh_age(self._last_refresh)
        snapshot = self.quota_snapshot
        if snapshot:
            window = snapshot.base_weekly
            banks = snapshot.banked_resets
            expiries = [format_local_reset(stamp) if stamp else 'Does not expire' for stamp in banks.expiries]
            expiry_details = [f'Reset {index}: {"Does not expire" if stamp is None else "Expires " + shown}'
                              for index, (stamp, shown) in enumerate(zip(banks.expiries, expiries), start=1)]
            expiry_notice = ''
            if banks.count and not banks.expiry_details_complete:
                expiry_notice = (f'Expiry details: {len(expiries)} of {banks.count} provided by Codex'
                                 if expiries else 'Expiry times not provided by Codex')
                expiry_details.append(expiry_notice)
            self._state['quota'] = {
                'available': window is not None and window.percent_used is not None,
                'used': window.percent_used if window and window.percent_used is not None else 0,
                'paceUsed': weekly_pace_percent(window.resets_at) if window and window.percent_used is not None else None,
                'remaining': f'{window.percent_remaining:g}%' if window and window.percent_remaining is not None else '—',
                'reset': format_local_reset(window.resets_at) if window else '',
                'resetParts': quota_timestamp_parts(window.resets_at if window else None),
                'countdown': format_countdown(window.resets_at) if window else '',
                'age': format_refresh_age(snapshot.refreshed_at or snapshot.generated_at),
                'stale': snapshot.stale or 'quota' in self._notifications,
                'banks': snapshot.banked_resets.count if snapshot.banked_resets.count is not None else -1,
                'expiries': expiries,
                'bankExpiryDetails': '\n'.join(expiry_details),
                'bankExpiryRows': [quota_timestamp_parts(stamp) if stamp else {'date': 'Does not expire', 'time': ''}
                                   for stamp in banks.expiries],
                'bankExpiryNotice': expiry_notice,
                'banksStale': is_banked_reset_status_stale(banks),
                'banksAge': format_refresh_age(banks.observed_at),
                'windows': [{'name': item.label, 'used': f'{item.percent_used:g}%' if item.percent_used is not None else '—',
                             'remaining': f'{item.percent_remaining:g}%' if item.percent_remaining is not None else '—',
                             'reset': format_local_reset(item.resets_at)} for item in snapshot.windows],
            }
        self.clockChanged.emit()

    def _render(self):
        today = date.today()
        start, end = self._custom_range or shifted_range(self._preset, self._offset, today, self.dataset)
        aggregate = aggregate_period(self.dataset, start, end)
        total = aggregate.total
        title = {'Day': 'Today', 'Week': 'This week', 'Month': 'This month', 'Year': 'This year', 'All time': 'All time'}[self._preset]
        if self._custom_range:
            title = 'Custom range'
        elif self._offset:
            title = start.strftime('%B %Y') if self._preset == 'Month' else str(start.year) if self._preset == 'Year' else format_date_range(start, end)
        self._state.update(period=title, range=format_date_range(start, end), start=start.isoformat(), end=end.isoformat(),
                           preset=self._preset if not self._custom_range else 'Custom', canNext=True, canPrevious=True)
        self._state['summary'] = {'cost': format_currency(total.cost), 'tokens': format_compact(total.tokens.total),
                                  'exactTokens': f'{total.tokens.total:,}', 'messages': f'{total.messages:,}',
                                  'conversations': f'{total.conversations:,}', 'toolCalls': f'{total.tool_calls:,}',
                                  'input': f'{total.tokens.input:,}', 'output': f'{total.tokens.output:,}',
                                  'cached': f'{total.tokens.cached:,}', 'reasoning': f'{total.tokens.reasoning:,}',
                                  'read': f'{total.tokens.cache_read:,}', 'write': f'{total.tokens.cache_write:,}',
                                  'empty': aggregate.is_empty}
        self._state.update(chartDetail='', chartEmpty='')
        self._notifications.pop('hourly', None)
        if start == end:
            buckets = hourly_chart_buckets(self.dataset, start)
            self._state['chartDetail'] = 'Hourly'
            timed_tokens = sum(bucket.total.tokens.total for bucket in buckets)
            timed_cost = sum(bucket.total.cost for bucket in buckets)
            if timed_tokens != total.tokens.total or abs(timed_cost-total.cost) > max(.02, total.cost*.001):
                self._notice('hourly', 'Some usage has no hourly detail',
                             'The chart shows timestamped usage only. Daily-only imports and older device snapshots remain in your totals. Refresh each device to add hourly detail.')
            if not any(row.day == start for row in self.dataset.hours) and (total.tokens.total or total.cost):
                self._state['chartEmpty'] = 'Hourly detail isn’t available for this usage'
        else:
            buckets = build_chart_buckets(chart_series(self.dataset, start, min(end, today))) if start <= today else []
        self._state['chart'] = [{'label': item.label, 'tokens': item.total.tokens.total, 'cost': item.total.cost} for item in buckets]
        self._state['models'] = [{'name': item.name, 'tokens': f'{item.tokens.total:,}', 'compactTokens': format_compact(item.tokens.total),
                                 'cost': format_currency(item.cost), 'amount': item.cost, 'messages': f'{item.messages:,}',
                                 'coverage': item.detail_coverage} for item in sorted(aggregate.by_model.values(), key=lambda m: m.cost, reverse=True)]
        self._state['tools'] = [{'name': name, 'tokens': f'{item.tokens.total:,}', 'cost': format_currency(item.cost),
                                'messages': f'{item.messages:,}', 'conversations': f'{item.conversations:,}', 'calls': f'{item.tool_calls:,}'}
                               for name, item in sorted(aggregate.by_analyzer.items(), key=lambda p: p[1].cost, reverse=True)]
        self._state['days'] = [{'name': row.day.strftime('%d %b %Y'), 'tokens': f'{row.total.tokens.total:,}',
                               'cost': format_currency(row.total.cost), 'messages': f'{row.total.messages:,}',
                               'tools': ', '.join(row.analyzers)} for row in daily_data_rows(self.dataset, start, end)]
        self._emit()

    @Slot(str)
    def choosePeriod(self, preset):
        if preset not in ('Day', 'Week', 'Month', 'Year', 'All time'):
            return
        self._preset, self._offset, self._custom_range = preset, 0, None
        self._render()

    @Slot(int)
    def cyclePeriod(self, direction):
        if direction not in (-1, 1):
            return
        periods = ('Day', 'Week', 'Month', 'Year', 'All time')
        self.choosePeriod(periods[(periods.index(self._preset) + direction) % len(periods)])

    @Slot(int)
    def shiftPeriod(self, direction):
        if direction not in (-1, 1):
            return
        if self._custom_range:
            start, end = self._custom_range
            delta = timedelta(days=(end - start).days + 1) * direction
            if (start + delta).year < 1970 or (end + delta).year > 2199:
                return
            self._custom_range = (start + delta, end + delta)
        elif self._preset != 'All time':
            offset = min(0, self._offset + direction)
            if shifted_range(self._preset, offset, date.today(), self.dataset)[0].year < 1970:
                return
            self._offset = offset
        self._render()

    @Slot(str, str, result=bool)
    def chooseDates(self, first, last):
        try:
            start, end = date.fromisoformat(first), date.fromisoformat(last)
            if start > end or start.year < 1970 or end.year > 2199:
                return False
        except ValueError:
            return False
        self._custom_range = (start, end)
        self._render()
        return True

    @Slot(str)
    def setTheme(self, name):
        if name in ('Pearl', 'Nord'):
            self._state['theme'] = name
            if not self.demo:
                preferences.save(theme=name)
            self._emit()

    @Slot(str)
    def setUsageMode(self, mode):
        if mode not in ('combined', 'local', 'codex') or mode == self._state['usageMode']:
            return
        self._state['usageMode'] = mode
        self._usage_generation += 1
        if not self.demo:
            preferences.save(usage_mode=mode)
        self.refresh()
        self._emit()

    @Slot()
    def beginSetup(self):
        self._timer.stop()
        self._quota_timer.stop()
        self._auth_cancel.set()
        self._setup_generation += 1
        self._state.update(onboarding=True, setupStage='welcome', setupError='', setupBusy=False, setupCode='')
        self._emit()

    @Slot()
    def setupContinue(self):
        stage = self._state['setupStage']
        if self._state['setupBusy']:
            return
        if stage == 'welcome':
            self._state.update(setupStage='account', setupError='', ghAvailable=bool(shutil.which('gh')))
            if self._state['ghAvailable'] and not self.demo:
                self._state['setupBusy'] = True
                self._run('account', sync.account, self._setup_generation)
        elif stage == 'account':
            if self._state['setupLogin']:
                self._state['setupStage'] = 'repository'
                if not self._state['repoExisting']:
                    self._state['repoName'] = 'splitrail-usage'
            elif not self._state['ghAvailable']:
                self.openLink('https://cli.github.com/')
            else:
                self.signIn()
        elif stage == 'ready':
            self.finishSetup()
        self._emit()

    @Slot()
    def checkGitHub(self):
        self._state['ghAvailable'] = bool(shutil.which('gh'))
        if self._state['ghAvailable'] and not self.demo:
            self._state['setupBusy'] = True
            self._run('account', sync.account, self._setup_generation)
        self._emit()

    @Slot()
    def signIn(self):
        if self.demo:
            self._state['setupError'] = 'Sign-in is disabled in demo mode.'
            self._emit()
            return
        if self._state['setupBusy']:
            return
        self._auth_cancel = threading.Event()
        cancel = self._auth_cancel
        generation = self._setup_generation
        self._state.update(setupBusy=True, setupError='', setupCode='', setupStage='auth')
        self._run('auth', lambda: sync.sign_in(lambda code: self.setupEvent.emit('code', code, generation), cancel), generation)
        self._emit()

    @Slot(str, str, int)
    def _setup_event(self, kind, value, generation):
        if generation != self._setup_generation or self.closed:
            return
        if kind == 'code':
            self._state['setupCode'] = value
        elif kind == 'created':
            self._state.update(repoName=value, repoExisting=True)
        self._emit()

    @Slot()
    def setupBack(self):
        if 'connect' in self._tasks:
            return
        self._auth_cancel.set()
        self._setup_generation += 1
        current = self._state['setupStage']
        self._state.update(setupStage='account' if current in ('auth', 'repository') else 'welcome',
                           setupError='', setupBusy=False, setupCode='')
        self._emit()

    @Slot()
    def finishSetup(self):
        if 'connect' in self._tasks:
            return
        self._auth_cancel.set()
        self._setup_generation += 1
        self._state.update(onboarding=False, setupBusy=False, setupError='')
        if not self.demo:
            preferences.save(onboarded=True)
        self._load_settings()
        self._emit()
        if self.auto_refresh:
            self.refresh()

    @Slot(str, bool, bool)
    def connectRepository(self, name, existing, automatic):
        if self.demo:
            self._state['setupError'] = 'Sync setup is disabled in demo mode.'
            self._emit()
            return
        if self._state['setupBusy']:
            return
        name = name.strip()
        generation = self._setup_generation
        scope = self._state['syncScope']
        self._state.update(setupBusy=True, setupError='')
        def connect():
            repository = name if existing else sync.create_private_repo(name)
            if not existing:
                self.setupEvent.emit('created', repository, generation)
            return sync.configure(repository, scope=scope, automatic=automatic)
        self._run('connect', connect, generation)
        self._emit()

    @Slot()
    def syncNow(self):
        if self.demo:
            return
        if not self._state['connected']:
            self.beginSetup()
            return
        if self._state['busy'] or self._state['syncBusy']:
            return
        self._state.update(syncBusy=True, settingsError='')
        self._run('sync', sync.sync_now)
        self._emit()

    @Slot(bool, bool, str)
    def updateSync(self, automatic, receive_only, scope):
        if self.demo or not self._state['connected'] or self._state['syncBusy']:
            return
        repository = self._state['repository']
        self._state.update(syncBusy=True, settingsError='')
        self._run('sync-settings', lambda: sync.configure(repository, automatic=automatic, receive_only=receive_only, scope=scope))
        self._emit()

    @Slot()
    def disconnect(self):
        if self.demo or self._state['syncBusy']:
            return
        self._state['syncBusy'] = True
        self._run('disconnect', sync.disconnect)
        self._emit()

    @Slot()
    def loadPrices(self):
        try:
            overrides = load_overrides()
            unknown = self.usage_result.cost_diagnostics.unknown_models if self.usage_result else ()
            rows = []
            for name in sorted(set(CATALOG['models']) | set(overrides) | set(unknown)):
                rate = resolve_rates(name, overrides=overrides)
                builtin = CATALOG['models'].get(name, {})
                rows.append({'name': name, 'provider': builtin.get('provider', 'Custom'),
                             'custom': name in overrides, 'missing': rate is None,
                             'priceNote': CATALOG.get('unpriced', {}).get(name, ''),
                             **{key: str(rate[key]) if rate and rate.get(key) is not None else '' for key in RATE_FIELDS}})
            self._state['prices'] = rows
        except (ValueError, OSError) as exc:
            self._state['priceError'] = str(exc)
        self._emit()

    @Slot(str, str, str, str, str, result=bool)
    def savePrice(self, model, input_rate, output_rate, read_rate, write_rate):
        try:
            values = dict(zip(RATE_FIELDS, (input_rate, output_rate, read_rate or None, write_rate or None)))
            if not self.demo:
                save_override(model, values)
            else:
                from .pricing import validate_rates
                validate_rates(values)
            self._state.update(priceError='', priceNotice='Price saved')
            self.loadPrices()
            if self._state['busy'] or self._state['syncBusy']:
                self._pricing_pending = True
            else:
                self.refresh()
            return True
        except (ValueError, OSError) as exc:
            self._state['priceError'] = str(exc)
            self._emit()
            return False

    @Slot(str)
    def removePrice(self, model):
        try:
            if not self.demo:
                save_override(model, None)
            self._state.update(priceError='', priceNotice='Built-in price restored')
            self.loadPrices()
            if self._state['busy'] or self._state['syncBusy']:
                self._pricing_pending = True
            else:
                self.refresh()
        except (ValueError, OSError) as exc:
            self._state['priceError'] = str(exc)
            self._emit()

    @Slot(str)
    def transfer(self, action):
        if self.demo or self._state['transferBusy']:
            return
        from .portable import scan_codex, write_export, import_export, data_dir
        if action == 'export':
            path, _ = QFileDialog.getSaveFileName(None, 'Export Codex usage', 'codex-usage.json.gz', 'Usage (*.json.gz)')
            def work():
                payload = scan_codex()
                write_export(Path(path), payload)
                return f"{len(payload['events']):,} requests exported"
        elif action == 'import':
            path, _ = QFileDialog.getOpenFileName(None, 'Import Codex usage', '', 'Usage (*.json.gz *.json)')
            def work():
                return f'{import_export(Path(path)):,} new requests added'
        else:
            return
        if path:
            self._state['transferBusy'] = True
            self._run(action, work)
            self._emit()

    @Slot()
    def removeImports(self):
        if self.demo or self._state['transferBusy']:
            return
        from .portable import data_dir
        self._state['transferBusy'] = True
        self._run('remove-imports', lambda: (data_dir() / 'imported-codex-usage.json.gz').unlink(missing_ok=True))
        self._emit()

    @Slot(str)
    def copy(self, value):
        QGuiApplication.clipboard().setText(value)

    @Slot(str)
    def openLink(self, url):
        allowed = ('https://github.com/login/device', 'https://cli.github.com/', 'https://github.com/Piebald-AI/splitrail')
        if url in allowed:
            QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def close(self):
        self.closed = True
        self._auth_cancel.set()
        self._timer.stop()
        self._quota_timer.stop()
        self._clock.stop()
