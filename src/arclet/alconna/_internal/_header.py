from __future__ import annotations

import re
from inspect import isclass
from typing import TYPE_CHECKING, Any, Callable, TypeVar, Generic

from nepattern import BasePattern, MatchMode, UnionPattern, all_patterns, parser
from nepattern.util import TPattern
from tarina import lang

from ..typing import TPrefixes
from ._util import escape, unescape


def _prefixed(pat: BasePattern):
    if pat.mode not in (MatchMode.REGEX_MATCH, MatchMode.REGEX_CONVERT):
        return pat
    new_pat = pat.copy()
    new_pat.regex_pattern = re.compile(f"^{new_pat.pattern}")
    return new_pat


regex_patterns = {
    "str": r".+",
    "int": r"\-?\d+",
    "float": r"\-?\d+\.?\d*",
    "number": r"\-?\d+(?:\.\d*)?",
    "bool": "(?i:True|False)",
    "list": r"\[.+?\]",
    "tuple": r"\(.+?\)",
    "set": r"\{.+?\}",
    "dict": r"\{.+?\}",
}


def _convert_bracket(name: str, mapping: dict):
    """处理字符串中的括号对并转为正则表达式"""
    pattern_map = all_patterns()
    name = escape(name)
    if len(parts := re.split(r"(\{.*?})", name)) <= 1:
        return unescape(name), False
    for i, part in enumerate(parts):
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            res = part[1:-1].split(":")
            if not res or (len(res) > 1 and not res[1] and not res[0]):
                parts[i] = ".+?"
            elif len(res) == 1 or not res[1]:
                parts[i] = f"(?P<{res[0]}>.+)"
            elif not res[0]:
                pat = pattern_map.get(res[1], res[1])
                parts[i] = regex_patterns.get(res[1], str(pat.pattern if isinstance(pat, BasePattern) else pat))
            elif res[1] in pattern_map:
                mapping[res[0]] = pattern_map[res[1]]
                pat = regex_patterns.get(res[1], str(pattern_map[res[1]].pattern))
                parts[i] = f"(?P<{res[0]}>{pat})"
            else:
                parts[i] = f"(?P<{res[0]}>{res[1]})"
    return unescape("".join(parts)), True

TContent = TypeVar("TContent", TPattern, "set[str]", BasePattern)
TCompact = TypeVar("TCompact", TPattern, BasePattern, None)

class Header(Generic[TContent, TCompact]):
    """命令头部的匹配表达式"""

    __slots__ = ("origin", "content", "mapping", "compact", "compact_pattern", "flag")

    def __init__(
        self,
        origin: tuple[str | BasePattern, TPrefixes],
        content: set[str] | TPattern | BasePattern,
        mapping: dict[str, BasePattern] | None = None,
        compact: bool = False,
        compact_pattern: TPattern | BasePattern | None = None,
    ):
        self.origin = origin  # type: ignore
        self.content: TContent = content  # type: ignore
        self.mapping = mapping or {}
        self.compact = compact
        self.compact_pattern: TCompact = compact_pattern  # type: ignore

        if isinstance(self.content, set):
            self.flag = 0
        elif isinstance(self.content, TPattern):  # type: ignore
            self.flag = 1
        elif isinstance(self.content, BasePattern):
            self.flag = 2
        else:
            raise TypeError(lang.require("header", "content_error"))  # TODO

    def __repr__(self):
        self.origin: tuple[str, list[str]]
        if isinstance(self.content, set):
            if not self.origin[1]:
                return self.origin[0]
            if self.origin[0]:
                return f"[{'│'.join(self.origin[1])}]{self.origin[0]}" if len(self.content) > 1 else f"{self.content.copy().pop()}"  # noqa: E501
            return '│'.join(self.origin[1])
        if isinstance(self.content, TPattern):  # type: ignore
            if not self.origin[1]:
                return self.origin[0]
            return f"[{'│'.join(self.origin[1])}]{self.origin[0]}"
        if isinstance(self.content, list):
            return "│".join(map(str, self.content))
        return str(self.content)

    @classmethod
    def generate(
        cls,
        command: str | type | BasePattern,
        prefixes: TPrefixes,
        compact: bool,
    ):
        mapping = {}

        if isinstance(command, str):
            pair = (command, prefixes)
            if command.startswith("re:"):
                _cmd = command[3:]
                to_regex = True
            else:
                _cmd, to_regex = _convert_bracket(command, mapping)

            if not prefixes:  # prefixes is empty list
                cmd = re.compile(_cmd) if to_regex else {_cmd}
                return cls(pair, cmd, mapping, compact, re.compile(f"^{_cmd}"))

            _cmd_pattern = _cmd

            prf = "|".join(re.escape(h) for h in prefixes)
            compp = re.compile(f"^(?:{prf}){_cmd_pattern}")

            if to_regex:
                return cls(pair, re.compile(f"(?:{prf}){_cmd}"), mapping, compact, compp)
        else:
            _cmd = parser(command)
            pair = (_cmd, prefixes)

            if not prefixes:  # prefixes is empty list
                return cls(pair, _cmd, mapping, compact, _prefixed(_cmd))
            
            _cmd_pattern = _cmd.pattern

            prf = "|".join(re.escape(h) for h in prefixes)
            compp = re.compile(f"^(?:{prf}){_cmd_pattern}")

        return cls(pair, {f"{h}{_cmd}" for h in prefixes}, mapping, compact, compp)
