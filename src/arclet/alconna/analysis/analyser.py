import re
import traceback
from weakref import finalize
from copy import copy
from abc import ABCMeta, abstractmethod
from typing import Dict, Union, List, Optional, TYPE_CHECKING, Tuple, Any, Pattern, Generic, TypeVar, \
    Set, Callable

from ..manager import command_manager
from ..exceptions import NullMessage
from ..base import Args, Option, Subcommand, Sentence
from ..arpamar import Arpamar
from ..util import split_once, split
from ..typing import DataCollection, pattern_map, BasePattern, args_type_parser
from ..config import config

if TYPE_CHECKING:
    from ..core import Alconna

T_Origin = TypeVar('T_Origin')


class Analyser(Generic[T_Origin], metaclass=ABCMeta):
    """
    Alconna使用的分析器基类, 实现了一些通用的方法

    Attributes:
        current_index(int): 记录解析时当前数据的index
        content_index(int): 记录内部index
        head_matched: 是否匹配了命令头部
    """
    preprocessors: Dict[str, Callable[..., Any]] = {}
    text_sign: str = 'text'

    alconna: 'Alconna'  # Alconna实例
    current_index: int  # 当前数据的index
    content_index: int  # 内部index
    is_str: bool  # 是否是字符串
    raw_data: List[Union[Any, List[str]]]  # 原始数据
    ndata: int  # 原始数据的长度
    command_params: Dict[str, Union[Sentence, List[Option], Subcommand]]
    param_ids: Set[str]
    # 命令头部
    command_header: Union[
        Union[Pattern, BasePattern], List[Tuple[Any, Pattern]],
        Tuple[Union[Tuple[List[Any], Pattern], List[Any]], Union[Pattern, BasePattern]],
    ]
    separators: Set[str]  # 分隔符
    is_raise_exception: bool  # 是否抛出异常
    options: Dict[str, Any]  # 存放解析到的所有选项
    subcommands: Dict[str, Any]  # 存放解析到的所有子命令
    main_args: Dict[str, Any]  # 主参数
    header: Optional[Union[Dict[str, Any], bool]]  # 命令头部
    need_main_args: bool  # 是否需要主参数
    head_matched: bool  # 是否匹配了命令头部
    part_len: range  # 分段长度
    default_main_only: bool  # 默认只有主参数
    self_args: Args  # 自身参数
    filter_out: List[str]  # 元素黑名单
    temporary_data: Dict[str, Any]  # 临时数据
    origin_data: T_Origin  # 原始数据
    temp_token: int  # 临时token
    used_tokens: Set[int]  # 已使用的token
    sentences: List[str]  # 存放解析到的所有句子

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "filter_out"):
            raise TypeError(config.lang.analyser_filter_missing)

    @staticmethod
    def generate_token(data: List[Union[Any, List[str]]], hs=hash) -> int:
        return hs(repr(data))

    def __init__(self, alconna: "Alconna"):
        self.reset()
        self.used_tokens = set()
        self.origin_data = None
        self.alconna = alconna
        self.self_args = alconna.args
        self.separators = alconna.separators
        self.is_raise_exception = alconna.is_raise_exception
        self.need_main_args = False
        self.default_main_only = False
        self.__handle_main_args__(alconna.args, alconna.nargs)
        self.__init_header__(alconna.command, alconna.headers)
        self.__init_actions__()

        def _clr(a: 'Analyser'):
            a.reset()
            a.used_tokens.clear()
            del a.origin_data
            del a.alconna

        finalize(self, _clr, self)

    def __handle_main_args__(self, main_args: Args, nargs: Optional[int] = None):
        nargs = nargs or len(main_args)
        if nargs > 0 and nargs > main_args.optional_count:
            self.need_main_args = True  # 如果need_marg那么match的元素里一定得有main_argument
        _de_count = sum(a['default'] is not None for k, a in main_args.argument.items())
        if _de_count and _de_count == nargs:
            self.default_main_only = True

    def __init_header__(
            self,
            command_name: Union[str, type, BasePattern],
            headers: Union[List[Union[str, Any]], List[Tuple[Any, str]]]
    ):
        if isinstance(command_name, str) and len(parts := re.split(r"(\{.*?})", command_name)) > 1:
            for i, part in enumerate(parts):
                if not part:
                    continue
                if res := re.match(r"\{(.*?)}", part):
                    _res = res.group(1)
                    if not _res:
                        parts[i] = ".+?"
                        continue
                    _parts = _res.split(":")
                    if len(_parts) == 1:
                        parts[i] = f"(?P<{_parts[0]}>.+?)"
                    elif not _parts[0] and not _parts[1]:
                        parts[i] = ".+?"
                    elif not _parts[0] and _parts[1]:
                        parts[i] = f"{pattern_map.get(_parts[1], _parts[1])}".replace("(", "").replace(")", "")
                    elif not _parts[1] and _parts[0]:
                        parts[i] = f"(?P<{_parts[0]}>.+?)"
                    else:
                        parts[i] = (
                            f"(?P<{_parts[0]}>"
                            f"{pattern_map[_parts[1]].pattern if _parts[1] in pattern_map else _parts[1]})"
                        )
            command_name = "".join(parts)

        if isinstance(command_name, str):
            _command_name, _command_str = re.compile(command_name), command_name
        else:
            _command_name, _command_str = copy(args_type_parser(command_name)), str(command_name)

        if headers == [""]:
            self.command_header = _command_name

        elif isinstance(headers[0], tuple):
            mixins = [(h[0], re.compile(re.escape(h[1]) + _command_str)) for h in headers]  # type: ignore
            self.command_header = mixins
        else:
            elements = []
            ch_text = ""
            for h in headers:
                if isinstance(h, str):
                    ch_text += f"{re.escape(h)}|"
                else:
                    elements.append(h)
            if not elements:
                if isinstance(_command_name, Pattern):
                    self.command_header = re.compile(f"(?:{ch_text[:-1]}){_command_str}")   # noqa
                else:
                    _command_name.pattern = f"(?:{ch_text[:-1]}){_command_name.pattern}"
                    _command_name.regex_pattern = re.compile(_command_name.pattern)
                    self.command_header = _command_name
            elif not ch_text:
                self.command_header = (elements, _command_name)
            else:
                self.command_header = (elements, re.compile(f"(?:{ch_text[:-1]})")), _command_name   # noqa

    def __init_actions__(self):
        actions = self.alconna.action_list
        actions['main'] = self.alconna.action
        for opt in self.alconna.options:
            if isinstance(opt, Option) and opt.action:
                actions['options'][opt.dest] = opt.action
            if isinstance(opt, Subcommand):
                if opt.action:
                    actions['subcommands'][opt.dest] = opt.action
                for option in opt.options:
                    if option.action:
                        actions['subcommands'][f"{opt.dest}.{option.dest}"] = option.action

    @staticmethod
    def default_params_generator(analyser: "Analyser"):
        analyser.param_ids = set()
        analyser.command_params = {}
        analyser.part_len = range(len(analyser.alconna.options) + 1)
        for opts in analyser.alconna.options:
            if isinstance(opts, Option):
                if analyser.command_params.get(opts.name):
                    analyser.command_params[opts.name].append(opts)  # type: ignore
                else:
                    analyser.command_params[opts.name] = [opts]
                analyser.param_ids.update(opts.aliases)
            elif isinstance(opts, Subcommand):
                analyser.command_params[opts.name] = opts
                analyser.param_ids.add(opts.name)
                opts.sub_part_len = range(len(opts.options) + 1)
                for sub_opts in opts.options:
                    if opts.sub_params.get(sub_opts.name):
                        opts.sub_params[sub_opts.name].append(sub_opts)  # type: ignore
                    else:
                        opts.sub_params[sub_opts.name] = [sub_opts]
                    if sub_opts.requires:
                        opts.sub_params.update({k: Sentence(name=k) for k in sub_opts.requires})
                    analyser.param_ids.update(sub_opts.aliases)
            if opts.requires:
                analyser.param_ids.update(opts.requires)
                analyser.command_params.update({k: Sentence(name=k) for k in opts.requires})

    def __repr__(self):
        return f"<{self.__class__.__name__} of {self.alconna.path}>"

    def reset(self):
        """重置分析器"""
        self.current_index, self.content_index, self.ndata, self.temp_token = 0, 0, 0, 0
        self.is_str, self.head_matched = False, False
        self.temporary_data, self.main_args, self.options, self.subcommands = {}, {}, {}, {}
        self.raw_data, self.sentences = [], []
        self.origin_data, self.header = None, None

    def next_data(self, separate: Optional[Set[str]] = None, pop: bool = True) -> Tuple[Union[str, Any], bool]:
        """获取解析需要的下个数据"""
        self.temporary_data["separators"] = None
        if self.current_index == self.ndata:
            return "", True
        _current_data = self.raw_data[self.current_index]
        if isinstance(_current_data, list):
            _rest_text: str = ""
            _text = _current_data[self.content_index]
            if separate and not self.separators.issuperset(separate):
                _text, _rest_text = split_once(_text, separate)
            if pop:
                if _rest_text:  # 这里实际上还是pop了
                    self.temporary_data["separators"] = separate
                    _current_data[self.content_index] = _rest_text
                else:
                    self.content_index += 1
            if len(_current_data) == self.content_index:
                self.current_index += 1
                self.content_index = 0
            return _text, True
        if pop:
            self.current_index += 1
        return _current_data, False

    def rest_count(self, separate: Optional[Set[str]] = None) -> int:
        """获取剩余的数据个数"""
        _result = 0
        is_cur = False
        for _data in self.raw_data[self.current_index:]:
            if isinstance(_data, list):
                for s in (_data[self.content_index:] if not is_cur else _data):
                    is_cur = True
                    _result += len(split(s, separate)) if separate and not self.separators.issuperset(separate) else 1
            else:
                _result += 1
        return _result

    def reduce_data(self, data: Union[str, Any], replace=False):
        """把pop的数据放回 (实际只是‘指针’移动)"""
        if not data:
            return
        if self.current_index == self.ndata:
            self.current_index -= 1
            if isinstance(data, str):
                self.content_index = len(self.raw_data[self.current_index]) - 1
            if replace:
                if isinstance(data, str):
                    self.raw_data[self.current_index][self.content_index] = data
                else:
                    self.raw_data[self.current_index] = data
        else:
            _current_data = self.raw_data[self.current_index]
            if isinstance(_current_data, list) and isinstance(data, str):
                if seps := self.temporary_data.get("separators", None):
                    _current_data[self.content_index] = f"{data}{seps.copy().pop()}{_current_data[self.content_index]}"
                else:
                    self.content_index -= 1
                    if replace:
                        _current_data[self.content_index] = data
            else:
                self.current_index -= 1
                if replace:
                    self.raw_data[self.current_index] = data

    def recover_raw_data(self) -> List[Union[str, Any]]:
        """将处理过的命令数据大概还原"""
        _result = []
        is_cur = False
        for _data in self.raw_data[self.current_index:]:
            if isinstance(_data, list):
                if not is_cur:
                    _result.append(f'{self.separators.copy().pop()}'.join(_data[self.content_index:]))
                    is_cur = True
                else:
                    _result.append(f'{self.separators.copy().pop()}'.join(_data))
            else:
                _result.append(_data)
        self.current_index = self.ndata
        self.content_index = 0
        return _result

    def process_message(self, data: DataCollection[Union[str, Any]]) -> 'Analyser':
        """命令分析功能, 传入字符串或消息链, 应当在失败时返回fail的arpamar"""
        self.origin_data = data
        if isinstance(data, str):
            self.is_str = True
            data = [data]
        separates = self.separators
        i, exc = 0, None
        raw_data = self.raw_data
        for unit in data:
            if (uname := unit.__class__.__name__) in self.filter_out:
                continue
            if (proc := self.preprocessors.get(uname)) and (res := proc(unit)):
                unit = res
            if text := getattr(unit, self.text_sign, unit if isinstance(unit, str) else None):
                if not (res := split(text.lstrip(), separates)):
                    continue
                raw_data.append(res)
            else:
                raw_data.append(unit)
            i += 1
        if i < 1:
            exp = NullMessage(config.lang.analyser_handle_null_message.format(target=data))
            if self.is_raise_exception:
                raise exp
            self.temporary_data["fail"] = exp
        else:
            self.ndata = i
            if config.enable_message_cache:
                self.temp_token = self.generate_token(raw_data)
        return self

    @abstractmethod
    def analyse(self, message: Union[DataCollection[Union[str, Any]], None] = None) -> Arpamar:
        """主体解析函数, 应针对各种情况进行解析"""

    @staticmethod
    def converter(command: str) -> T_Origin:
        return command  # type: ignore

    def export(self, exception: Optional[BaseException] = None, fail: bool = False) -> Arpamar:
        """创建arpamar, 其一定是一次解析的最后部分"""
        result = Arpamar(self.alconna)
        result.head_matched = self.head_matched
        result.matched = not fail
        if fail:
            result.error_info = repr(exception or traceback.format_exc(limit=1))
            result.error_data = self.recover_raw_data()
        else:
            result.encapsulate_result(self.header, self.main_args, self.options, self.subcommands)
            if config.enable_message_cache:
                command_manager.record(self.temp_token, self.origin_data, result)  # type: ignore
                self.used_tokens.add(self.temp_token)
        self.reset()
        return result
