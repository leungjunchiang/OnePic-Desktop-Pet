"""验证八日硬额度、取消不退额、收件箱分组与新事件通知边界。"""
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
    assert len(groups['今天'])==len(groups['更早'])==len(groups['待我回应'])==1
    assert response_actions(rows[0])==[('回个加油','cheer')]
    assert response_actions(rows[1])==[('去处理','handle')]
    assert ('去开工','focus') in response_actions({'event_type':'start'})

def test_feed_is_compact_and_responses_reuse_callback(app):
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
