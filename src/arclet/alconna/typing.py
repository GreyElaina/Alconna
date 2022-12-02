"""Alconna 参数相关"""
from typing import TypeVar, Iterator, runtime_checkable, Protocol, Union, Any, Literal
from nepattern import BasePattern, type_parser, PatternModel

DataUnit = TypeVar("DataUnit", covariant=True)


@runtime_checkable
class DataCollection(Protocol[DataUnit]):
    """数据集合协议"""

    def __repr__(self) -> str: ...

    def __iter__(self) -> Iterator[DataUnit]: ...

    def __len__(self) -> int: ...


TDataCollection = TypeVar("TDataCollection", bound=DataCollection[Union[str, Any]])


class KeyWordVar(BasePattern):
    """对具名参数的包装"""
    base: BasePattern

    def __init__(self, value: Union[BasePattern, Any]):
        self.base = value if isinstance(value, BasePattern) else type_parser(value)
        assert isinstance(self.base, BasePattern)
        alias = f"@{value}"
        super().__init__(r"(.+?)", PatternModel.KEEP, str, alias=alias)

    def __repr__(self):
        return self.alias


class _Kw:
    __slots__ = ()

    def __getitem__(self, item):
        return KeyWordVar(item)

    def __matmul__(self, other):
        return KeyWordVar(other)


class MultiVar(BasePattern):
    """对可变参数的包装"""
    base: BasePattern
    flag: Literal["+", "*"]
    length: int

    def __init__(
            self,
            value: Union[BasePattern, Any],
            flag: Union[int, Literal["+", "*"]] = 1
    ):
        self.base = value if isinstance(value, BasePattern) else type_parser(value)
        assert isinstance(self.base, BasePattern)
        if not isinstance(flag, int):
            alias = f"({flag}{self.base})"
            self.flag = flag
            self.length = -1
        elif flag > 1:
            alias = f"(+{self.base})[:{flag}]"
            self.flag = "+"
            self.length = flag
        else:
            alias = str(self.base)
            self.flag = "+"
            self.length = 1
        super().__init__(r"(.+?)", PatternModel.KEEP, str, alias=alias)

    def __repr__(self):
        return self.alias


Nargs = MultiVar
Kw = _Kw()

__all__ = [
    "DataCollection", "TDataCollection", "MultiVar", "Nargs", "Kw", "KeyWordVar"
]
