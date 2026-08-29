"""UI v2 统一导航外壳的纯 Python 路由层（阶段 3）。

本模块刻意保持"纯 Python、零 Qt 导入"，保证没有 GUI 环境时也能完整测试
路由模型、历史栈、页面状态缓存与面包屑逻辑（见 ``tests/test_navigation_shell.py``）。

设计要点（对应实施方案 §7.1 路由模型 / §7.2 全局链接合同 / §7.3 状态保留）：

- ``Route``：不可变（frozen dataclass）路由值对象，``Route(name, **params)``
  按 14 个规范路由的参数 schema 校验。所有参数值在内部规范化为字符串
  （schema 标注为 int 的参数会自动转换并校验为整数字符串），因此实例可哈希、
  可比较、可安全作为字典键；序列化格式见 ``Route.to_path``。
- ``Router``：浏览器式后退/前进历史栈 + 页面状态缓存。页面状态是调用方给的
  不透明对象，本层不理解其内容；切换存档时由外壳调用 ``clear_page_states()``。
- ``breadcrumbs``：根据路由与可选的显示上下文推导面包屑列表。
"""

from __future__ import annotations

import dataclasses
import urllib.parse
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "ROUTE_NAMES",
    "Breadcrumb",
    "Route",
    "Router",
    "breadcrumbs",
    "route_title",
]


# ---------------------------------------------------------------------------
# 路由 schema（实施方案 §7.1 的 14 个规范路由）
# ---------------------------------------------------------------------------

_KIND_STR = "str"
_KIND_INT = "int"


@dataclasses.dataclass(frozen=True)
class _ParamSpec:
    """单个路由参数的规格：是否必填、值类型（int 参数会自动转换）。"""

    required: bool
    kind: str


def _spec(required: bool, kind: str) -> _ParamSpec:
    return _ParamSpec(required=required, kind=kind)


_ROUTE_PARAM_SCHEMAS: Mapping[str, Mapping[str, _ParamSpec]] = {
    "dashboard": {},
    "weekly_report": {"week": _spec(True, _KIND_INT)},
    "season_overview": {"season": _spec(True, _KIND_INT)},
    "competition": {"competition": _spec(True, _KIND_STR), "season": _spec(True, _KIND_INT)},
    "matches": {
        "season": _spec(True, _KIND_INT),
        "competition": _spec(False, _KIND_STR),
        "week": _spec(False, _KIND_INT),
    },
    "match": {"match": _spec(True, _KIND_STR)},
    "teams": {},
    "team": {"team": _spec(True, _KIND_INT), "season": _spec(True, _KIND_INT)},
    "players": {},
    "player": {
        "player": _spec(True, _KIND_STR),
        "season": _spec(True, _KIND_INT),
        "tab": _spec(False, _KIND_STR),
    },
    "transfers": {"season": _spec(True, _KIND_INT)},
    "draft": {"season": _spec(True, _KIND_INT)},
    "history": {"season": _spec(False, _KIND_INT)},
    "saves": {},
}

ROUTE_NAMES: Tuple[str, ...] = tuple(_ROUTE_PARAM_SCHEMAS)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, init=False, repr=False)
class Route:
    """不可变路由值对象。

    构造方式为 ``Route(name, **params)``，参数按 schema 校验：

    - 未知路由 / 缺少必填参数 / 传入了 schema 之外的参数名 → ``ValueError``；
    - int 型参数接受 ``int`` 或整数字符串（自动转换），非法值 → ``ValueError``；
    - 所有参数值规范化为字符串后存入 ``params``（只读映射），保证实例可哈希。
    """

    name: str
    params: Mapping[str, str]

    def __init__(self, name: str, **params: Any) -> None:
        schema = _ROUTE_PARAM_SCHEMAS.get(name)
        if schema is None:
            raise ValueError(f"未知路由：{name!r}，可用路由：{'、'.join(ROUTE_NAMES)}")
        unknown = sorted(set(params) - set(schema))
        if unknown:
            raise ValueError(
                f"路由 {name!r} 不支持参数：{'、'.join(unknown)}"
                f"（支持：{'、'.join(schema) if schema else '无参数'}）"
            )
        missing = sorted(key for key, spec in schema.items() if spec.required and key not in params)
        if missing:
            raise ValueError(f"路由 {name!r} 缺少必填参数：{'、'.join(missing)}")
        normalized = {
            key: _coerce_param_value(name, key, schema[key], value) for key, value in params.items()
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "params", MappingProxyType(normalized))

    # -- 序列化 ---------------------------------------------------------------

    def to_path(self) -> str:
        """规范序列化：``player?player=real%3A%3Apedri&season=2``。

        参数按名字排序，键与值均做 URL 百分号转义；无参数时只返回路由名。
        """

        if not self.params:
            return self.name
        query = "&".join(
            f"{urllib.parse.quote(key, safe='')}={urllib.parse.quote(value, safe='')}"
            for key, value in sorted(self.params.items())
        )
        return f"{self.name}?{query}"

    @classmethod
    def parse(cls, path: str) -> "Route":
        """从 ``to_path`` 的输出反序列化；与 ``to_path`` 互逆。"""

        if not isinstance(path, str):
            raise ValueError(f"路由路径必须是字符串，得到 {type(path).__name__}")
        text = path.strip()
        if not text:
            raise ValueError("路由路径不能为空")
        raw_name, has_query, raw_query = text.partition("?")
        raw_params: Dict[str, str] = {}
        if has_query:
            raw_params = dict(urllib.parse.parse_qsl(raw_query, keep_blank_values=True))
        return cls(raw_name.strip(), **raw_params)

    # -- 便捷访问 -------------------------------------------------------------

    @property
    def route_key(self) -> str:
        """页面状态缓存与历史栈去重的键，等于 ``to_path()``。"""

        return self.to_path()

    def int_param(self, name: str) -> Optional[int]:
        """按 int 语义读取参数；参数不存在时返回 ``None``。"""

        value = self.params.get(name)
        if value is None:
            return None
        return int(value)

    # -- 对象协议 -------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not Route:
            return NotImplemented
        return self.name == other.name and dict(self.params) == dict(other.params)  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((Route, self.name, tuple(sorted(self.params.items()))))

    def __repr__(self) -> str:
        return f"Route(name={self.name!r}, params={dict(self.params)!r})"

    def __str__(self) -> str:
        return self.to_path()


def _coerce_param_value(route_name: str, param_name: str, spec: _ParamSpec, value: object) -> str:
    """把用户传入的参数值规范化为字符串；非法值抛 ``ValueError``。"""

    if value is None:
        raise ValueError(f"路由 {route_name!r} 的参数 {param_name!r} 不能为 None")
    if spec.kind == _KIND_INT:
        if isinstance(value, bool):
            raise ValueError(f"路由 {route_name!r} 的参数 {param_name!r} 需要整数，得到布尔值 {value!r}")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            text = value.strip()
            try:
                int(text)
            except ValueError:
                raise ValueError(
                    f"路由 {route_name!r} 的参数 {param_name!r} 需要整数，得到 {value!r}"
                ) from None
            return text
        raise ValueError(
            f"路由 {route_name!r} 的参数 {param_name!r} 需要整数，"
            f"得到 {type(value).__name__} 类型的 {value!r}"
        )
    # 字符串参数：宽容地接受 int（自动转 str），拒绝其它类型。
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    raise ValueError(
        f"路由 {route_name!r} 的参数 {param_name!r} 需要字符串，得到 {type(value).__name__} 类型的 {value!r}"
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_Observer = Callable[["Route", str], None]


class Router:
    """纯 Python 观察者模式路由器。

    - ``navigate(route)``：与当前 ``route_key`` 相同则无操作；否则当前路由入
      back 栈、清空 forward 栈、以 cause='navigate' 通知观察者。
    - ``back()`` / ``forward()``：在栈间移动并以 cause='back' / 'forward' 通知
      观察者；栈空时无操作（不入栈、不通知）。
    - 历史上限 ``MAX_HISTORY``（200）：back 栈超限丢最旧。
    - 页面状态缓存：``set_page_state`` / ``get_page_state`` / ``clear_page_states``，
      状态是调用方提供的不透明对象（例如 ViewModel 快照）。
    """

    MAX_HISTORY = 200

    def __init__(self) -> None:
        self._current: Optional[Route] = None
        self._back: List[Route] = []
        self._forward: List[Route] = []
        self._observers: List[_Observer] = []
        self._page_states: Dict[str, object] = {}

    # -- 当前状态 -------------------------------------------------------------

    @property
    def current(self) -> Optional[Route]:
        return self._current

    @property
    def can_back(self) -> bool:
        return bool(self._back)

    @property
    def can_forward(self) -> bool:
        return bool(self._forward)

    # -- 导航 -----------------------------------------------------------------

    def navigate(self, route: Route) -> None:
        if not isinstance(route, Route):
            raise TypeError(f"navigate 需要 Route 实例，得到 {type(route).__name__}")
        if self._current is not None and route.route_key == self._current.route_key:
            return
        if self._current is not None:
            self._back.append(self._current)
            if len(self._back) > self.MAX_HISTORY:
                del self._back[0]
        self._forward.clear()
        self._current = route
        self._notify(route, "navigate")

    def back(self) -> Optional[Route]:
        if not self._back:
            return None
        previous = self._back.pop()
        if self._current is not None:
            self._forward.append(self._current)
        self._current = previous
        self._notify(previous, "back")
        return previous

    def forward(self) -> Optional[Route]:
        if not self._forward:
            return None
        next_route = self._forward.pop()
        if self._current is not None:
            self._back.append(self._current)
            if len(self._back) > self.MAX_HISTORY:
                del self._back[0]
        self._current = next_route
        self._notify(next_route, "forward")
        return next_route

    # -- 观察者 ---------------------------------------------------------------

    def observe(self, callback: _Observer) -> None:
        """注册观察者，签名为 ``callback(route: Route, cause: str)``。"""

        if callback not in self._observers:
            self._observers.append(callback)

    def unobserve(self, callback: _Observer) -> None:
        """移除观察者；未注册时静默忽略。"""

        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def _notify(self, route: Route, cause: str) -> None:
        for callback in list(self._observers):
            callback(route, cause)

    # -- 页面状态缓存（§7.3 状态保留） -----------------------------------------

    def set_page_state(self, route_key: str, state: object) -> None:
        self._page_states[route_key] = state

    def get_page_state(self, route_key: str) -> object:
        return self._page_states.get(route_key)

    def clear_page_states(self) -> None:
        """切换存档时由外壳调用，避免旧存档的状态指向新存档的实体 ID。"""

        self._page_states.clear()


# ---------------------------------------------------------------------------
# 面包屑
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Breadcrumb:
    """面包屑项；``route`` 为 ``None`` 表示当前页（不可点击）。"""

    label: str
    route: Optional[Route]


ROUTE_TITLES: Mapping[str, str] = {
    "dashboard": "主页",
    "weekly_report": "本周战报",
    "season_overview": "赛季总览",
    "competition": "赛事详情",
    "matches": "比赛中心",
    "match": "比赛详情",
    "teams": "球队",
    "team": "球队详情",
    "players": "球员",
    "player": "球员详情",
    "transfers": "转会",
    "draft": "选秀",
    "history": "历史",
    "saves": "存档",
}


def route_title(route: Route, context: Optional[Mapping[str, object]] = None) -> str:
    """路由的显示标题；实体详情类路由优先使用 context 提供的显示名。"""

    context = context or {}
    if route.name == "match":
        return str(context.get("match_label") or context.get("match_name") or ROUTE_TITLES["match"])
    if route.name == "player":
        return str(context.get("player_name") or ROUTE_TITLES["player"])
    if route.name == "team":
        return str(context.get("team_name") or ROUTE_TITLES["team"])
    if route.name == "competition":
        return str(context.get("competition_name") or ROUTE_TITLES["competition"])
    return ROUTE_TITLES.get(route.name, route.name)


def _safe_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def breadcrumbs(route: Route, context: Optional[Mapping[str, object]] = None) -> List[Breadcrumb]:
    """推导面包屑（最后一项始终是当前页，``route=None`` 不可点击）。

    ``context`` 提供显示信息，可含：``season``（比赛所在赛季）、``week``、
    ``match_label``、``player_name``、``team_name``、``competition_name``。

    嵌套关系：

    - ``match`` → [比赛中心 → matches?season=s, 第 N 周 → matches?season=s&week=n,
      当前页]。season/week 取自 context；缺 season 时无法构造比赛中心链接，
      因此只返回当前页一项（绝不虚构参数）；缺 week 时省略"第 N 周"中层。
    - ``player`` → [球员 → players, 当前页]；``team`` → [球队 → teams, 当前页]；
      ``competition`` → [赛季总览 → season_overview?season=s, 当前页]。
    - 其余一级路由 → [当前页]。
    """

    context = context or {}
    if route.name == "match":
        label = str(context.get("match_label") or context.get("match_name") or ROUTE_TITLES["match"])
        season = _safe_int(context.get("season"))
        crumbs: List[Breadcrumb] = []
        if season is not None:
            crumbs.append(Breadcrumb("比赛中心", Route("matches", season=season)))
            week = _safe_int(context.get("week"))
            if week is not None:
                crumbs.append(Breadcrumb(f"第 {week} 周", Route("matches", season=season, week=week)))
        crumbs.append(Breadcrumb(label, None))
        return crumbs
    if route.name == "player":
        return [
            Breadcrumb("球员", Route("players")),
            Breadcrumb(route_title(route, context), None),
        ]
    if route.name == "team":
        return [
            Breadcrumb("球队", Route("teams")),
            Breadcrumb(route_title(route, context), None),
        ]
    if route.name == "competition":
        season = route.params.get("season", "")
        return [
            Breadcrumb("赛季总览", Route("season_overview", season=season)),
            Breadcrumb(route_title(route, context), None),
        ]
    return [Breadcrumb(route_title(route, context), None)]
