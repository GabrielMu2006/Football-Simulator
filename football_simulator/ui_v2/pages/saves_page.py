"""存档管理页（阶段 5 重写，实施方案 §8.8）。

Route：``Route("saves")``（无参数）。

写流程（全部经 ``context.service``，每个调用 try/except；``service`` 为
None 时只读展示、全部写按钮禁用）：

- 新建存档：输入框 + “新建”按钮 → ``service.create_save``；
  ``normalize_save_name`` 的 ``ValueError``（含合法字符集规则说明）在行内
  状态条提示，不弹窗打断；成功后刷新列表并
  ``context.request_save_reload(新存档名)`` 让外壳切换到新存档；
- 打开存档：行内“打开”按钮 → ``context.request_save_reload(存档名)``，
  由外壳走 Router 的存档切换流程（旧页的 ``_replace_save_state`` 回调不再
  使用）；
- 删除存档：行内“删除”按钮 → ``QMessageBox`` Yes/No 二次确认（明确列出
  存档名）→ ``service.delete_save`` → 刷新列表 + 状态条 →
  ``request_save_reload(service.current_save_name())`` 让外壳重载（删除
  当前存档时 ``load_current_save_name`` 自动回退到剩余存档）；
- 初始化赛季：未初始化存档（目录里还没有 ``save.sqlite3``）的行内
  “初始化赛季”按钮 → ``service.initialize`` → ``request_save_reload``。

页面信息（不虚构、不迁移旧档）：
- 存档目录：``service.save_directory()``；
- 新架构说明：SQLite——每个存档一个 ``save.sqlite3``；旧版 ``state.json``
  存档不兼容、不迁移（§9.3）；
- 当前存档标记：``service.current_save_name()``。

滚动面归属（§8.2）：内容型页面——单个外层 ``QScrollArea`` 是唯一纵向滚动
面；存档列表逐行完整展开，不出现内部小滚动区。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base
from football_simulator.ui_v2.components import debug_log
from football_simulator.ui_v2.components import (
    BG_COLOR_CARD,
    BORDER_COLOR_SOFT,
    DANGER_COLOR,
    LINK_COLOR,
    PageHeader,
    TEXT_COLOR,
    TEXT_COLOR_BRIGHT,
    TEXT_COLOR_MUTED,
)
from football_simulator.ui_v2.design_tokens import SUCCESS_HIGHLIGHT
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext
from football_simulator.ui_v2.widgets import section_header

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = f"color: {TEXT_COLOR_BRIGHT}; background: transparent; font-weight: 700;"
_ACCENT_STYLE = f"color: {LINK_COLOR}; background: transparent; font-weight: 700;"
_ERROR_STYLE = f"color: {DANGER_COLOR}; background: transparent; font-weight: 600;"
_OK_STYLE = f"color: {SUCCESS_HIGHLIGHT}; background: transparent; font-weight: 600;"


class SavesPage(EntityPageBase):
    """存档管理：新建 / 打开 / 删除存档与初始化赛季（外壳经 request_save_reload 切换）。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._status_message: str = ""
        self._status_is_error: bool = False
        self._rows_container: Optional[QWidget] = None
        self._rows_layout: Optional[QVBoxLayout] = None
        self._save_rows: Dict[str, Dict[str, QWidget]] = {}
        self._create_input: Optional[QLineEdit] = None
        self._create_status: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None
        self._saves_frame: Optional[QFrame] = None
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；列表在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # 唯一外层纵向滚动面（内容型页面，全部内容完整展开）。
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("savesScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(self._scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        self._scroll.setWidget(content)
        root.addWidget(self._scroll, 1)
        self._content = content
        self._content_layout = content_layout

        content_layout.addWidget(PageHeader("存档管理", [], parent=content))

        # 说明卡：存档目录 + SQLite 新架构说明（旧 state.json 不兼容、不迁移）。
        info_frame = QFrame(content)
        info_frame.setObjectName("cardFrame")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(6)
        service = self._context.service
        directory = service.save_directory() if service is not None else "（当前未启用写服务）"
        info_layout.addWidget(section_header("存档目录与新架构", None))
        directory_label = QLabel(f"存档目录：{directory}")
        directory_label.setObjectName("savesDirectoryLabel")
        directory_label.setWordWrap(True)
        info_layout.addWidget(directory_label)
        architecture_label = QLabel(
            "存档采用 SQLite 新架构：每个存档目录包含一个 save.sqlite3 数据库，"
            "周推进与赛季结算在单事务内完成。旧版 state.json 存档不兼容，也不会迁移。"
        )
        architecture_label.setObjectName("savesArchitectureNote")
        architecture_label.setWordWrap(True)
        architecture_label.setStyleSheet(_MUTED_STYLE)
        info_layout.addWidget(architecture_label)
        if service is None:
            readonly_note = QLabel(
                "当前未启用写服务：存档列表与全部写操作（新建/打开/删除/初始化）不可用，页面为只读展示。"
            )
            readonly_note.setObjectName("savesReadonlyNote")
            readonly_note.setWordWrap(True)
            readonly_note.setStyleSheet(_ERROR_STYLE)
            info_layout.addWidget(readonly_note)
        content_layout.addWidget(info_frame)

        # 新建存档卡。
        create_frame = QFrame(content)
        create_frame.setObjectName("cardFrame")
        create_layout = QVBoxLayout(create_frame)
        create_layout.setContentsMargins(12, 10, 12, 10)
        create_layout.setSpacing(8)
        create_layout.addWidget(section_header("新建存档", "存档名只能由中文、字母或数字开头，并仅包含中文、字母、数字、空格、下划线和连字符（1–64 字符）。"))
        create_row = QWidget(create_frame)
        create_row_layout = QHBoxLayout(create_row)
        create_row_layout.setContentsMargins(0, 0, 0, 0)
        create_row_layout.setSpacing(10)
        self._create_input = QLineEdit(create_row)
        self._create_input.setObjectName("savesCreateInput")
        self._create_input.setPlaceholderText("输入新存档名")
        self._create_input.returnPressed.connect(self._on_create_clicked)
        create_button = QPushButton("＋ 新建", create_row)
        create_button.setObjectName("savesCreateButton")
        create_button.setEnabled(service is not None)
        create_button.clicked.connect(self._on_create_clicked)
        self._create_button = create_button
        create_row_layout.addWidget(self._create_input, 1)
        create_row_layout.addWidget(create_button)
        import_button = QPushButton("⇧ 导入存档", create_row)
        import_button.setObjectName("savesImportButton")
        import_button.setEnabled(service is not None)
        import_button.clicked.connect(self._on_import_clicked)
        create_row_layout.addWidget(import_button)
        open_dir_button = QPushButton("📂 打开目录", create_row)
        open_dir_button.setObjectName("savesOpenDirButton")
        open_dir_button.setEnabled(service is not None)
        open_dir_button.clicked.connect(self._on_open_dir_clicked)
        create_row_layout.addWidget(open_dir_button)
        create_layout.addWidget(create_row)
        self._create_status = QLabel("", create_frame)
        self._create_status.setObjectName("savesCreateStatus")
        self._create_status.setWordWrap(True)
        create_layout.addWidget(self._create_status)
        content_layout.addWidget(create_frame)

        # 存档列表卡（行完整展开，无内部滚动）。
        self._saves_frame = QFrame(content)
        self._saves_frame.setObjectName("cardFrame")
        saves_layout = QVBoxLayout(self._saves_frame)
        saves_layout.setContentsMargins(12, 10, 12, 10)
        saves_layout.setSpacing(8)
        saves_layout.addWidget(section_header("存档列表", "“打开”切换到该存档；删除前会二次确认；未初始化的存档可先初始化赛季。"))
        self._rows_container = QWidget(self._saves_frame)
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)
        saves_layout.addWidget(self._rows_container)
        content_layout.addWidget(self._saves_frame)

        # 回收站卡：删除的存档移到这里，可恢复。
        self._trash_frame = QFrame(content)
        self._trash_frame.setObjectName("cardFrame")
        trash_layout = QVBoxLayout(self._trash_frame)
        trash_layout.setContentsMargins(12, 10, 12, 10)
        trash_layout.setSpacing(8)
        trash_header_row = QHBoxLayout()
        trash_header_row.setContentsMargins(0, 0, 0, 0)
        trash_header_row.setSpacing(8)
        trash_header_row.addWidget(
            section_header("回收站", "删除的存档移到这里，可一键恢复；确认不再需要后清空。"),
            1,
        )
        self._empty_trash_button = QPushButton("清空回收站", self._trash_frame)
        self._empty_trash_button.setObjectName("savesEmptyTrashButton")
        self._empty_trash_button.setEnabled(service is not None)
        self._empty_trash_button.clicked.connect(self._on_empty_trash_clicked)
        trash_header_row.addWidget(self._empty_trash_button)
        trash_layout.addLayout(trash_header_row)
        self._trash_layout = QVBoxLayout()
        self._trash_layout.setContentsMargins(0, 0, 0, 0)
        self._trash_layout.setSpacing(8)
        trash_layout.addLayout(self._trash_layout)
        content_layout.addWidget(self._trash_frame)

        # 行内状态条（写流程反馈）。
        self._status_label = QLabel("", content)
        self._status_label.setObjectName("savesStatusLabel")
        self._status_label.setWordWrap(True)
        content_layout.addWidget(self._status_label)
        content_layout.addStretch(1)

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "saves":
            return
        self._rebuild_rows()
        self._rebuild_trash()
        self._apply_status_label()

    # -- 存档列表 -------------------------------------------------------------

    def _list_saves(self) -> List[str]:
        service = self._context.service
        if service is None:
            return []
        try:
            return list(service.available_saves())
        except Exception as exc:  # 枚举失败时给出明确文案，不空白
            self._set_status(f"读取存档列表失败：{exc}", is_error=True)
            return []

    def _is_initialized(self, save_name: str) -> bool:
        """已初始化 = 存档目录中存在 save.sqlite3（仅建档的存档为未初始化）。"""

        try:
            return base.database_path(save_name).exists()
        except Exception:
            return False

    def _rebuild_rows(self) -> None:
        debug_log.log("saves._rebuild_rows begin")
        assert self._rows_layout is not None
        service = self._context.service
        current = None
        if service is not None:
            try:
                current = service.current_save_name()
            except Exception:
                current = None
        saves = self._list_saves()

        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 不要 setParent(None)：那会把小组件临时变成顶层窗口，
                # macOS 全屏下创建顶层窗口会触发 Space 滑动并退出全屏。
                widget.deleteLater()
        self._save_rows = {}

        if service is None:
            note = QLabel("当前未启用写服务，无法枚举存档。")
            note.setObjectName("savesNoServiceNote")
            note.setStyleSheet(_MUTED_STYLE)
            self._rows_layout.addWidget(note)
            return
        if not saves:
            note = QLabel("存档目录是空的。输入存档名并点击“新建”开始第一个存档。")
            note.setObjectName("savesEmptyNote")
            note.setStyleSheet(_MUTED_STYLE)
            self._rows_layout.addWidget(note)
            debug_log.log("saves._rebuild_rows end (empty)")
            return

        for save_name in saves:
            self._rows_layout.addWidget(self._build_save_row(save_name, save_name == current))
        debug_log.log(f"saves._rebuild_rows end count={len(saves)}")

    def _build_save_row(self, save_name: str, is_current: bool) -> QWidget:
        service = self._context.service
        initialized = self._is_initialized(save_name)

        row = QFrame(self._rows_container)
        row.setObjectName("savesRowFrame")
        row.setStyleSheet(
            "QFrame#savesRowFrame { background: " + BG_COLOR_CARD
            + "; border: 1px solid " + BORDER_COLOR_SOFT + "; border-radius: 9px; }"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 12, 8)
        row_layout.setSpacing(10)

        name_label = QLabel(save_name + ("　（当前存档）" if is_current else ""))
        name_label.setObjectName("savesRowNameLabel")
        name_label.setStyleSheet(_BRIGHT_STYLE if is_current else f"color: {TEXT_COLOR}; background: transparent; font-weight: 600;")
        name_label.setToolTip(save_name)
        row_layout.addWidget(name_label)

        state_label = QLabel("已初始化（save.sqlite3）" if initialized else "未初始化（还没有 save.sqlite3）")
        state_label.setObjectName("savesRowStateLabel")
        state_label.setStyleSheet(_ACCENT_STYLE if initialized else _MUTED_STYLE)
        row_layout.addWidget(state_label)
        row_layout.addStretch(1)

        open_button = QPushButton("▶ 打开", row)
        open_button.setObjectName("savesOpenButton")
        open_button.setEnabled(service is not None)
        open_button.clicked.connect(lambda _=False, name=save_name: self._open_save(name))
        row_layout.addWidget(open_button)

        initialize_button = QPushButton("⚙ 初始化赛季", row)
        initialize_button.setObjectName("savesInitializeButton")
        initialize_button.setEnabled(service is not None and not initialized)
        initialize_button.setVisible(not initialized)
        initialize_button.clicked.connect(lambda _=False, name=save_name: self._initialize_save(name))
        row_layout.addWidget(initialize_button)

        backup_button = QPushButton("⤓ 备份", row)
        backup_button.setObjectName("savesBackupButton")
        backup_button.setEnabled(service is not None and initialized)
        backup_button.setToolTip("用 SQLite 在线备份为独立 .sqlite3（WAL 安全）")
        backup_button.clicked.connect(lambda _=False, name=save_name: self._on_backup_clicked(name))
        row_layout.addWidget(backup_button)

        export_button = QPushButton("⇩ 导出", row)
        export_button.setObjectName("savesExportButton")
        export_button.setEnabled(service is not None and initialized)
        export_button.setToolTip("把存档数据库导出到本地其它位置")
        export_button.clicked.connect(lambda _=False, name=save_name: self._on_export_clicked(name))
        row_layout.addWidget(export_button)

        delete_button = QPushButton("🗑 移入回收站", row)
        delete_button.setObjectName("savesDeleteButton")
        delete_button.setEnabled(service is not None)
        delete_button.setToolTip("移入回收站，可恢复，不会立即丢失")
        delete_button.clicked.connect(lambda _=False, name=save_name: self._delete_save(name))
        row_layout.addWidget(delete_button)

        self._save_rows[save_name] = {
            "row": row,
            "name": name_label,
            "state": state_label,
            "open": open_button,
            "initialize": initialize_button,
            "backup": backup_button,
            "export": export_button,
            "delete": delete_button,
        }
        return row

    # -- 写流程 ----------------------------------------------------------------

    def _request_reload(self, save_name: str) -> None:
        debug_log.log(f"saves._request_reload save={save_name!r}")
        reload_hook: Optional[Callable[[str], None]] = self._context.request_save_reload
        if reload_hook is not None:
            try:
                reload_hook(save_name)
            except Exception as exc:
                debug_log.log(f"saves._request_reload failed: {exc}")
                self._set_status(f"切换存档失败：{exc}", is_error=True)

    def _rebuild_trash(self) -> None:
        debug_log.log("saves._rebuild_trash begin")
        assert self._trash_layout is not None
        service = self._context.service
        while self._trash_layout.count():
            item = self._trash_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 同上：避免创建临时顶层窗口触发全屏退场。
                widget.deleteLater()
        trash_paths = service.list_trash() if service is not None else []
        self._trash_frame.setVisible(bool(trash_paths))
        for trash_path in trash_paths:
            row = QWidget(self._trash_frame)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            label = QLabel(str(trash_path))
            label.setObjectName("savesTrashLabel")
            label.setStyleSheet(_MUTED_STYLE)
            label.setToolTip(str(trash_path))
            row_layout.addWidget(label, 1)
            restore_button = QPushButton("恢复", row)
            restore_button.setObjectName("savesRestoreButton")
            restore_button.clicked.connect(
                lambda _=False, p=trash_path, name=Path(trash_path).name: self._on_restore_clicked(p, name)
            )
            row_layout.addWidget(restore_button)
            self._trash_layout.addWidget(row)
        debug_log.log(f"saves._rebuild_trash end count={len(trash_paths)}")

    def _on_backup_clicked(self, save_name: str) -> None:
        service = self._context.service
        if service is None:
            return
        try:
            backup_path = service.backup_save(save_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"备份存档失败：{exc}")
            return
        self._set_status(f"已备份存档 {save_name}：{backup_path}", is_error=False)

    def _on_export_clicked(self, save_name: str) -> None:
        service = self._context.service
        if service is None:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出存档", f"{save_name}.sqlite3", "SQLite (*.sqlite3 *.db)"
        )
        if not dest:
            return
        try:
            exported = service.export_save(save_name, dest)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"导出存档失败：{exc}")
            return
        self._set_status(f"已导出存档 {save_name}：{exported}", is_error=False)

    def _on_import_clicked(self) -> None:
        service = self._context.service
        if service is None:
            return
        src, _ = QFileDialog.getOpenFileName(
            self, "选择存档数据库", "", "SQLite (*.sqlite3 *.db)"
        )
        if not src:
            return
        save_name, ok = QInputDialog.getText(self, "导入存档", "为新存档命名：")
        if not ok or not save_name.strip():
            return
        save_name = save_name.strip()
        try:
            imported = service.import_save(save_name, src)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"导入存档失败：{exc}")
            return
        self._set_status(f"已导入存档 {save_name}：{imported}", is_error=False)
        self._rebuild_rows()
        self._request_reload(save_name)

    def _on_open_dir_clicked(self) -> None:
        service = self._context.service
        if service is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(service.save_directory()))

    def _on_restore_clicked(self, trash_path: str, target_name: str) -> None:
        service = self._context.service
        if service is None:
            return
        try:
            restored = service.restore_trash(trash_path, target_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"恢复存档失败：{exc}")
            return
        self._set_status(f"已从回收站恢复：{restored}", is_error=False)
        self._rebuild_rows()
        self._rebuild_trash()

    def _on_empty_trash_clicked(self) -> None:
        debug_log.log("saves._on_empty_trash_clicked begin")
        service = self._context.service
        if service is None:
            return
        answer = QMessageBox.question(
            self,
            "清空回收站",
            "确定要清空回收站吗？已移入回收站的存档将被永久删除，不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            service.empty_trash()
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"清空回收站失败：{exc}")
            return
        self._set_status("回收站已清空。", is_error=False)
        self._rebuild_trash()
        debug_log.log("saves._on_empty_trash_clicked end")

    def _on_create_clicked(self) -> None:
        assert self._create_input is not None and self._create_status is not None
        service = self._context.service
        if service is None:
            return
        save_name = self._create_input.text().strip()
        if not save_name:
            self._set_create_status("请输入新存档名。", is_error=True)
            return
        try:
            state = service.create_save(save_name)
        except ValueError as exc:
            # normalize_save_name 的合法性规则（字符集/保留名等）行内提示。
            self._set_create_status(f"存档名不合法：{exc}", is_error=True)
            return
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"创建存档失败：{exc}")
            return
        created = state.save_name
        self._create_input.clear()
        self._set_create_status(f"已创建存档 {created}。", is_error=False)
        self._set_status(f"已创建存档 {created}，正在切换到该存档。", is_error=False)
        self._rebuild_rows()
        self._request_reload(created)

    def _open_save(self, save_name: str) -> None:
        debug_log.log(f"saves._open_save save={save_name!r}")
        self._set_status(f"正在打开存档 {save_name}。", is_error=False)
        self._request_reload(save_name)

    def _initialize_save(self, save_name: str) -> None:
        debug_log.log(f"saves._initialize_save begin save={save_name!r}")
        service = self._context.service
        if service is None:
            return
        try:
            state = service.initialize(save_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"初始化存档 {save_name} 失败：{exc}")
            return
        self._set_status(f"已初始化存档 {state.save_name} 的第 1 赛季，正在切换到该存档。", is_error=False)
        self._rebuild_rows()
        self._request_reload(state.save_name)
        debug_log.log(f"saves._initialize_save end save={state.save_name!r}")

    def _delete_save(self, save_name: str) -> None:
        debug_log.log(f"saves._delete_save begin save={save_name!r}")
        service = self._context.service
        if service is None:
            return
        answer = QMessageBox.question(
            self,
            "Football Simulator UI v2",
            f"确定要删除存档“{save_name}”吗？该操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            debug_log.log(f"saves._delete_save cancelled save={save_name!r}")
            self._set_status(f"已取消删除存档 {save_name}。", is_error=False)
            return
        debug_log.log(f"saves._delete_save confirmed save={save_name!r}")
        try:
            service.delete_save(save_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"删除存档 {save_name} 失败：{exc}")
            return
        current = None
        try:
            current = service.current_save_name()
        except Exception:
            current = None
        self._set_status(f"已删除存档 {save_name}。", is_error=False)
        self._rebuild_rows()
        # 让外壳重载：删除当前存档时外壳按 current_save_name 的回退结果切换。
        if current:
            self._request_reload(current)
        debug_log.log(f"saves._delete_save end save={save_name!r} current={current!r}")

    # -- 状态条 ----------------------------------------------------------------

    def _set_status(self, message: str, is_error: bool) -> None:
        self._status_message = message
        self._status_is_error = is_error
        self._apply_status_label()

    def _set_create_status(self, message: str, is_error: bool) -> None:
        assert self._create_status is not None
        self._create_status.setStyleSheet(_ERROR_STYLE if is_error else _OK_STYLE)
        self._create_status.setText(message)

    def _apply_status_label(self) -> None:
        assert self._status_label is not None
        self._status_label.setStyleSheet(_ERROR_STYLE if self._status_is_error else _OK_STYLE)
        self._status_label.setText(self._status_message)
