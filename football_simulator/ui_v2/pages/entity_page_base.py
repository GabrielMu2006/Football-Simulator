"""阶段 4 实体页面契约（由主 Agent 维护，页面与外壳共同遵守）。

页面（EntityPageBase 子类）只通过 PageContext 与外壳交互：
- 数据：`save_name_provider()` 给出当前存档名，页面用
  `football_simulator.queries.base.open_read_connection` 只读查询；
- 导航：`navigate(route)` 走全局 Router（禁止页面自行遍历侧栏）；
- 状态：`save_state/restore_state` 以 route_key 为键保存筛选/排序/页签/滚动
  位置，返回列表页时由外壳在 apply_route 前自动可取。

外壳（MainWindow，Agent D 所有）对每个路由页面调用：
- `apply_route(route)`：路由切换（页面内部完成状态恢复 + 数据刷新）；
- `refresh()`：存档切换/数据变更后的强制刷新；
- `route_context()`：面包屑上下文（显示名等，可选）。

构造签名统一为 `__init__(self, context: PageContext, parent=None)`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, Optional

from PySide6.QtWidgets import QWidget

from football_simulator.ui_v2.navigation import Route

if TYPE_CHECKING:  # 避免运行时重依赖；仅写流程页面（审核/选秀/存档）使用
    from football_simulator.ui_v2.services import SimulatorUIService

PageState = Dict[str, object]


@dataclass(frozen=True)
class PageContext:
    """外壳注入给每个实体页面的运行环境。"""

    save_name_provider: Callable[[], str]
    navigate: Callable[[Route], None]
    route_provider: Callable[[], Optional[Route]]
    page_state_get: Callable[[str], Optional[PageState]]
    page_state_set: Callable[[str, PageState], None]
    service: Optional["SimulatorUIService"] = None  # 写流程页面（审核/选秀/存档）专用
    request_save_reload: Optional[Callable[[str], None]] = None  # 存档页：请外壳切换/重载存档


class EntityPageBase(QWidget):
    """查询驱动的实体页面基类。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._context = context
        self._current_route: Optional[Route] = None
        self._build_ui()

    # -- 子类必须实现 ----------------------------------------------------

    def _build_ui(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """按当前路由参数与最新存档数据重建内容（外壳在路由切换与数据
        变更后调用；实现必须幂等且不阻塞）。"""
        raise NotImplementedError

    # -- 子类常用工具 ----------------------------------------------------

    def current_route(self) -> Optional[Route]:
        return self._current_route

    def navigate(self, route: Route) -> None:
        self._context.navigate(route)

    def save_name(self) -> str:
        return self._context.save_name_provider()

    def save_state(self, state: PageState) -> None:
        if self._current_route is not None:
            self._context.page_state_set(self._current_route.route_key, dict(state))

    def stored_state(self, route: Optional[Route] = None) -> PageState:
        target = route if route is not None else self._current_route
        if target is None:
            return {}
        state = self._context.page_state_get(target.route_key)
        return dict(state) if state else {}

    def route_context(self) -> dict:
        """面包屑上下文（子类可选提供：显示名等）。"""
        return {}

    # -- 外壳入口 --------------------------------------------------------

    def apply_route(self, route: Route) -> None:
        self._current_route = route
        self.refresh()
