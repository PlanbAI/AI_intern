#!/usr/bin/env python3
import json, signal, sys, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi
from Xlib import X, display, error

OUTPUT_FILE = Path('events.jsonl')
IGNORED_APPLICATIONS = {'gnome-shell','ibus','xdg-desktop-portal','gnome-settings-daemon'}
IGNORE_CANDIDATE_MIN_WINDOW_EVENTS = 10
IGNORE_CANDIDATE_MAX_UI_EVENTS = 0
stop_event = threading.Event(); write_lock = threading.Lock(); application_stats = {}

def now_iso(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
def read_process_name(pid: Optional[int]):
    if not pid: return None
    try: return Path(f'/proc/{pid}/comm').read_text().strip()
    except (OSError, PermissionError): return None

def norm(s): return s.strip().lower() if s else ''
def ignored(*names): return bool({norm(n) for n in names if n} & IGNORED_APPLICATIONS)
def update_stats(app, typ):
    if not app: return
    s=application_stats.setdefault(norm(app), {'window_events':0,'ui_events':0})
    if typ.startswith('window.'): s['window_events'] += 1
    elif typ.startswith('ui.'): s['ui_events'] += 1

def emit_event(event):
    event.setdefault('timestamp', now_iso())
    app=(event.get('application') or {}).get('name')
    if ignored(app): return
    update_stats(app, event['type'])
    line=json.dumps(event, ensure_ascii=False, separators=(',',':'))
    with write_lock:
        with OUTPUT_FILE.open('a', encoding='utf-8') as f: f.write(line+'\n')
        print(line, flush=True)

class X11Collector:
    def __init__(self):
        self.d=display.Display(); self.root=self.d.screen().root
        self.a_active=self.d.intern_atom('_NET_ACTIVE_WINDOW'); self.a_pid=self.d.intern_atom('_NET_WM_PID')
        self.a_name=self.d.intern_atom('_NET_WM_NAME'); self.a_utf8=self.d.intern_atom('UTF8_STRING'); self.last=None
    def start(self):
        self.root.change_attributes(event_mask=X.PropertyChangeMask); self.emit_active()
        while not stop_event.is_set():
            try: e=self.d.next_event()
            except Exception:
                if stop_event.is_set(): break
                raise
            if e.type == X.PropertyNotify and e.atom == self.a_active: self.emit_active()
    def active_id(self):
        p=self.root.get_full_property(self.a_active, X.AnyPropertyType)
        return int(p.value[0]) if p and p.value else None
    def emit_active(self):
        wid=self.active_id()
        if not wid or wid == self.last: return
        self.last=wid
        try: w=self.d.create_resource_object('window', wid)
        except error.XError: return
        try:
            p=w.get_full_property(self.a_pid, X.AnyPropertyType); pid=int(p.value[0]) if p and p.value else None
        except error.XError: pid=None
        try:
            p=w.get_full_property(self.a_name, self.a_utf8); v=p.value if p and p.value else b''
            title=v.decode('utf-8', errors='replace') if isinstance(v, bytes) else str(v)
        except error.XError: title=''
        try: wc=w.get_wm_class(); wm_class=wc[-1] if wc else None
        except error.XError: wm_class=None
        process=read_process_name(pid)
        if ignored(process, wm_class): return
        emit_event({'type':'window.focused','source':'x11','application':{'name':process or wm_class or 'unknown','pid':pid,'wm_class':wm_class},'window':{'id':wid,'title':title}})

class AtspiCollector:
    EVENT_TYPES=('object:state-changed:focused','object:selection-changed','object:text-changed','window:activate')
    def __init__(self): Atspi.init(); self.listener=Atspi.EventListener.new(self._on_event)
    def register(self):
        for t in self.EVENT_TYPES:
            try: print(f'AT-SPI register {t}: {self.listener.register(t)}')
            except Exception as ex: print(f'Cannot register {t}: {ex}', file=sys.stderr)
    def stop(self):
        for t in self.EVENT_TYPES:
            try: self.listener.deregister(t)
            except Exception: pass
        try: Atspi.event_quit()
        except Exception: pass
    def _on_event(self, e):
        try:
            s=e.source
            if s is None: return
            try: name=s.get_name() or ''
            except Exception: name=''
            try: role=s.get_role_name() or ''
            except Exception: role=''
            try: pid=s.get_process_id()
            except Exception: pid=None
            process=read_process_name(pid)
            try:
                a=s.get_application(); an=(a.get_name() or '') if a else ''
            except Exception: an=''
            app=process or an or 'unknown'
            if ignored(app, process, an): return
            typ=self.translate(e.type)
            payload={'type':typ,'source':'atspi','native_event':e.type,'application':{'name':app,'pid':pid,'atspi_name':an},'element':{'role':role,'name':name}}
            if e.type.startswith('object:text-changed'): payload['content_redacted']=True
            emit_event(payload)
        except Exception as ex: print(f'AT-SPI event error: {ex}', file=sys.stderr)
    @staticmethod
    def translate(t):
        if t.startswith('object:state-changed:focused'): return 'ui.focus'
        if t.startswith('object:text-changed'): return 'ui.text_changed'
        if t.startswith('object:selection-changed'): return 'ui.selection_changed'
        if t.startswith('window:activate'): return 'window.activated'
        return 'ui.event'

def print_ignore_candidates():
    c=[(a,s) for a,s in application_stats.items() if a not in IGNORED_APPLICATIONS and s['window_events']>=IGNORE_CANDIDATE_MIN_WINDOW_EVENTS and s['ui_events']<=IGNORE_CANDIDATE_MAX_UI_EVENTS]
    if c:
        print('\nPossible ignore candidates:')
        for a,s in c: print(f"{a}: window_events={s['window_events']}, ui_events={s['ui_events']}")

def main():
    print(f'Linux Collector\nOutput: {OUTPUT_FILE.resolve()}\nPress Ctrl+C to stop.\n')
    x=X11Collector(); a=AtspiCollector(); th=threading.Thread(target=x.start, daemon=True)
    def shutdown(*_):
        if not stop_event.is_set(): print('\nStopping collector...'); stop_event.set(); a.stop()
    signal.signal(signal.SIGINT, shutdown); signal.signal(signal.SIGTERM, shutdown)
    a.register(); th.start()
    try: Atspi.event_main()
    finally: stop_event.set(); print_ignore_candidates(); print(f'\nEvents written to {OUTPUT_FILE.resolve()}')

if __name__ == '__main__': main()
