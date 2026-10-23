"""Football Simulator 正式入口（PySide6）。

只使用新存档格式。测试/截图可通过 FS_DATA_ROOT 环境变量把整个数据根
（saves/、backups/、trash/、current_save.txt 与配置）重定向到独立目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _page_scroll_area(page) -> Optional[object]:
    """页面级纵向滚动面（没有则返回 None）。

    ``QWidget`` 自带 ``scroll(dx, dy)`` 方法，所以不能用 ``getattr(page, "scroll")``
    判断页面是否有滚动面：表格型页面（比赛/球队/球员）会拿到那个内建方法。
    只有真正的 ``QAbstractScrollArea`` 才算页面主滚动面，避免把表格的横向滚动
    误报成主内容水平溢出。
    """
    from PySide6.QtWidgets import QAbstractScrollArea

    candidate = getattr(page, "scroll", None)
    if isinstance(candidate, QAbstractScrollArea):
        return candidate
    return None


def main() -> int:
    data_root = os.environ.get("FS_DATA_ROOT")
    if data_root:
        from football_simulator import runtime

        runtime.set_data_root_override(Path(data_root))
    else:
        # 源码运行时把总配置作为只读来源；不复制、不改写用户文件。
        from football_simulator import runtime

        config = runtime.shared_config_path()
        if config.exists():
            runtime.set_config_path_override(config)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from football_simulator.app.service import GameService
    from football_simulator.ui.main_window import MainWindow
    from football_simulator.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Football Simulator")
    from football_simulator.version import APP_VERSION
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(runtime.resource_root() / "assets" / "app_icon_256.png")))
    apply_theme(app)
    from football_simulator.game import rules
    window = MainWindow(GameService(honor_profile=rules.HONOR_PROFILE if sys.platform == "darwin" else None, edition_archives=sys.platform == "darwin"))
    smoke_size = os.environ.get("FS_SMOKE_SIZE")
    if smoke_size and "x" in smoke_size:
        try:
            width, height = (int(part) for part in smoke_size.lower().split("x", 1))
            window.setFixedSize(width, height)
        except ValueError:
            pass
    window.show()
    # 打包/前台冒烟钩子（仅环境变量触发；不影响正常启动）。
    smoke_route = os.environ.get("FS_SMOKE_ROUTE")
    if smoke_route:
        if smoke_route.startswith("competition:"):
            competition_id = smoke_route.split(":", 1)[1]
            window.navigate("competition", competition_id=competition_id, **({"season": int(os.environ["FS_SMOKE_SEASON"])} if os.environ.get("FS_SMOKE_SEASON") else {}))
            if sys.platform == "darwin" and os.environ.get("FS_SMOKE_SECTION"):
                window.stack.currentWidget().set_detail_section(f"{competition_id}:{window.world()['season_number']}", os.environ["FS_SMOKE_SECTION"])
            if sys.platform == "darwin" and os.environ.get("FS_SMOKE_BRACKET_VIEW") in ("rounds", "panorama"):
                window.stack.currentWidget().detail_state(competition_id)["bracket_view"] = os.environ["FS_SMOKE_BRACKET_VIEW"]
                window._refresh_current_page(restore_scroll=True)
            if sys.platform == "darwin" and competition_id == "starpath" and os.environ.get("FS_SMOKE_SWISS_VIEW") == "graph":
                window.stack.currentWidget().detail_state(f"{competition_id}:{window.world()['season_number']}")["swiss_view"] = "graph"
                window._refresh_current_page(restore_scroll=True)
        elif smoke_route.startswith("match:"):
            window.navigate("match", match_id=smoke_route.split(":", 1)[1])
        elif sys.platform == "darwin" and smoke_route.startswith(("team:", "player:")):
            kind, entity_id = smoke_route.split(":", 1)
            window.navigate(kind, **{kind + "_id": entity_id})
            section = os.environ.get("FS_SMOKE_SECTION")
            if section:
                window.stack.currentWidget().set_detail_section(entity_id, section)
        elif smoke_route.startswith("team_honors:"):
            window.navigate("team_honors", team_id=smoke_route.split(":", 1)[1])
        elif smoke_route.startswith("player_honors:"):
            window.navigate("player_honors", player_id=smoke_route.split(":", 1)[1])
        elif smoke_route.startswith("history_competition:"):
            _, comp, season = smoke_route.split(":", 2)
            window.navigate("history_competition", competition_id=comp, season=int(season))
        elif smoke_route.startswith("draft:"):
            window.navigate("draft", season=int(smoke_route.split(":", 1)[1]))
        elif smoke_route.startswith("weekly:"):
            window.navigate("weekly", week=int(smoke_route.split(":", 1)[1]))
        else:
            window.navigate(smoke_route)
    if smoke_route and sys.platform == "darwin":
        page = window.stack.currentWidget()
        if page.route_name in ("competition", "history_competition") and getattr(page, "_display_world", None):
            context = page._display_world
            comp = window._current_route[1].get("competition_id")
            year = int(context["season_number"])
            if os.environ.get("FS_SMOKE_SECTION"):
                page.detail_state(f"{comp}:{year}")["section"] = os.environ["FS_SMOKE_SECTION"]
            bracket_key = comp if year == int(window.world()["season_number"]) else f"{comp}:bracket:{year}"
            if os.environ.get("FS_SMOKE_BRACKET_VIEW"):
                page.detail_state(bracket_key)["bracket_view"] = os.environ["FS_SMOKE_BRACKET_VIEW"]
            if comp == "starpath" and os.environ.get("FS_SMOKE_SWISS_VIEW"):
                page.detail_state(f"{comp}:{year}")["swiss_view"] = os.environ["FS_SMOKE_SWISS_VIEW"]
            window._refresh_current_page(restore_scroll=True)
        elif page.route_name == "player" and os.environ.get("FS_SMOKE_SEASON"):
            page.detail_state(window._current_route[1]["player_id"])["stats_season"] = int(os.environ["FS_SMOKE_SEASON"])
            window._refresh_current_page(restore_scroll=True)
        elif page.route_name == "team" and os.environ.get("FS_SMOKE_TEAM_COMPETITION") is not None:
            recent = page.detail_state(window._current_route[1]["team_id"]).setdefault("recent", {})
            recent.update(competition=os.environ["FS_SMOKE_TEAM_COMPETITION"], page=int(os.environ.get("FS_SMOKE_TEAM_PAGE", "1")))
            window._refresh_current_page(restore_scroll=True)
    if os.environ.get("FS_SMOKE_SCROLL_BOTTOM"):
        def _scroll_bottom() -> None:
            page = window.stack.currentWidget()
            scroll = _page_scroll_area(page)
            if scroll is not None:
                from PySide6.QtWidgets import QAbstractScrollArea

                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                for area in scroll.findChildren(QAbstractScrollArea):
                    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        QTimer.singleShot(800, _scroll_bottom)
    smoke_result = os.environ.get("FS_SMOKE_RESULT")
    if smoke_result:
        def _write_result() -> None:
            import json

            page = window.stack.currentWidget()
            scroll = _page_scroll_area(page)
            labels = []
            try:
                from PySide6.QtWidgets import QLabel

                labels = [label.text() for label in page.findChildren(QLabel) if label.text()]
            except Exception:
                pass
            from football_simulator import runtime as _runtime

            main_module = type(window).__module__
            page_module = type(page).__module__
            data = {
                "app_version": app.applicationVersion(),
                "platform": app.platformName(),
                "pid": os.getpid(),
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
                "main_window_module": main_module,
                "main_window_file": getattr(sys.modules.get(main_module), "__file__", ""),
                "page": type(page).__name__,
                "page_module": page_module,
                "page_file": getattr(sys.modules.get(page_module), "__file__", ""),
                "resource_root": str(_runtime.resource_root()),
                "data_root": str(_runtime.data_root()),
                "save": window.current_save_name(),
                "route": list(window._current_route or ("", {})),
                "error": window.error_label.text(),
                "body_width": scroll.widget().width() if scroll is not None else None,
                "viewport_width": scroll.viewport().width() if scroll is not None else None,
                "horizontal_max": scroll.horizontalScrollBar().maximum() if scroll is not None else None,
                "labels": labels,
            }
            from football_simulator.ui.competition_boards import GraphPanel
            from football_simulator.ui.components import DataTable

            data["graphs"] = [
                {"class": type(panel.view).__name__, "nodes": len(panel.view._nodes),
                 "two_leg_nodes": sum(bool(node.get("legs")) for node in panel.view._nodes),
                 "headers": [header.get("label") for header in panel.view._headers],
                 "canvas_height": panel.view.height(), "scroll_height": panel.scroll.height(),
                 "vertical_max": panel.scroll.verticalScrollBar().maximum(),
                 "venue_cards": sum(bool(node.get("venues")) for node in panel.view._nodes)}
                for panel in page.findChildren(GraphPanel)
            ]
            data["honor_tables"] = [
                {"name": table.objectName(), "rows": table.rowCount(), "font_pixels": table.font().pixelSize()}
                for table in page.findChildren(DataTable)
                if table.objectName() in ("PlayerHonorAwards", "PlayerHonorAnnual", "TeamHonorTotals")
            ]
            data["season_tables"] = [
                {"name": table.objectName(), "rows": table._rows, "columns": [c["key"] for c in table._columns],
                 "team_icons": [not table.item(i, 2).icon().isNull() for i in range(table.rowCount())] if table.objectName() == "AnnualTop20" else []}
                for table in page.findChildren(DataTable) if table.objectName() in ("TeamSeasonMatrix", "AnnualTop20", "DraftResults", "PlayerSeasonStats", "PlayerCareerTeamSeasons")
            ]
            context = getattr(page, "_display_world", None)
            data["edition_context"] = {"season": context["season_number"], "source": context.get("_edition_source"),
                                       "fixtures": len(context["season"]["fixtures"])} if context else None
            data["player_origins"] = [{"text": item.text(), "font_pixels": item.font().pixelSize()} for item in page.findChildren(QLabel, "PlayerOrigin")]
            from football_simulator.ui.pages.honors import TeamHonorLeaderboard
            data["team_honor_boards"] = [board.data for board in page.findChildren(TeamHonorLeaderboard)]
            from PySide6.QtWidgets import QComboBox, QWidget
            from football_simulator.ui.components import MatchRowCard
            from football_simulator.ui.macos_presentation import Pagination
            from football_simulator.ui.pages.honors import TeamTrophyCollection
            data["team_trophy_collections"] = [item.data for item in page.findChildren(TeamTrophyCollection)]
            data["team_recent"] = [
                {"competition": frame.findChild(QComboBox, "TeamRecentCompetition").currentData(),
                 "match_ids": [item.match_id for item in frame.findChildren(MatchRowCard)],
                 "results": [item.property("teamResult") for item in frame.findChildren(MatchRowCard)],
                 "paging": frame.findChild(Pagination).state if frame.findChild(Pagination) else None}
                for frame in page.findChildren(QWidget, "TeamRecentResults")
            ]
            data["champion_tables"] = [
                {"name": table.objectName(), "rows": table._rows,
                 "columns": [column["key"] for column in table._columns],
                 "team_icons": [not table.item(i, 1).icon().isNull() for i in range(table.rowCount())]}
                for table in page.findChildren(DataTable)
                if table.objectName() in ("TeamHonorTotals", "LeagueChampions")
            ]
            try:
                Path(smoke_result).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        QTimer.singleShot(900, _write_result)
    smoke_shot = os.environ.get("FS_SMOKE_SCREENSHOT")
    if smoke_shot:
        def _grab() -> None:
            try:
                window.grab().save(smoke_shot)
            except OSError:
                pass
        QTimer.singleShot(1200, _grab)
    smoke_ms = os.environ.get("FS_SMOKE_EXIT_MS")
    if smoke_ms:
        QTimer.singleShot(int(smoke_ms), app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
