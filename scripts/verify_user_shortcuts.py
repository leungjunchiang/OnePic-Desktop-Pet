"""Windows 主动快捷与被动窗口验收：自有非激活窗口模拟几何，监测真实前台句柄；不联网。"""
from __future__ import annotations
import os, sys, ctypes, json
from pathlib import Path
if sys.platform != 'win32':
    raise SystemExit('This native verification requires Windows.')
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['ONEPIC_USE_DEMO_ASSETS'] = '1'
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root/'src'), str(root/'tests')]
from PySide6.QtCore import Qt, QObject, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PySide6.QtTest import QTest
from onepic_desktop_pet import activity
import onepic_desktop_pet.window as window_module
from onepic_desktop_pet.buddy_reminder_toast import BuddyReminderToast
from onepic_desktop_pet.alarm_manager import Alarm
from onepic_desktop_pet.alarm_ui import AlarmCard
from types import SimpleNamespace
from test_window import _create_window

app = QApplication.instance() or QApplication([])
u = ctypes.windll.user32
activity._prepare_windows_geometry_api(u)
u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
original_foreground = int(u.GetForegroundWindow() or 0)
record = {'geometry_source':'owned non-activating windows, not a DirectX game', 'foreground_samples':[], 'shown_windows':[]}
class Observer(QObject):
    def eventFilter(self, obj, event):
        if isinstance(obj, QWidget) and obj.isWindow() and event.type()==QEvent.Type.Show:
            record['shown_windows'].append({'class':type(obj).__name__, 'width':obj.width(), 'height':obj.height(),
                                           'no_focus':bool(obj.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)})
        return False
observer=Observer();app.installEventFilter(observer)
host=QWidget()
host.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowDoesNotAcceptFocus)
host.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
QVBoxLayout(host).addWidget(QLabel('Local passive-window verification'))
host.resize(640,400)
BuddyReminderToast._set_windows_no_activate(host, tool_window=False)
host.show();app.processEvents()
reference = None
def mode(bounds=None, *, reference_hwnd=None):
    return activity._windows_foreground_display_mode(u, int(host.winId()), reference_hwnd=reference_hwnd)
activity.active_window_display_mode=mode
activity.active_application_name=lambda:'chrome.exe'
window_module.detect_quiet_mode=lambda:SimpleNamespace(blocked=False)
app,pet=_create_window()
for timer in pet.findChildren(QTimer):timer.stop()
reference=int(pet.winId())
pet._foreground_display_mode=lambda:mode(reference_hwnd=reference)
def sample(label):
    for _ in range(25):
        app.processEvents()
        actual=int(u.GetForegroundWindow() or 0)
        record['foreground_samples'].append({'phase':label,'unchanged':actual==original_foreground})
        assert actual==original_foreground, f'foreground changed during {label}'
        QTest.qWait(2)
record['normal_mode']=mode(reference_hwnd=reference)
assert record['normal_mode']=='normal'
pet.notification_manager.notify('buddy:native','New request','Complete body',lambda:None)
pet.notification_manager.flush();sample('toast')
toast=pet.notification_manager.current
assert toast is not None and toast.isVisible()
record['toast_noactivate']=bool(u.GetWindowLongPtrW(int(toast.winId()), -20)&0x08000000)
assert record['toast_noactivate']
pet.notification_manager.notify('nudge:second','Second request','Complete body',lambda:None)
pet.notification_manager.flush();sample('replacement')
record['max_visible_toasts']=len([w for w in app.topLevelWidgets() if isinstance(w,BuddyReminderToast) and w.isVisible()])
assert record['max_visible_toasts']==1
child_count = len(pet.findChildren(QWidget))
pet._notify_instant_interaction('room:cheer','cheer','Cheer','Body');sample('inline')
record['interaction_rendered_inside_pet']=len(pet.findChildren(QWidget))<=child_count and bool(pet._interaction_hint_text)
assert record['interaction_rendered_inside_pet']
pet.grab().save(str(root.parent/'v315-inline-hint.png'))
# 用户主动双击/右击须不受后台免打扰误拦，仍然保留非激活样式。
window_module.detect_quiet_mode=lambda:SimpleNamespace(blocked=True,reason='meeting')
pet.notification_manager.clear_all()
QTest.mouseDClick(pet,Qt.MouseButton.LeftButton)
assert pet.quick_panel.isVisible()
pet.quick_panel._set_report_button_visible(True)
assert pet.quick_panel.report_button.isVisible()
pet.quick_panel._show_hint(pet.quick_panel.work_button)
assert pet.quick_panel.hover_hint.isVisible()
pet._show_work_controls()
assert pet.work_controls.isVisible()
record['explicit_shortcuts_noactivate']=all(bool(u.GetWindowLongPtrW(int(w.winId()),-20)&0x08000000) for w in (pet.quick_panel,pet.quick_panel.hover_hint,pet.work_controls))
assert record['explicit_shortcuts_noactivate']
assert not pet._notify_instant_interaction('room:quiet','cheer','Cheer','Body')
pet.quick_panel.grab().save(str(root.parent/'v315-user-shortcuts.png'))
sample('explicit-shortcuts')
# 用户开启彩雾可以显示，但同一免打扰环境的远端通知仍然被拦截。
assert pet._toggle_color_mist_world()
assert pet._local_burst_effect.isVisible()
sample('explicit-color-world')
pet._toggle_color_mist_world()
pet.quick_panel.hide();pet.quick_panel.hover_hint.hide();pet.work_controls.hide()
window_module.detect_quiet_mode=lambda:SimpleNamespace(blocked=False)
host.showMaximized();app.processEvents()
record['maximized_mode']=mode(reference_hwnd=reference)
assert record['maximized_mode']=='maximized'
sample('maximized')
for label, borderless in [('borderless', True), ('fullscreen', False)]:
    host.setWindowFlag(Qt.WindowType.FramelessWindowHint,borderless)
    BuddyReminderToast._set_windows_no_activate(host, tool_window=False)
    host.showFullScreen();app.processEvents()
    assert mode(reference_hwnd=reference)=='fullscreen'
    pet._poll_fullscreen_visibility();pet._poll_fullscreen_visibility()
    assert not pet.isVisible()
    count=len(record['shown_windows'])
    pet._notify_instant_interaction('room:'+label,'cheer','Cheer','Body')
    pet.notification_manager.notify('work:'+label,'Work','Body',lambda:None)
    pet.notification_manager.flush()
    pet.visit_status_bubble.set_taunter('Buddy',remaining_seconds=1200)
    pet._topmost_watchdog_tick();sample(label)
    assert len(record['shown_windows'])==count, 'fullscreen created a visible passive window'
    host.showNormal();app.processEvents()
    pet._poll_fullscreen_visibility();pet._poll_fullscreen_visibility();pet._finish_fullscreen_restore()
    assert pet.isVisible() and not pet._interaction_hint_text and pet.notification_manager.current is None
    sample(label+'-exit')
# 不播放真实音频；验证音频启动入口与 GUI HWND 独立。
audio=[]
old_audio=AlarmCard._start_alarm_audio
AlarmCard._start_alarm_audio=lambda card:audio.append(card.alarm.id)
host.showFullScreen();app.processEvents()
pet._show_alarm_card(Alarm(id='native-alarm',title='Alarm',trigger_at='2026-10-02T12:00:00',sound_enabled=False))
card=pet._alarm_card
record['alarm_audio_requested']=audio==['native-alarm']
record['alarm_has_no_native_window']=not card.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
assert record['alarm_audio_requested'] and record['alarm_has_no_native_window']
sample('alarm')
AlarmCard._start_alarm_audio=old_audio
record['foreground_unchanged']=all(s['unchanged'] for s in record['foreground_samples'])
(root.parent/'v315-native-passive.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in record.items() if k not in {'foreground_samples','shown_windows'}},ensure_ascii=False))
pet.close();host.close();pet.deleteLater();host.deleteLater();app.processEvents()
