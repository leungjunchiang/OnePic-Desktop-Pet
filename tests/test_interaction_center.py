"""验证八日硬额度、取消不退额、收件箱分组与通知边界；固定样例日期不随运行日过期。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt
from onepic_desktop_pet.discipline import DisciplineStore
from onepic_desktop_pet.time_service import BEIJING_TIMEZONE
from onepic_desktop_pet.interaction_center import grouped_rows,response_actions,InteractionFeed
from onepic_desktop_pet.notification_manager import NotificationManager

@pytest.fixture
def app(): return QApplication.instance() or QApplication([])

def test_month_quota_distinct_dates_cancel_does_not_refund_and_rollover(tmp_path):
    store=DisciplineStore('a',path=tmp_path/'d.json')
    store.settings.workdays=['mon','tue','wed','thu','fri','sat','sun']
    for day in range(1,9):
        at=datetime(2026,9,day,12,tzinfo=BEIJING_TIMEZONE)
        assert store.exempt_today(at)
        store.cancel_exemption(at+timedelta(minutes=1))
        assert not store.is_exempt(at.date())
        assert store.exempt_today(at+timedelta(minutes=2))
        assert store.is_exempt(at.date())
        assert store.exemption_quota(at)['used']==day
    assert not store.exempt_today(datetime(2026,9,9,12,tzinfo=BEIJING_TIMEZONE))
    # UTC Sept 30 16:00 is already October in Beijing.
    next_month=datetime(2026,9,30,16,tzinfo=timezone.utc)
    assert store.exemption_quota(next_month)['used']==0
    assert store.exempt_today(next_month)

def test_fixed_rest_days_and_cross_device_quota(tmp_path):
    store=DisciplineStore('a',path=tmp_path/'d.json')
    assert not store.exempt_today(datetime(2026,10,3,12,tzinfo=BEIJING_TIMEZONE))
    store.merge_remote({'rest_day_quota':{'used_dates':[f'2026-10-{d:02d}' for d in range(1,9)]}})
    at=datetime(2026,10,9,12,tzinfo=BEIJING_TIMEZONE)
    assert not store.exemption_quota(at)['can_use']
    assert not store.exempt_today(at)
    assert DisciplineStore('a',path=store.path).exemption_quota(at)['used']==8

def test_remote_reenable_updates_same_fact_and_cache_addition_is_bounded(tmp_path):
    store=DisciplineStore('a',path=tmp_path/'d.json')
    at=datetime(2026,10,1,12,tzinfo=BEIJING_TIMEZONE)
    rest={'id':'rest','event_type':'rest_day','event_date':'2026-10-01','occurred_at':at.isoformat()}
    cancel={'id':'cancel','event_type':'cancel_rest_day','event_date':'2026-10-01','occurred_at':(at+timedelta(minutes=1)).isoformat()}
    store.merge_remote({'events':[rest,cancel]})
    assert not store.is_exempt(at.date())
    store.merge_remote({'events':[{**rest,'occurred_at':(at+timedelta(minutes=2)).isoformat()}]})
    assert store.is_exempt(at.date())
    store.merge_remote({'rest_day_quota':{'month':'2026-10','used_dates':[f'2026-10-{d:02d}' for d in range(1,8)]}})
    store.settings.workdays=['mon','tue','wed','thu','fri','sat','sun']
    assert store.exempt_today(at.replace(day=8))
    assert not store.exempt_today(at.replace(day=9))

def test_unread_independent_of_required_action():
    now=datetime(2026,10,1,12,tzinfo=BEIJING_TIMEZONE)
    rows=[{'event_type':'cheer','unread':True,'requires_action':False,'created_at':now.isoformat()},
          {'event_type':'coaching_action','unread':False,'requires_action':True},
          {'event_type':'visit','created_at':(now-timedelta(days=1)).isoformat()}]
    groups=grouped_rows(rows,now)
    assert len(groups['今天'])==len(groups['昨天'])==len(groups['待我回应'])==1
    assert response_actions(rows[0])==[('回个加油','cheer')]
    assert response_actions(rows[1])==[('去处理','handle')]
    assert ('去开工','focus') in response_actions({'event_type':'start'})

def test_feed_is_compact_and_responses_reuse_callback(app, monkeypatch):
    monkeypatch.setattr('onepic_desktop_pet.interaction_center.now_beijing',
                        lambda: datetime(2026, 10, 1, 12, tzinfo=BEIJING_TIMEZONE))
    calls=[]
    feed=InteractionFeed(lambda row,action:calls.append(action))
    assert feed.sizeHint().height()<160
    feed.render([{'event_id':'1','event_type':'cheer','nickname':'论文搭子','created_at':'2026-10-01T03:32:00Z'}])
    from PySide6.QtWidgets import QPushButton
    button=next(b for b in feed.findChildren(QPushButton) if b.text()=='回个加油')
    button.click()
    assert calls==['cheer']
    feed.deleteLater()

def test_initial_load_reconnect_empty_and_burst_notification(app):
    parent=QWidget();manager=NotificationManager(parent)
    now=datetime.now(timezone.utc)
    seen=[]
    manager.observe('visit',[{'id':'old','created_at':now.isoformat()}],notify=seen.append)
    assert not seen
    manager.baselines['visit']=now-timedelta(seconds=1)
    manager.observe('visit',[{'id':'new','created_at':now.isoformat()}],notify=seen.append)
    assert len(seen)==1
    assert not manager.notify('empty','训导主任','',lambda:None)
    assert manager.notify('one','加油','论文搭子给你加油',lambda:None)
    assert not manager.notify('one','加油','重复',lambda:None)
    manager.notify('two','投喂','室友投喂咖啡',lambda:None)
    manager.flush()
    toast=manager.current
    assert '2 条新互动' in toast.title_label.text()
    assert toast.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert toast.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert toast.clock.remaining_ms==3000
    manager.notify('three','新事项','正文',lambda:None);manager.flush()
    assert not toast.isVisible()
    assert manager.current.isVisible()
    manager.clear_all()
    assert manager.current is None and not manager.timer.isActive()
    parent.deleteLater()

def test_dnd_consumes_notification_only_and_foreground_is_inline(app):
    parent=QWidget();state={'blocked':True};inline=[]
    manager=NotificationManager(parent,blocked=lambda:state['blocked'],foreground=lambda:True,inline=inline.append)
    assert not manager.notify('one','标题','正文',lambda:None)
    state['blocked']=False
    assert not manager.notify('one','标题','正文',lambda:None)
    manager.notify('two','标题','正文',lambda:None);manager.flush()
    assert inline==['标题\n正文'] and manager.current is None
    parent.deleteLater()


def test_date_feed_converts_utc_before_grouping_and_filters_seven_calendar_days():
    now=datetime(2026,10,1,12,tzinfo=BEIJING_TIMEZONE)
    rows=[{'event_id':'midnight','created_at':'2026-09-30T18:30:00Z'},
          {'event_id':'yesterday','created_at':'2026-09-30T14:05:00Z'},
          {'event_id':'old','created_at':'2026-09-29T08:00:00+08:00'},
          {'event_id':'first','created_at':'2026-09-24T16:00:00Z'},
          {'event_id':'outside','created_at':'2026-09-24T15:59:59Z'},
          {'event_id':'future','created_at':'2026-10-01T16:00:00Z'}]
    groups=grouped_rows(rows,now)
    assert list(groups)==['待我回应','今天','昨天','9月29日','9月25日']
    assert groups['今天'][0]['event_id']=='midnight'
    assert groups['昨天'][0]['event_id']=='yesterday'
    assert not any(r['event_id'] in {'outside','future'} for rows in groups.values() for r in rows)

def test_feed_year_labels_sort_limit_and_unknown_time():
    now=datetime(2026,1,2,12,tzinfo=BEIJING_TIMEZONE)
    groups=grouped_rows([{'created_at':'2025-12-31T12:00:00Z'}, {'created_at':'2026-01-01T12:00:00Z'},
                         {'created_at':'invalid'}, {'requires_action':True,'created_at':'2020-01-01T00:00:00Z'}],now)
    assert list(groups)==['待我回应','昨天','2025年12月31日','日期未知']
    rows=[{'event_id':str(n),'created_at':(now+timedelta(seconds=n)).isoformat()} for n in range(40)]
    groups=grouped_rows(rows,now)
    assert len(groups['今天'])==30
    assert [r['event_id'] for r in groups['今天']]==[str(n) for n in range(39,9,-1)]

def test_card_time_uses_same_server_naive_utc_contract_as_group_heading(app, monkeypatch):
    import onepic_desktop_pet.interaction_center as module
    from PySide6.QtWidgets import QLabel
    monkeypatch.setattr(module,'now_beijing',lambda:datetime(2026,10,1,12,tzinfo=BEIJING_TIMEZONE))
    feed=InteractionFeed(lambda *args:None)
    feed.render([{'event_id':'one','created_at':'2026-09-30T18:30:00','event_type':'cheer'},
                 {'event_id':'two','created_at':'2026-09-30T14:05:00Z','event_type':'tease'}])
    labels=[w.text() for w in feed.findChildren(QLabel)]
    assert '今天 · 1' in labels and '昨天 · 1' in labels
    assert '02:30' in labels and '22:05' in labels
    assert not any('更早' in s for s in labels)
    feed.deleteLater()

def test_instant_hint_repeats_restart_clock_and_different_kind_replaces():
    from onepic_desktop_pet.interaction_center import InteractionHintState,INTERACTION_HINT_DURATION_MS
    state=InteractionHintState()
    assert INTERACTION_HINT_DURATION_MS==4000
    assert state.text('return',at=10)=='👀 该回来了 · +1'
    assert state.text('return',at=12)=='👀 该回来了 · +2'
    assert state.until==16
    assert state.text('cheer',at=13)=='💪 加油 · +1'
    assert state.text('cheer',at=17)=='💪 加油 · +1'

def test_instant_display_shares_event_deduplication_and_dnd(app):
    parent=QWidget();shown=[];blocked=[False]
    manager=NotificationManager(parent,blocked=lambda:blocked[0])
    assert manager.notify('new','标题','正文',lambda:None,display=lambda:shown.append('new'))
    assert not manager.notify('new','标题','正文',lambda:None,display=lambda:shown.append('duplicate'))
    assert shown==['new'] and manager.current is None and not manager.timer.isActive()
    blocked[0]=True
    assert not manager.notify('quiet','标题','正文',lambda:None,display=lambda:shown.append('quiet'))
    parent.deleteLater()
