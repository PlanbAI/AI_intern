#!/usr/bin/env python3

import json
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

from Xlib import X, display, error

OUTPUT_FILE = Path("events.jsonl")

IGNORED_APPLICATIONS = {
    "gnome-shell",
    "ibus",
    "xdg-desktop-portal",
    "gnome-settings-daemon",
}

IGNORE_CANDIDATE_MIN_WINDOW_EVENTS = 10
IGNORE_CANDIDATE_MAX_UI_EVENTS = 0

stop_event = threading.Event()
write_lock = threading.Lock()
application_stats = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def read_process_name(pid: Optional[int]) -> Optional[str]:
    if not pid:
        return None
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except (OSError, PermissionError):
        return None


def normalize_app_name(name: Optional[str]) -> str:
    return name.strip().lower() if name else ""


def is_ignored_application(*names: Optional[str]) -> bool:
    normalized = {normalize_app_name(name) for name in names if name}
    return bool(normalized & IGNORED_APPLICATIONS)


def update_stats(app: Optional[str], event_type: str):
    if not app:
        return
    app = normalize_app_name(app)
    stats = application_stats.setdefault(app, {"window_events": 0, "ui_events": 0})
    if event_type.startswith("window."):
        stats["window_events"] += 1
    elif event_type.startswith("ui."):
        stats["ui_events"] += 1


def emit_event(event: dict):
    event.setdefault("timestamp", now_iso())
    application = event.get("application") or {}
    app_name = application.get("name")

    if is_ignored_application(app_name):
        return

    if app_name:
        update_stats(app_name, event["type"])

    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with write_lock:
        with OUTPUT_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)


class X11Collector:
    def __init__(self):
        self.display = display.Display()
        self.root = self.display.screen().root
        self.atom_active_window = self.display.intern_atom("_NET_ACTIVE_WINDOW")
        self.atom_wm_pid = self.display.intern_atom("_NET_WM_PID")
        self.atom_net_wm_name = self.display.intern_atom("_NET_WM_NAME")
        self.atom_utf8_string = self.display.intern_atom("UTF8_STRING")
        self.last_window_id = None

    def start(self):
        self.root.change_attributes(event_mask=X.PropertyChangeMask)
        self.emit_active_window()

        while not stop_event.is_set():
            try:
                event = self.display.next_event()
            except Exception:
                if stop_event.is_set():
                    break
                raise

            if event.type == X.PropertyNotify and event.atom == self.atom_active_window:
                self.emit_active_window()

    def get_active_window_id(self) -> Optional[int]:
        prop = self.root.get_full_property(self.atom_active_window, X.AnyPropertyType)
        if not prop or not prop.value:
            return None
        return int(prop.value[0])

    def get_window(self, window_id):
        try:
            return self.display.create_resource_object("window", window_id)
        except error.XError:
            return None

    def get_window_pid(self, window) -> Optional[int]:
        try:
            prop = window.get_full_property(self.atom_wm_pid, X.AnyPropertyType)
            if prop and prop.value:
                return int(prop.value[0])
        except error.XError:
            pass
        return None

    def get_window_title(self, window) -> str:
        try:
            prop = window.get_full_property(self.atom_net_wm_name, self.atom_utf8_string)
            if prop and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value)
        except error.XError:
            pass

        try:
            return window.get_wm_name() or ""
        except error.XError:
            return ""

    def get_wm_class(self, window) -> Optional[str]:
        try:
            wm_class = window.get_wm_class()
            return wm_class[-1] if wm_class else None
        except error.XError:
            return None

    def emit_active_window(self):
        window_id = self.get_active_window_id()
        if not window_id or window_id == self.last_window_id:
            return

        self.last_window_id = window_id
        window = self.get_window(window_id)
        if not window:
            return

        pid = self.get_window_pid(window)
        process_name = read_process_name(pid)
        wm_class = self.get_wm_class(window)
        title = self.get_window_title(window)

        if is_ignored_application(process_name, wm_class):
            return

        app_name = process_name or wm_class or "unknown"

        emit_event({
            "type": "window.focused",
            "source": "x11",
            "application": {
                "name": app_name,
                "pid": pid,
                "wm_class": wm_class,
            },
            "window": {
                "id": window_id,
                "title": title,
            },
        })


class AtspiCollector:
    EVENT_TYPES = (
        "object:state-changed:focused",
        "object:state-changed:checked",
        "object:state-changed:selected",
        "object:selection-changed",
        "object:active-descendant-changed",
        "object:property-change:accessible-name",
        "object:property-change:accessible-value",
        "object:text-changed",
        "window:activate",
        "window:create",
        "window:close",
    )

    def __init__(self):
        Atspi.init()
        self.listener = Atspi.EventListener.new(self._on_event)

    @staticmethod
    def get_state_names(source):
        result = []
        state_set = source.get_state_set()
        if state_set is None:
            return result

        interesting_states = (
            Atspi.StateType.ACTIVE,
            Atspi.StateType.CHECKED,
            Atspi.StateType.ENABLED,
            Atspi.StateType.EXPANDED,
            Atspi.StateType.FOCUSED,
            Atspi.StateType.SELECTED,
            Atspi.StateType.SENSITIVE,
            Atspi.StateType.SHOWING,
            Atspi.StateType.VISIBLE,
        )

        for state in interesting_states:
            try:
                if state_set.contains(state):
                    try:
                        result.append(Atspi.state_get_name(state))
                    except Exception:
                        result.append(str(state))
            except Exception:
                pass

        return result

    @staticmethod
    def get_action_names(source):
        result = []
        try:
            action = source.get_action_iface()
        except Exception:
            return result

        if action is None:
            return result

        try:
            count = action.get_n_actions()
        except Exception:
            return result

        for index in range(count):
            try:
                name = action.get_action_name(index)
                if name:
                    result.append(name)
            except Exception:
                pass

        return result

    def register(self):
        for event_type in self.EVENT_TYPES:
            try:
                success = self.listener.register(event_type)
                print(f"AT-SPI register {event_type}: {success}")
            except Exception as exc:
                print(f"Cannot register AT-SPI event {event_type}: {exc}", file=sys.stderr)

    def stop(self):
        for event_type in self.EVENT_TYPES:
            try:
                self.listener.deregister(event_type)
            except Exception:
                pass
        try:
            Atspi.event_quit()
        except Exception:
            pass

    def _on_event(self, event):
        try:
            source = event.source
            if source is None:
                return

            try:
                name = source.get_name() or ""
            except Exception:
                name = ""

            try:
                role = source.get_role_name() or ""
            except Exception:
                role = ""

            try:
                description = source.get_description() or ""
            except Exception:
                description = ""

            try:
                states = self.get_state_names(source)
            except Exception:
                states = []

            try:
                actions = self.get_action_names(source)
            except Exception:
                actions = []

            try:
                pid = source.get_process_id()
            except Exception:
                pid = None

            process_name = read_process_name(pid)

            try:
                application = source.get_application()
                application_name = application.get_name() if application else ""
                application_name = application_name or ""
            except Exception:
                application_name = ""

            app_name = process_name or application_name or "unknown"

            if is_ignored_application(app_name, process_name, application_name):
                return

            event_type = self.translate_event_type(event.type)

            payload = {
                "type": event_type,
                "source": "atspi",
                "native_event": event.type,
                "application": {
                    "name": app_name,
                    "pid": pid,
                    "atspi_name": application_name,
                },
                "element": {
                    "role": role,
                    "name": name,
                    "description": description,
                    "states": states,
                    "actions": actions,
                },
            }

            if event.type.startswith("object:text-changed"):
                payload["content_redacted"] = True

            emit_event(payload)

        except Exception as exc:
            print(f"AT-SPI event error: {exc}", file=sys.stderr)

    @staticmethod
    def translate_event_type(native_type: str) -> str:
        if native_type.startswith("object:state-changed:focused"):
            return "ui.focus"
        if native_type.startswith("object:state-changed:checked"):
            return "ui.checked_changed"
        if native_type.startswith("object:state-changed:selected"):
            return "ui.selected_changed"
        if native_type.startswith("object:text-changed"):
            return "ui.text_changed"
        if native_type.startswith("object:selection-changed"):
            return "ui.selection_changed"
        if native_type.startswith("object:active-descendant-changed"):
            return "ui.active_descendant_changed"
        if native_type.startswith("object:property-change:accessible-name"):
            return "ui.name_changed"
        if native_type.startswith("object:property-change:accessible-value"):
            return "ui.value_changed"
        if native_type.startswith("window:activate"):
            return "window.activated"
        if native_type.startswith("window:create"):
            return "window.created"
        if native_type.startswith("window:close"):
            return "window.closed"
        return "ui.event"


def print_ignore_candidates():
    candidates = []
    for app, stats in application_stats.items():
        if app in IGNORED_APPLICATIONS:
            continue
        if (
            stats["window_events"] >= IGNORE_CANDIDATE_MIN_WINDOW_EVENTS
            and stats["ui_events"] <= IGNORE_CANDIDATE_MAX_UI_EVENTS
        ):
            candidates.append((app, stats))

    if not candidates:
        return

    print("\nPossible ignore candidates:")
    print("-" * 60)
    for app, stats in candidates:
        print(
            f"{app}: window_events={stats['window_events']}, "
            f"ui_events={stats['ui_events']}"
        )


def main():
    print("Linux Collector")
    print(f"Output: {OUTPUT_FILE.resolve()}")
    print("Press Ctrl+C to stop.\n")

    x11_collector = X11Collector()
    atspi_collector = AtspiCollector()

    x11_thread = threading.Thread(
        target=x11_collector.start,
        name="x11-collector",
        daemon=True,
    )

    def shutdown(*_):
        if stop_event.is_set():
            return
        print("\nStopping collector...")
        stop_event.set()
        atspi_collector.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    atspi_collector.register()
    x11_thread.start()

    try:
        Atspi.event_main()
    finally:
        stop_event.set()
        print_ignore_candidates()
        print(f"\nEvents written to {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
