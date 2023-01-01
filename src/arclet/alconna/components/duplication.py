from __future__ import annotations

from typing import TYPE_CHECKING, cast 
from inspect import isclass
from typing_extensions import Self

from ..config import config
from .stub import BaseStub, ArgsStub, SubcommandStub, OptionStub, Subcommand, Option

if TYPE_CHECKING:
    from ..core import Alconna, AlconnaGroup
    from ..arparma import Arparma


class Duplication:
    """
    用以更方便的检查、调用解析结果的类。
    """
    __stubs__: dict[str, str | list[str] | dict[str, str]]

    @property
    def header(self):
        return self.__stubs__['header']

    def set_target(self, target: Arparma) -> Self:
        if target.header:
            self.__stubs__['header'] = target.header.copy()
        if self.__stubs__.get("main_args"):
            getattr(self, self.__stubs__["main_args"]).set_result(target.main_args.copy())  # type: ignore
        for key in self.__stubs__["options"]:
            if key in target.options:
                getattr(self, key).set_result(target._options[key].copy())  # noqa
        for key in self.__stubs__["subcommands"]:
            if key in target.subcommands:
                getattr(self, key).set_result(target._subcommands[key].copy())  # noqa
        for key in target.all_matched_args:
            if key in self.__stubs__['other_args']:
                setattr(self, key, target.all_matched_args[key])
        for key in target.header:
            if key in self.__stubs__['other_args']:
                setattr(self, key, target.header[key])
        return self

    def __init__(self, target: Alconna | AlconnaGroup):
        self.__stubs__ = {"options": [], "subcommands": [], "other_args": [], "header": {}}
        for key, value in self.__annotations__.items():
            if isclass(value) and issubclass(value, BaseStub):
                if value == ArgsStub:
                    setattr(self, key, ArgsStub(target.args))
                    self.__stubs__["main_args"] = key  # type: ignore
                elif value == SubcommandStub:
                    for subcommand in filter(lambda x: isinstance(x, Subcommand), target.options):
                        if subcommand.dest == key:
                            setattr(self, key, SubcommandStub(subcommand))  # type: ignore
                            self.__stubs__["subcommands"].append(key)
                elif value == OptionStub:
                    for option in filter(lambda x: isinstance(x, Option), target.options):
                        if option.dest == key:
                            setattr(self, key, OptionStub(option))  # type: ignore
                            self.__stubs__["options"].append(key)
                else:
                    raise TypeError(config.lang.duplication_stub_type_error.format(target=value))
            elif key != '__stubs__':
                self.__stubs__['other_args'].append(key)

    def __repr__(self):
        return f'<{self.__class__.__name__} with {self.__stubs__}>'

    def option(self, name: str) -> OptionStub | None:
        return cast(OptionStub, getattr(self, name, None))

    def subcommand(self, name: str) -> SubcommandStub | None:
        return cast(SubcommandStub, getattr(self, name, None))


def generate_duplication(command: Alconna) -> Duplication:
    """
    依据给定的命令生成一个解析结果的检查类。
    """
    options = filter(lambda x: isinstance(x, Option), command.options)
    subcommands = filter(lambda x: isinstance(x, Subcommand), command.options)
    return cast(Duplication, type(
        command.name.strip("/\\.-:") + 'Interface',
        (Duplication,), {
            "__annotations__": {
                **{"args": ArgsStub},
                **{opt.dest: OptionStub for opt in options},
                **{sub.dest: SubcommandStub for sub in subcommands},
            }
        }
    )(command))
