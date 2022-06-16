import re
from typing import Iterable, Union, List, Any, Dict, Pattern, Tuple, Set

from .analyser import Analyser
from ..exceptions import ParamsUnmatched, ArgumentMissing, FuzzyMatchSuccess
from ..typing import AllParam, Empty, MultiArg, BasePattern
from ..base import Args, Option, Subcommand, OptionResult, SubcommandResult, Sentence
from ..util import levenshtein_norm, split_once
from ..config import config


def multi_arg_handler(
    analyser: Analyser,
    may_arg: Union[str, Any],
    key: str,
    value: MultiArg,
    default: Any,
    nargs: int,
    seps: Set[str],
    result_dict: Dict[str, Any]
):
    # 当前args 已经解析 m 个参数， 总共需要 n 个参数，总共剩余p个参数，
    # q = n - m 为剩余需要参数（包括自己）， p - q + 1 为自己可能需要的参数个数
    _m_rest_arg = nargs - len(result_dict) - 1
    _m_all_args_count = analyser.rest_count(seps) - _m_rest_arg + 1
    if value.array_length:
        _m_all_args_count = min(_m_all_args_count, value.array_length)
    analyser.reduce_data(may_arg)
    if value.flag == 'args':
        result = []
        for i in range(_m_all_args_count):
            _m_arg, _m_str = analyser.next_data(seps)
            if _m_str and _m_arg in analyser.param_ids:
                analyser.reduce_data(_m_arg)
                for ii in range(min(len(result), _m_rest_arg)):
                    analyser.reduce_data(result.pop(-1))
                break
            res, s = value.validate(_m_arg)
            if s != 'V':
                analyser.reduce_data(_m_arg)
                break
            result.append(res)
        if len(result) == 0:
            result = [default] if default else []
        result_dict[key] = tuple(result)
    else:
        result = {}

        def __putback(data):
            analyser.reduce_data(data)
            for _ in range(min(len(result), _m_rest_arg)):
                arg = result.popitem()  # type: ignore
                analyser.reduce_data(f'{arg[0]}={arg[1]}')

        for i in range(_m_all_args_count):
            _m_arg, _m_str = analyser.next_data(seps)
            if not _m_str:
                analyser.reduce_data(_m_arg)
                break
            if _m_str and _m_arg in analyser.command_params:
                __putback(_m_arg)
                break
            if _kwarg := re.match(r'^([^=]+)=([^=]+?)$', _m_arg):
                _m_arg = _kwarg.group(2)
                res, s = value.validate(_m_arg)
                if s != 'V':
                    analyser.reduce_data(_m_arg)
                    break
                result[_kwarg.group(1)] = res
            elif _kwarg := re.match(r'^([^=]+)=\s?$', _m_arg):
                _m_arg, _m_str = analyser.next_data(seps)
                res, s = value.validate(_m_arg)
                if s != 'V':
                    __putback(_m_arg)
                    break
                result[_kwarg.group(1)] = res
            else:
                analyser.reduce_data(_m_arg)
                break
        if len(result) == 0:
            result = [default] if default else []
        result_dict[key] = result


def analyse_args(
        analyser: Analyser,
        opt_args: Args,
        nargs: int
) -> Dict[str, Any]:
    """
    分析 Args 部分

    Args:
        analyser: 使用的分析器
        opt_args: 目标Args
        nargs: Args参数个数

    Returns:
        Dict: 解析结果
    """
    option_dict: Dict[str, Any] = {}
    seps = opt_args.separators
    for key, arg in opt_args.argument.items():
        value = arg['value']
        default = arg['default']
        kwonly = arg['kwonly']
        optional = arg['optional']
        may_arg, _str = analyser.next_data(seps)
        if not may_arg or (_str and may_arg in analyser.param_ids):
            analyser.reduce_data(may_arg)
            if default is None:
                if optional:
                    continue
                raise ArgumentMissing(config.lang.args_missing.format(key=key))
            option_dict[key] = None if default is Empty else default
            continue
        if kwonly:
            _kwarg = re.findall(f'^{key}=(.*)$', may_arg)
            if not _kwarg:
                analyser.reduce_data(may_arg)
                if analyser.alconna.is_fuzzy_match and (k := may_arg.split('=')[0]) != may_arg:
                    if levenshtein_norm(k, key) >= config.fuzzy_threshold:
                        raise FuzzyMatchSuccess(config.lang.common_fuzzy_matched.format(source=k, target=key))
                if default is None and analyser.is_raise_exception:
                    raise ParamsUnmatched(config.lang.args_key_missing.format(target=may_arg, key=key))
                option_dict[key] = None if default is Empty else default
                continue
            may_arg = _kwarg[0]
            if may_arg == '':
                may_arg, _str = analyser.next_data(seps)
                if _str:
                    analyser.reduce_data(may_arg)
                    if default is None and analyser.is_raise_exception:
                        raise ParamsUnmatched(config.lang.args_type_error.format(target=may_arg.__class__))
                    option_dict[key] = None if default is Empty else default
                    continue
        if isinstance(value, BasePattern):
            if value.__class__ is MultiArg:
                multi_arg_handler(analyser, may_arg, key, value, default, nargs, seps, option_dict)  # type: ignore
            else:
                res, state = value.validate(may_arg, default) if not value.anti else value.invalidate(may_arg, default)
                if state != "V":
                    analyser.reduce_data(may_arg)
                if state == "E":
                    if optional:
                        continue
                    raise res
                option_dict[key] = res
        elif value is AllParam:
            analyser.reduce_data(may_arg)
            option_dict[key] = analyser.recover_raw_data()
            return option_dict
        elif may_arg == value:
            option_dict[key] = may_arg
        else:
            if default is None:
                if optional:
                    continue
                raise ParamsUnmatched(config.lang.args_error.format(target=may_arg))
            option_dict[key] = None if default is Empty else default
    if opt_args.var_keyword:
        kwargs = option_dict[opt_args.var_keyword]
        if not isinstance(kwargs, dict):
            kwargs = {opt_args.var_keyword: kwargs}
        option_dict['__kwargs__'] = (kwargs, opt_args.var_keyword)
    if opt_args.var_positional:
        varargs = option_dict[opt_args.var_positional]
        if not isinstance(varargs, Iterable):
            varargs = [varargs]
        elif not isinstance(varargs, list):
            varargs = list(varargs)
        option_dict['__varargs__'] = (varargs, opt_args.var_positional)
    return option_dict


def analyse_params(
        analyser: Analyser,
        params: Dict[str, Union[List[Option], Sentence, Subcommand]]
):
    _text, _str = analyser.next_data(analyser.separators, pop=False)
    if not _str:
        return Ellipsis
    if not _text:
        return _text
    if param := params.get(_text, None):
        return param
    for p in params:
        _p = params[p]
        if isinstance(_p, List):
            res = []
            for _o in _p:
                if not _o.is_compact:
                    _may_param, _ = split_once(_text, _o.separators)
                    if _may_param in _o.aliases:
                        res.append(_o)
                        continue
                    if analyser.alconna.is_fuzzy_match and levenshtein_norm(_may_param, p) >= config.fuzzy_threshold:
                        raise FuzzyMatchSuccess(config.lang.common_fuzzy_matched.format(source=_may_param, target=p))
                elif any(map(lambda x: _text.startswith(x), _o.aliases)):
                    res.append(_o)
            if res:
                return res
        else:
            _may_param, _ = split_once(_text, _p.separators)
            if _may_param == _p.name:
                return _p
            if analyser.alconna.is_fuzzy_match and levenshtein_norm(_may_param, p) >= config.fuzzy_threshold:
                raise FuzzyMatchSuccess(config.lang.common_fuzzy_matched.format(source=_may_param, target=p))


def analyse_option(
        analyser: Analyser,
        param: Option,
) -> Tuple[str, OptionResult]:
    """
    分析 Option 部分

    Args:
        analyser: 使用的分析器
        param: 目标Option
    """
    if param.requires:
        if analyser.sentences != param.requires:
            raise ParamsUnmatched(f"{param.name}'s required is not '{' '.join(analyser.sentences)}'")
        analyser.sentences = []
    if param.is_compact:
        name, _ = analyser.next_data()
        for al in param.aliases:
            if name.startswith(al):
                analyser.reduce_data(name.lstrip(al), replace=True)
                break
        else:
            raise ParamsUnmatched(f"{name} dose not matched with {param.name}")
    else:
        name, _ = analyser.next_data(param.separators)
        if name not in param.aliases:  # 先匹配选项名称
            raise ParamsUnmatched(f"{name} dose not matched with {param.name}")
    name = param.dest
    res: OptionResult = {"value": None, "args": {}}
    if param.nargs == 0:
        res['value'] = Ellipsis
    else:
        res['args'] = analyse_args(analyser, param.args, param.nargs)
    return name, res


def analyse_subcommand(
        analyser: Analyser,
        param: Subcommand
) -> Tuple[str, SubcommandResult]:
    """
    分析 Subcommand 部分

    Args:
        analyser: 使用的分析器
        param: 目标Subcommand
    """
    if param.requires:
        if analyser.sentences != param.requires:
            raise ParamsUnmatched(f"{param.name}'s required is not '{' '.join(analyser.sentences)}'")
        analyser.sentences = []
    if param.is_compact:
        name, _ = analyser.next_data()
        if name.startswith(param.name):
            analyser.reduce_data(name.lstrip(param.name), replace=True)
        else:
            raise ParamsUnmatched(f"{name} dose not matched with {param.name}")
    else:
        name, _ = analyser.next_data(param.separators)
        if name != param.name:  # 先匹配选项名称
            raise ParamsUnmatched(f"{name} dose not matched with {param.name}")
    name = param.dest
    res: SubcommandResult = {"value": None, "args": {}, 'options': {}}
    if param.sub_part_len.stop == 0:
        res['value'] = Ellipsis
        return name, res

    args = False
    subcommand = res['options']
    need_args = param.nargs > 0
    for _ in param.sub_part_len:
        sub_param = analyse_params(analyser, param.sub_params)  # type: ignore
        if sub_param and isinstance(sub_param, List):
            for p in sub_param:
                _current_index = analyser.current_index
                _content_index = analyser.content_index
                try:
                    subcommand.setdefault(*analyse_option(analyser, p))
                    break
                except Exception as e:
                    exc = e
                    analyser.current_index = _current_index
                    analyser.content_index = _content_index
                    continue
            else:
                raise exc  # type: ignore  # noqa

        elif not args:
            res['args'] = analyse_args(analyser, param.args, param.nargs)
            args = True
    if need_args and not args:
        raise ArgumentMissing(config.lang.subcommand_args_missing.format(name=name))
    return name, res


def analyse_header(
        analyser: Analyser,
) -> Union[Dict[str, Any], bool, None]:
    """
    分析命令头部

    Args:
        analyser: 使用的分析器
    Returns:
        head_match: 当命令头内写有正则表达式并且匹配成功的话, 返回匹配结果
    """
    command = analyser.command_header
    separators = analyser.separators
    head_text, _str = analyser.next_data(separators)
    if isinstance(command, Pattern) and _str and (_head_find := command.fullmatch(head_text)):
        analyser.head_matched = True
        return _head_find.groupdict() or True
    elif isinstance(command, BasePattern) and (_head_find := command.validate(head_text, Empty)[0]):
        analyser.head_matched = True
        return _head_find or True
    else:
        may_command, _m_str = analyser.next_data(separators)
        if isinstance(command, List) and _m_str and not _str:
            for _command in command:
                if (_head_find := _command[1].fullmatch(may_command)) and head_text == _command[0]:
                    analyser.head_matched = True
                    return _head_find.groupdict() or True
        if isinstance(command, tuple):
            if not _str and (
                (isinstance(command[0], list) and head_text in command[0]) or
                (isinstance(command[0], tuple) and head_text in command[0][0])
            ):
                if isinstance(command[1], Pattern):
                    if _m_str and (_command_find := command[1].fullmatch(may_command)):
                        analyser.head_matched = True
                        return _command_find.groupdict() or True
                elif _command_find := command[1].validate(may_command, Empty)[0]:
                    analyser.head_matched = True
                    return _command_find or True
            elif _str and isinstance(command[0][1], Pattern):
                if not _m_str:
                    if isinstance(command[1], BasePattern) and (_head_find := command[0][1].fullmatch(head_text)) and (
                        _command_find := command[1].validate(may_command, Empty)[0]
                    ):
                        analyser.head_matched = True
                        return _command_find or True
                else:
                    pat = re.compile(command[0][1].pattern + command[1].pattern)  # type: ignore
                    if _head_find := pat.fullmatch(head_text):
                        analyser.reduce_data(may_command)
                        analyser.head_matched = True
                        return _head_find.groupdict() or True
                    elif _command_find := pat.fullmatch(head_text + may_command):
                        analyser.head_matched = True
                        return _command_find.groupdict() or True

    if not analyser.head_matched:
        if _str and analyser.alconna.is_fuzzy_match:
            headers_text = []
            if analyser.alconna.headers and analyser.alconna.headers != [""]:
                for i in analyser.alconna.headers:
                    if isinstance(i, str):
                        headers_text.append(f"{i}{analyser.alconna.command}")
                    else:
                        headers_text.extend((f"{i}", analyser.alconna.command))
            elif analyser.alconna.command:
                headers_text.append(analyser.alconna.command)
            if isinstance(command, (Pattern, BasePattern)):
                source = head_text
            else:
                source = head_text + analyser.separators.copy().pop() + str(may_command)  # type: ignore  # noqa
            if source == analyser.alconna.command:
                analyser.head_matched = False
                raise ParamsUnmatched(config.lang.header_error.format(target=head_text))
            for ht in headers_text:
                if levenshtein_norm(source, ht) >= config.fuzzy_threshold:
                    analyser.head_matched = True
                    raise FuzzyMatchSuccess(config.lang.common_fuzzy_matched.format(target=source, source=ht))
        raise ParamsUnmatched(config.lang.header_error.format(target=head_text))
