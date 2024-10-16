from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, overload

from .arparma import Arparma, ArparmaBehavior
from .exceptions import BehaveCancelled
__all__ = ["set_default"]


class _MISSING_TYPE:
    ...

MISSING = _MISSING_TYPE()


@dataclass(init=True, eq=True, unsafe_hash=True)
class _SetDefault(ArparmaBehavior):
    _default: Any = field(default=MISSING)
    _default_factory: Callable | _MISSING_TYPE = field(default=MISSING)
    path: str | None = field(default=None)

    @property
    def default(self):
        if self._default is not MISSING:
            return self._default
        if callable(self._default_factory):
            return self._default_factory()
        raise BehaveCancelled("cannot specify both value and factory")

    def operate(self, interface: Arparma):
        if not self.path:
            raise BehaveCancelled
        def_val = self.default
        if not interface.query(self.path):
            self.update(interface, self.path, def_val)


@overload
def set_default(
    *,
    value: Any,
    path: str,
) -> _SetDefault:
    ...


@overload
def set_default(
    *,
    factory: Callable[..., Any],
    path: str,
) -> _SetDefault:
    ...


def set_default(
    *,
    value: Any = MISSING,
    factory: Callable[..., Any] | _MISSING_TYPE = MISSING,
    path: str | None = None,
) -> _SetDefault:
    """
    设置一个选项的默认值, 在无该选项时会被设置

    当 option 与 subcommand 同时传入时, 则会被设置为该 subcommand 内 option 的默认值

    Args:
        value (Any): 默认值
        factory (Callable[..., Any]): 默认值工厂
        path: str: 参数路径
    """
    if value is not MISSING and factory is not MISSING:
        raise ValueError("cannot specify both value and factory")

    return _SetDefault(value, factory, path)
