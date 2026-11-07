"""Main window of YT Profile Manager (PySide6)."""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from . import config, crypto, drive, profiles

MAIN_PY = str(config.APP_DIR / "main.py")

STYLE = """
* { font-family: 'Segoe UI', 'Noto Sans Bengali', sans-serif; font-size: 10pt; }
QMainWindow, QDialog, QWidget { background: #16181d; color: #e6e6e6; }
QToolBar { background: #1d2027; border: none; padding: 6px; spacing: 6px; }
QToolBar QLabel, QToolBar QWidget#spacer { background: transparent; }
QPushButton { background: #2a2e37; border: 1px solid #353a45; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #343947; }
QPushButton:disabled { color: #6b7080; }
QPushButton#primary { background: #e0262e; border-color: #e0262e; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #f03a42; }
QPushButton#stop { background: #3a2a2d; border-color: #6b2f35; color: #ff9aa0; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { background: #0f1115; border: 1px solid #353a45;
    border-radius: 6px; padding: 5px; selection-background-color: #e0262e; }
QTableWidget { background: #111318; alternate-background-color: #15181e; gridline-color: #23262e;
    border: 1px solid #23262e; border-radius: 6px; }
QTableWidget::item:selected { background: #2b3242; }
QHeaderView::section { background: #1d2027; color: #9aa1b2; border: none; padding: 6px;
    font-weight: 600; }
QMenu { background: #1d2027; border: 1px solid #353a45; }
QMenu::item:selected { background: #2b3242; }
QLabel#muted { color: #8b92a3; }
QLabel#title { font-size: 18pt; font-weight: 700; }
"""


# ------------------------------------------------------------- background work

class _Signals(QObject):
    done = Signal(object)
    error = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            r = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            e._trace = traceback.format_exc()
            self.signals.error.emit(e)
        else:
            self.signals.done.emit(r)


_alive = set()


def run_bg(fn, *args, on_done=None, on_error=None, **kwargs):
    t = _Task(fn, args, kwargs)
    _alive.add(t)

    def _fin(_=None):
        _alive.discard(t)

    if on_done:
        t.signals.done.connect(on_done)
    if on_error:
        t.signals.error.connect(on_error)
    t.signals.done.connect(_fin)
    t.signals.error.connect(_fin)
    t.setAutoDelete(False)
    QThreadPool.globalInstance().start(t)


def show_error(parent, title, e):
    msg = str(e) or type(e).__name__
    box = QMessageBox(QMessageBox.Critical, title, msg, parent=parent)
    tr = getattr(e, "_trace", None)
    if tr:
        box.setDetailedText(tr)
    box.exec()


def fmt_time(iso):
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return iso


# ------------------------------------------------------------- dialogs

class PasswordDialog(QDialog):
    def __init__(self, email, create: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Master password")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        if create:
            txt = ("<b>Create a master password</b><br>All channel data is encrypted with this "
                   "password before it goes to Google Drive.<br><br>"
                   "<span style='color:#ff9aa0'>Use the SAME password on every office PC. "
                   "If you forget it, the data cannot be recovered.</span>")
        else:
            txt = f"<b>Enter master password</b><br>for {email}"
        lab = QLabel(txt)
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.p1 = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Master password")
        lay.addWidget(self.p1)
        self.p2 = None
        if create:
            self.p2 = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Type it again")
            lay.addWidget(self.p2)
        self.remember = QCheckBox("Remember on this PC")
        self.remember.setChecked(True)
        lay.addWidget(self.remember)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        if len(self.p1.text()) < 8:
            QMessageBox.warning(self, "Too short", "Password must be at least 8 characters.")
            return
        if self.p2 is not None and self.p1.text() != self.p2.text():
            QMessageBox.warning(self, "Mismatch", "The two passwords are not the same.")
            return
        self.accept()


class ProfileDialog(QDialog):
    def __init__(self, parent, groups, prof=None, settings=None):
        super().__init__(parent)
        self.prof = prof
        self.setWindowTitle("Edit profile" if prof else "New profile")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(prof.get("name", "") if prof else "")
        self.name.setPlaceholderText("e.g. DavidTalesUSA")
        self.group = QComboBox(editable=True)
        self.group.addItems([""] + groups)
        self.group.setCurrentText(prof.get("group", "") if prof else "")
        self.proxy = QLineEdit(prof.get("proxy", "") if prof else "")
        self.proxy.setPlaceholderText("host:port:user:pass  or  http://user:pass@host:port")
        prow = QHBoxLayout()
        prow.addWidget(self.proxy)
        chk = QPushButton("Check")
        chk.clicked.connect(self._check)
        prow.addWidget(chk)
        self.proxy_info = QLabel("")
        self.proxy_info.setObjectName("muted")
        self.start_url = QLineEdit(prof.get("start_url", "") if prof else "")
        self.start_url.setPlaceholderText((settings or {}).get("start_url", "https://studio.youtube.com"))
        self.notes = QPlainTextEdit(prof.get("notes", "") if prof else "")
        self.notes.setFixedHeight(70)
        form.addRow("Channel name", self.name)
        form.addRow("Group", self.group)
        form.addRow("Proxy", prow)
        form.addRow("", self.proxy_info)
        form.addRow("Start page", self.start_url)
        form.addRow("Notes", self.notes)
        if not prof:
            self.os = QComboBox()
            self.os.addItems(["windows", "macos"])
            self.os.setCurrentText((settings or {}).get("fingerprint_os", "windows"))
            form.addRow("Device type", self.os)
        else:
            fp = QLabel(profiles.fingerprint_summary(prof.get("fingerprint", {})))
            fp.setObjectName("muted")
            fp.setWordWrap(True)
            form.addRow("Fingerprint", fp)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _check(self):
        text = self.proxy.text()
        if not text.strip():
            self.proxy_info.setText("No proxy: your office internet IP will be used.")
            return
        try:
            profiles.parse_proxy(text)
        except ValueError as e:
            self.proxy_info.setText(f"❌ {e}")
            return
        self.proxy_info.setText("Checking...")
        run_bg(profiles.check_proxy, text,
               on_done=lambda r: self.proxy_info.setText(
                   f"✅ {r['ip']} — {r['city']}, {r['country']} ({r['timezone']})"),
               on_error=lambda e: self.proxy_info.setText(f"❌ Proxy not working: {e}"))

    def _ok(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Name", "Please give the profile a name.")
            return
        try:
            profiles.parse_proxy(self.proxy.text())
        except ValueError as e:
            QMessageBox.warning(self, "Proxy", str(e))
            return
        self.accept()

    def values(self):
        v = dict(name=self.name.text().strip(), group=self.group.currentText().strip(),
                 proxy=self.proxy.text().strip(), start_url=self.start_url.text().strip(),
                 notes=self.notes.toPlainText())
        if not self.prof:
            v["fp_os"] = self.os.currentText()
        return v


class BulkDialog(QDialog):
    def __init__(self, parent, groups):
        super().__init__(parent)
        self.setWindowTitle("Create many profiles")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.prefix = QLineEdit("Channel")
        self.count = QSpinBox(minimum=1, maximum=200, value=5)
        self.group = QComboBox(editable=True)
        self.group.addItems([""] + groups)
        self.proxies = QPlainTextEdit()
        self.proxies.setPlaceholderText("One proxy per line (optional).\nLine 1 → profile 1, line 2 → profile 2 ...")
        form.addRow("Name prefix", self.prefix)
        form.addRow("How many", self.count)
        form.addRow("Group", self.group)
        form.addRow("Proxies", self.proxies)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def proxy_list(self):
        return [l.strip() for l in self.proxies.toPlainText().splitlines() if l.strip()]

    def _ok(self):
        for i, p in enumerate(self.proxy_list(), 1):
            try:
                profiles.parse_proxy(p)
            except ValueError as e:
                QMessageBox.warning(self, "Proxy", f"Line {i}: {e}")
                return
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, parent, s):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.start_url = QLineEdit(s["start_url"])
        self.os = QComboBox()
        self.os.addItems(["windows", "macos"])
        self.os.setCurrentText(s["fingerprint_os"])
        self.w = QSpinBox(minimum=1280, maximum=3840, value=s["screen_max_width"])
        self.h = QSpinBox(minimum=720, maximum=2160, value=s["screen_max_height"])
        form.addRow("Default start page", self.start_url)
        form.addRow("Default device type", self.os)
        form.addRow("Max screen width", self.w)
        form.addRow("Max screen height", self.h)
        lay.addLayout(form)
        tip = QLabel("Screen size applies to NEW profiles. Set it to the smallest monitor in your office.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        return {"start_url": self.start_url.text().strip() or "https://studio.youtube.com",
                "fingerprint_os": self.os.currentText(),
                "screen_max_width": self.w.value(), "screen_max_height": self.h.value()}


# ------------------------------------------------------------- login screen

class LoginWidget(QWidget):
    def __init__(self, on_click):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch()
        t = QLabel(config.APP_NAME)
        t.setObjectName("title")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        sub = QLabel("Sign in with the office Gmail. All channel profiles are saved in that "
                     "account's Google Drive and appear on every PC that signs in.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        self.btn = QPushButton("  Sign in with Google  ")
        self.btn.setObjectName("primary")
        self.btn.setMinimumHeight(42)
        self.btn.clicked.connect(on_click)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.btn)
        row.addStretch()
        lay.addSpacing(20)
        lay.addLayout(row)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setAlignment(Qt.AlignCenter)
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        lay.addStretch()


# ------------------------------------------------------------- main window

COLS = ["Channel", "Group", "Status", "Proxy", "Last used", "Notes", ""]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.resize(1180, 680)
        self.settings = config.load_settings()
        self.creds = None
        self.email = ""
        self.service: profiles.ProfileService | None = None
        self.profiles: list[dict] = []
        self.remote_locks: dict = {}
        self.procs: dict[str, QProcess] = {}
        self.run_status: dict[str, str] = {}
        self._buffers: dict[str, str] = {}
        self._refreshing = False
        self._pending_lock: dict = {}
        self._pending_error: dict = {}

        self.login = LoginWidget(self.do_sign_in)
        self.setCentralWidget(self.login)
        QTimer.singleShot(100, self.startup)

    # ---------------- auth flow
    def startup(self):
        if not config.CLIENT_SECRET_FILE.exists():
            self.login.btn.setEnabled(False)
            self.login.info.setText(
                f"<span style='color:#ff9aa0'>client_secret.json not found.</span><br>"
                f"Put it in this folder:<br><b>{config.APP_DIR}</b><br>then restart the app "
                f"(see the guide, step 1).")
            return
        self.login.info.setText("Checking saved login...")
        run_bg(drive.load_credentials, on_done=self._got_creds,
               on_error=lambda e: self.login.info.setText(""))

    def do_sign_in(self):
        self.login.btn.setEnabled(False)
        self.login.info.setText("A browser window opened. Choose the office Gmail and allow access...")
        run_bg(drive.sign_in_interactive, on_done=self._got_creds, on_error=self._sign_in_failed)

    def _sign_in_failed(self, e):
        self.login.btn.setEnabled(True)
        self.login.info.setText("")
        show_error(self, "Sign-in failed", e)

    def _got_creds(self, creds):
        if not creds:
            self.login.info.setText("")
            self.login.btn.setEnabled(True)
            return
        self.creds = creds
        self.login.info.setText("Opening your vault...")

        def prep():
            store = drive.DriveStore(creds)
            return drive.get_email(creds), store, profiles.load_vault(store)

        run_bg(prep, on_done=self._got_vault, on_error=self._sign_in_failed)

    def _got_vault(self, res):
        email, store, vault = res
        self.email = email
        if vault is None:
            dlg = PasswordDialog(email, create=True, parent=self)
            if dlg.exec() != QDialog.Accepted:
                self.login.btn.setEnabled(True)
                self.login.info.setText("")
                return
            pw, remember = dlg.p1.text(), dlg.remember.isChecked()
            self.login.info.setText("Creating vault...")
            run_bg(profiles.create_vault, store, pw,
                   on_done=lambda key: self._unlocked(store, key, remember),
                   on_error=self._sign_in_failed)
            return
        key = crypto.recall_key(email)
        if key:
            try:
                crypto.check_key(vault, key)
                self._unlocked(store, key, False)
                return
            except Exception:
                crypto.forget_key(email)
        self._ask_password(store, vault)

    def _ask_password(self, store, vault):
        dlg = PasswordDialog(self.email, create=False, parent=self)
        if dlg.exec() != QDialog.Accepted:
            self.login.btn.setEnabled(True)
            self.login.info.setText("")
            return
        pw, remember = dlg.p1.text(), dlg.remember.isChecked()
        self.login.info.setText("Checking password...")

        def bad(e):
            QMessageBox.warning(self, "Wrong password", "That master password is not correct.")
            self._ask_password(store, vault)

        run_bg(crypto.unlock_vault, vault, pw,
               on_done=lambda key: self._unlocked(store, key, remember), on_error=bad)

    def _unlocked(self, store, key, remember):
        if remember:
            crypto.remember_key(self.email, key)
        self.service = profiles.ProfileService(store, key)
        self.key = key
        self.build_main_ui()
        self.refresh()
        self.ensure_browser()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_locks)
        self.timer.start(60_000)

    def ensure_browser(self):
        """First start on a PC: put the anti-detect browser in place."""
        from . import browser_setup
        try:
            if not browser_setup.needs_setup():
                return
        except Exception:
            return
        prog = QProgressDialog("Preparing the browser (first time on this PC only)...\n"
                               "This can take a few minutes.", None, 0, 0, self)
        prog.setWindowTitle("First-time setup")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        def done(_):
            prog.close()
            self.status_line.setText("Browser ready ✓")

        def fail(e):
            prog.close()
            show_error(self, "Browser setup failed", e)

        run_bg(browser_setup.setup, on_done=done, on_error=fail)

    # ---------------- main UI
    def build_main_ui(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        new_btn = QPushButton("+ New profile")
        new_btn.setObjectName("primary")
        new_btn.clicked.connect(self.new_profile)
        tb.addWidget(new_btn)
        for text, fn in (("Create many", self.bulk_create), ("⟳ Sync", self.refresh),
                         ("Settings", self.open_settings)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            tb.addWidget(b)
        spacer = QWidget()
        spacer.setObjectName("spacer")
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        acct = QLabel(f"{self.email}  •  {config.PC_NAME}  ")
        acct.setObjectName("muted")
        tb.addWidget(acct)
        out = QPushButton("Sign out")
        out.clicked.connect(self.sign_out)
        tb.addWidget(out)

        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search channel, proxy or notes...")
        self.search.textChanged.connect(self.fill_table)
        self.group_filter = QComboBox()
        self.group_filter.currentTextChanged.connect(self.fill_table)
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        top.addWidget(self.search, 3)
        top.addWidget(self.group_filter, 1)
        top.addWidget(self.count_label)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        h.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 110)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_menu)
        self.table.cellDoubleClicked.connect(lambda r, c: self.edit_profile(self._pid_at(r)))
        lay.addWidget(self.table)
        self.status_line = QLabel("")
        self.status_line.setObjectName("muted")
        lay.addWidget(self.status_line)
        self.setCentralWidget(w)

    def _pid_at(self, row):
        it = self.table.item(row, 0)
        return it.data(Qt.UserRole) if it else None

    def prof(self, pid):
        return next((p for p in self.profiles if p["id"] == pid), None)

    def groups(self):
        return sorted({p.get("group", "") for p in self.profiles if p.get("group")})

    # ---------------- data refresh
    def refresh(self):
        if self._refreshing or not self.service:
            return
        self._refreshing = True
        self.status_line.setText("Syncing with Google Drive...")

        def work():
            return self.service.sync_list(), self.service.all_locks()

        def done(res):
            self._refreshing = False
            self.profiles, self.remote_locks = res
            cur = self.group_filter.currentText()
            self.group_filter.blockSignals(True)
            self.group_filter.clear()
            self.group_filter.addItems(["All groups"] + self.groups())
            self.group_filter.setCurrentText(cur if cur else "All groups")
            self.group_filter.blockSignals(False)
            self.fill_table()
            self.status_line.setText(f"Synced at {datetime.now().strftime('%I:%M:%S %p')}")

        def fail(e):
            self._refreshing = False
            self.status_line.setText(f"Sync failed: {e}")

        run_bg(work, on_done=done, on_error=fail)

    def refresh_locks(self):
        if not self.service:
            return

        def done(locks):
            self.remote_locks = locks
            self.fill_table()

        run_bg(self.service.all_locks, on_done=done, on_error=lambda e: None)

    def status_of(self, pid):
        if pid in self.run_status:
            return self.run_status[pid], "#7ee787"
        if pid in self.remote_locks:
            return "In use on another PC", "#ffb86b"
        return "Ready", "#9aa1b2"

    def fill_table(self):
        if not hasattr(self, "table"):
            return
        q = self.search.text().lower().strip()
        g = self.group_filter.currentText()
        rows = [p for p in self.profiles
                if (g in ("", "All groups") or p.get("group", "") == g)
                and (not q or q in (p.get("name", "") + " " + p.get("proxy", "") + " "
                                    + p.get("notes", "")).lower())]
        for r in range(self.table.rowCount()):
            self.table.removeCellWidget(r, 6)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            pid = p["id"]
            name = QTableWidgetItem(p.get("name", ""))
            name.setData(Qt.UserRole, pid)
            f = QFont()
            f.setBold(True)
            name.setFont(f)
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(p.get("group", "")))
            st, color = self.status_of(pid)
            si = QTableWidgetItem(st)
            si.setForeground(QColor(color))
            self.table.setItem(r, 2, si)
            proxy = p.get("proxy", "")
            try:
                pd = profiles.parse_proxy(proxy)
                proxy_txt = pd["server"].split("://", 1)[1] if pd else "No proxy"
            except ValueError:
                proxy_txt = "Invalid"
            self.table.setItem(r, 3, QTableWidgetItem(proxy_txt))
            lu = fmt_time(p.get("last_used_at"))
            if p.get("last_used_pc"):
                lu += f"  ({p['last_used_pc']})"
            self.table.setItem(r, 4, QTableWidgetItem(lu))
            self.table.setItem(r, 5, QTableWidgetItem(p.get("notes", "").replace("\n", " ")))
            running = pid in self.procs
            btn = QPushButton("Stop" if running else "▶ Open")
            btn.setObjectName("stop" if running else "primary")
            if running:
                btn.clicked.connect(lambda _=False, x=pid: self.stop_profile(x))
            else:
                btn.clicked.connect(lambda _=False, x=pid: self.open_profile(x))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 4, 6, 4)
            cl.addWidget(btn)
            self.table.setCellWidget(r, 6, cell)
        self.count_label.setText(f"{len(rows)} / {len(self.profiles)} profiles")

    # ---------------- row menu
    def row_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        pid = self._pid_at(row)
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_profile(pid))
        m.addAction("Edit", lambda: self.edit_profile(pid))
        m.addAction("Check proxy", lambda: self.check_proxy_of(pid))
        m.addSeparator()
        m.addAction("Unlock (if stuck as 'in use')", lambda: self.force_unlock(pid))
        m.addAction("New fingerprint...", lambda: self.new_fingerprint(pid))
        m.addAction("Copy profile ID", lambda: QGuiApplication.clipboard().setText(pid))
        m.addSeparator()
        m.addAction("Delete", lambda: self.delete_profile(pid))
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ---------------- actions
    def new_profile(self):
        dlg = ProfileDialog(self, self.groups(), settings=self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        self.status_line.setText("Creating profile and fingerprint...")
        run_bg(self.service.create, **v, max_w=self.settings["screen_max_width"],
               max_h=self.settings["screen_max_height"],
               on_done=lambda p: self.refresh(),
               on_error=lambda e: show_error(self, "Could not create profile", e))

    def bulk_create(self):
        dlg = BulkDialog(self, self.groups())
        if dlg.exec() != QDialog.Accepted:
            return
        n, prefix, group, proxies = dlg.count.value(), dlg.prefix.text().strip() or "Channel", \
            dlg.group.currentText().strip(), dlg.proxy_list()
        existing = {p["name"] for p in self.profiles}
        names, i = [], 1
        while len(names) < n:
            nm = f"{prefix} {i}"
            if nm not in existing:
                names.append(nm)
            i += 1
        prog = QProgressDialog("Creating profiles...", None, 0, n, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.show()
        s = self.settings

        class Prog(QObject):
            step = Signal(int)

        sig = Prog()
        sig.step.connect(prog.setValue)

        def work():
            for k, nm in enumerate(names):
                self.service.create(nm, group=group, proxy=proxies[k] if k < len(proxies) else "",
                                    fp_os=s["fingerprint_os"], max_w=s["screen_max_width"],
                                    max_h=s["screen_max_height"])
                sig.step.emit(k + 1)

        def fin(_=None):
            prog.close()
            self.refresh()

        run_bg(work, on_done=fin,
               on_error=lambda e: (fin(), show_error(self, "Bulk create stopped", e)))

    def edit_profile(self, pid):
        p = self.prof(pid)
        if not p:
            return
        local = self.service.local_profile(pid) or p
        dlg = ProfileDialog(self, self.groups(), prof=local, settings=self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        run_bg(self.service.update_settings, pid, **dlg.values(),
               on_done=lambda _: self.refresh(),
               on_error=lambda e: show_error(self, "Could not save", e))

    def check_proxy_of(self, pid):
        p = self.prof(pid)
        if not p.get("proxy"):
            QMessageBox.information(self, "Proxy", "This profile has no proxy.")
            return
        self.status_line.setText(f"Checking proxy of {p['name']}...")
        run_bg(profiles.check_proxy, p["proxy"],
               on_done=lambda r: QMessageBox.information(
                   self, "Proxy OK", f"{p['name']}\n\nIP: {r['ip']}\n{r['city']}, {r['country']}\n"
                                     f"Timezone: {r['timezone']}"),
               on_error=lambda e: QMessageBox.warning(self, "Proxy not working", f"{p['name']}\n\n{e}"))

    def force_unlock(self, pid):
        if pid in self.procs:
            return
        if QMessageBox.question(
                self, "Unlock",
                "Only do this if the other PC is OFF or crashed.\n\nIf the profile is really open "
                "on another PC, unlocking can lose that PC's latest changes. Continue?") \
                != QMessageBox.Yes:
            return
        run_bg(self.service.force_unlock, pid, on_done=lambda _: self.refresh_locks())

    def new_fingerprint(self, pid):
        p = self.prof(pid)
        if QMessageBox.warning(
                self, "New fingerprint",
                f"Give '{p['name']}' a completely new device fingerprint?\n\nYouTube will see this "
                f"channel on a NEW device. Only do this if you have a reason.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        run_bg(self.service.regenerate_fingerprint, pid, self.settings["screen_max_width"],
               self.settings["screen_max_height"], on_done=lambda _: self.refresh(),
               on_error=lambda e: show_error(self, "Failed", e))

    def delete_profile(self, pid):
        p = self.prof(pid)
        if pid in self.procs:
            QMessageBox.warning(self, "Delete", "Close the browser first.")
            return
        text, ok = QInputDialog.getText(
            self, "Delete profile",
            f"This deletes '{p['name']}' from Google Drive and ALL PCs (login, cookies, "
            f"fingerprint).\nThe YouTube channel itself is NOT deleted.\n\nType DELETE to confirm:")
        if not ok or text.strip() != "DELETE":
            return
        run_bg(self.service.delete, pid, on_done=lambda _: self.refresh(),
               on_error=lambda e: show_error(self, "Delete failed", e))

    def open_settings(self):
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() == QDialog.Accepted:
            self.settings.update(dlg.values())
            config.save_settings(self.settings)

    def sign_out(self):
        if self.procs:
            QMessageBox.warning(self, "Sign out", "Close all open browsers first.")
            return
        if QMessageBox.question(self, "Sign out", "Sign out from Google on this PC?") != QMessageBox.Yes:
            return
        crypto.forget_key(self.email)
        drive.sign_out()
        QMessageBox.information(self, "Signed out", "Signed out. The app will now close.")
        QApplication.quit()

    # ---------------- running profiles
    def open_profile(self, pid, force=False):
        if pid in self.procs:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        base = [] if config.FROZEN else [MAIN_PY]
        args = base + ["--run", pid] + (["--force"] if force else [])
        proc.setArguments(args)
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_output(pid, proc))
        proc.finished.connect(lambda code, _st: self._on_finished(pid, code))
        self.procs[pid] = proc
        self._buffers[pid] = ""
        self.run_status[pid] = "Starting..."
        proc.start()
        proc.write((self.key.hex() + "\n").encode())
        self.fill_table()

    def stop_profile(self, pid):
        proc = self.procs.get(pid)
        if proc:
            proc.write(b"STOP\n")
            self.run_status[pid] = "Closing..."
            self.fill_table()

    def _on_output(self, pid, proc):
        self._buffers[pid] += bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self._buffers[pid]:
            line, self._buffers[pid] = self._buffers[pid].split("\n", 1)
            try:
                ev = json.loads(line)
            except Exception:
                continue
            kind, msg = ev.get("kind"), ev.get("msg", "")
            name = (self.prof(pid) or {}).get("name", pid)
            if kind in ("status", "running"):
                self.run_status[pid] = msg if kind == "status" else "● Open"
            elif kind == "locked":
                self._pending_lock[pid] = ev.get("pc", "?")
            elif kind == "error":
                self._pending_error[pid] = (msg, ev.get("trace", ""))
            elif kind == "warn":
                self.status_line.setText(f"{name}: {msg}")
            elif kind == "done":
                self.status_line.setText(f"{name}: saved to Google Drive ✓")
            self.fill_table()

    def _on_finished(self, pid, code):
        self._on_output(pid, self.procs[pid])
        self.procs.pop(pid, None)
        self.run_status.pop(pid, None)
        self.refresh()
        name = (self.prof(pid) or {}).get("name", pid)
        lock = self._pending_lock.pop(pid, None)
        err = self._pending_error.pop(pid, None)
        if lock:
            r = QMessageBox.question(
                self, "Profile in use",
                f"'{name}' is open on PC: {lock}\n\nClose it there first.\n\n"
                f"Open here anyway? (Only if that PC is OFF — otherwise data can be lost)")
            if r == QMessageBox.Yes:
                self.open_profile(pid, force=True)
        elif err:
            box = QMessageBox(QMessageBox.Critical, f"{name}", err[0], parent=self)
            if err[1]:
                box.setDetailedText(err[1])
            box.exec()

    def closeEvent(self, ev):
        if self.procs:
            r = QMessageBox.question(
                self, "Browsers still open",
                f"{len(self.procs)} profile(s) are open. Close them, save to Google Drive and exit?")
            if r != QMessageBox.Yes:
                ev.ignore()
                return
            for pid in list(self.procs):
                self.stop_profile(pid)
            ev.ignore()
            self.status_line.setText("Saving all profiles, the app will close when finished...")
            self._exit_when_done()
            return
        ev.accept()

    def _exit_when_done(self):
        if self.procs:
            QTimer.singleShot(1000, self._exit_when_done)
        else:
            QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyleSheet(STYLE)
    icon = Path(getattr(sys, "_MEIPASS", config.APP_DIR)) / "assets" / "icon.ico"
    if icon.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
