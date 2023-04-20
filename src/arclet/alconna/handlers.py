from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable
from tarina import Empty, split_once
from tarina.lang import lang
from nepattern import AllParam, BasePattern
from nepattern.util import TPattern

from .args import Arg, Args
from .header import Double, Header
from .base import Option, Subcommand
from .config import config
from .completion import Prompt, comp_ctx
from .exceptions import ArgumentMissing, FuzzyMatchSuccess, ParamsUnmatched, SpecialOptionTriggered, PauseTriggered
from .model import OptionResult, Sentence, HeadResult
from .output import output_manager
from .typing import KeyWordVar, MultiVar
from .util import levenshtein_norm

if TYPE_CHECKING:
    from .argv import Argv
    from .core import Alconna
    from .analyser import SubAnalyser, Analyser


def _handle_keyword(
    argv: Argv,
    value: KeyWordVar,
    may_arg: Any,
    seps: tuple[str, ...],
    result_dict: dict[str, Any],
    default_val: Any,
    optional: bool,
    key: str | None = None,
    fuzzy: bool = False,
):
    if _kwarg := re.match(fr'^([^{value.sep}]+){value.sep}(.*?)$', may_arg):
        key = key or _kwarg[1]
        if (_key := _kwarg[1]) != key:
            argv.pushback(may_arg)
            if fuzzy and levenshtein_norm(_key, key) >= config.fuzzy_threshold:
                raise FuzzyMatchSuccess(lang.common.fuzzy_matched.format(source=_key, target=key))
            if default_val is None:
                raise ParamsUnmatched(lang.common.fuzzy_matched.format(source=_key, target=key))
            result_dict[_key] = None if default_val is Empty else default_val
            return
        if not (_m_arg := _kwarg[2]):
            _m_arg, _ = argv.popitem(seps)
        res = value.base(_m_arg, default_val)
        if res.flag != 'valid':
            argv.pushback(may_arg)
        if res.flag == 'error':
            if optional:
                return
            raise ParamsUnmatched(*res.error.args)
        result_dict[_kwarg[1]] = res._value  # type: ignore
        return
    argv.pushback(may_arg)
    raise ParamsUnmatched(lang.args.key_missing.format(target=may_arg, key=key))


def _loop_kw(
    argv: Argv,
    _loop: int,
    seps: tuple[str, ...],
    value: MultiVar,
    default: Any
):
    result = {}
    for _ in range(_loop):
        _m_arg, _m_str = argv.popitem(seps)
        if not _m_arg:
            continue
        if _m_str and _m_arg in argv.param_ids:
            argv.pushback(_m_arg)
            break
        try:
            _handle_keyword(argv, value.base, _m_arg, seps, result, default, False)  # type: ignore
        except ParamsUnmatched:
            break
    if not result:
        if value.flag == '+':
            raise ParamsUnmatched
        result = [default] if default else []
    return result


def _loop(
    argv: Argv,
    _loop: int,
    seps: tuple[str, ...],
    value: MultiVar,
    default: Any,
    args: Args
):
    kw = args[args.var_keyword].value.base if args.var_keyword else None
    result = []
    for _ in range(_loop):
        _m_arg, _m_str = argv.popitem(seps)
        if not _m_arg:
            continue
        if _m_str and (
            _m_arg in argv.param_ids or
            (kw and re.match(fr'^([^{kw.sep}]+){kw.sep}(.*?)$', _m_arg))
        ):
            argv.pushback(_m_arg)
            break
        if (res := value.base(_m_arg)).flag != 'valid':
            argv.pushback(_m_arg)
            break
        result.append(res._value)  # type: ignore
    if not result:
        if value.flag == '+':
            raise ParamsUnmatched
        result = [default] if default else []
    return tuple(result)


def multi_arg_handler(
    argv: Argv,
    args: Args,
    arg: Arg,
    result_dict: dict[str, Any],
):
    seps = arg.separators
    value = arg.value
    key = arg.name
    default = arg.field.default_gen
    kw = value.base.__class__ == KeyWordVar
    _m_rest_arg = len(args) - len(result_dict)
    _m_rest_all_param_count = len(argv.release(seps))
    if not kw and not args.var_keyword or kw and not args.var_positional:
        loop = _m_rest_all_param_count - _m_rest_arg + 1
    elif not kw:
        loop = _m_rest_all_param_count - (_m_rest_arg - 2*(args[args.var_keyword].value.flag == "*"))
    else:
        loop = _m_rest_all_param_count - (_m_rest_arg - 2*(args[args.var_positional].value.flag == "*"))
    if value.length > 0:
        loop = min(loop, value.length)
    result_dict[key] = (
        _loop_kw(argv, loop, seps, value, default) if kw
        else _loop(argv, loop, seps, value, default, args)
    )


def analyse_args(argv: Argv, args: Args) -> dict[str, Any]:
    """
    分析 Args 部分

    Args:
        argv: 使用的分析器
        args: 目标Args

    Returns:
        Dict: 解析结果
    """
    result: dict[str, Any] = {}
    for arg in args.argument:
        argv.context = arg
        key = arg.name
        value = arg.value
        default_val = arg.field.default_gen
        optional = arg.optional
        seps = arg.separators
        may_arg, _str = argv.popitem(seps)
        if _str and may_arg in argv.special:
            raise SpecialOptionTriggered(argv.special[may_arg])
        if not may_arg or (_str and may_arg in argv.param_ids):
            argv.pushback(may_arg)
            if default_val is not None:
                result[key] = None if default_val is Empty else default_val
            elif value.__class__ is MultiVar and value.flag == '*':
                result[key] = ()
            elif not optional:
                raise ArgumentMissing(lang.args.missing.format(key=key))
            continue
        if value.__class__ is MultiVar:
            argv.pushback(may_arg)
            multi_arg_handler(argv, args, arg, result)  # type: ignore
        elif value.__class__ is KeyWordVar:
            _handle_keyword(
                argv, value, may_arg, seps, result, default_val, optional, key, analyser.fuzzy_match  # type: ignore
            )
        elif value is AllParam:
            argv.pushback(may_arg)
            result[key] = argv.release(seps)
            argv.current_index = argv.ndata
            return result
        else:
            res = (
                value.invalidate(may_arg, default_val)
                if value.anti
                else value.validate(may_arg, default_val)
            )
            if res.flag != 'valid':
                argv.pushback(may_arg)
            if res.flag == 'error':
                if optional:
                    continue
                raise ParamsUnmatched(*res.error.args)
            if not arg.anonymous:
                result[key] = res._value  # type: ignore
    if args.var_keyword:
        kwargs = result[args.var_keyword]
        if not isinstance(kwargs, dict):
            kwargs = {args.var_keyword: kwargs}
        result['$kwargs'] = (kwargs, args.var_keyword)
    if args.var_positional:
        varargs = result[args.var_positional]
        if not isinstance(varargs, Iterable):
            varargs = [varargs]
        elif not isinstance(varargs, list):
            varargs = list(varargs)
        result['$varargs'] = (varargs, args.var_positional)
    if args.keyword_only:
        result['$kwonly'] = {k: v for k, v in result.items() if k in args.keyword_only}
    argv.context = None
    return result


def analyse_unmatch_params(analyser: SubAnalyser, argv: Argv, text: str):
    for _p in analyser.compile_params.values():
        if isinstance(_p, list):
            res = []
            for _o in _p:
                _may_param, _ = split_once(text, _o.separators)
                if _may_param in _o.aliases or any(map(_may_param.startswith, _o.aliases)):
                    analyser.compile_params.setdefault(text, res)
                    res.append(_o)
                    continue
                if argv.fuzzy_match and levenshtein_norm(_may_param, _o.name) >= config.fuzzy_threshold:
                    raise FuzzyMatchSuccess(lang.common.fuzzy_matched.format(source=_may_param, target=_o.name))
            if res:
                return res
        elif isinstance(_p, Sentence):
            if (_may_param := split_once(text, _p.separators)[0]) == _p.name:
                analyser.compile_params.setdefault(text, _p)
                return _p
            if argv.fuzzy_match and levenshtein_norm(_may_param, _p.name) >= config.fuzzy_threshold:
                raise FuzzyMatchSuccess(lang.common.fuzzy_matched.format(source=_may_param, target=_p.name))
        else:
            _may_param, _ = split_once(text, _p.command.separators)
            if _may_param == _p.command.name or _may_param.startswith(_p.command.name):
                analyser.compile_params.setdefault(text, _p)
                return _p
            if argv.fuzzy_match and levenshtein_norm(_may_param, _p.command.name) >= config.fuzzy_threshold:
                raise FuzzyMatchSuccess(lang.common.fuzzy_matched.format(source=_may_param, target=_p.command.name))


def analyse_option(analyser: SubAnalyser, argv: Argv, param: Option) -> tuple[str, OptionResult]:
    """
    分析 Option 部分

    Args:
        analyser: 使用的分析器
        param: 目标Option
    """
    argv.context = param
    if param.requires and analyser.sentences != param.requires:
        raise ParamsUnmatched(f"{param.name}'s required is not '{' '.join(analyser.sentences)}'")
    analyser.sentences = []
    if param.is_compact:
        name, _ = argv.popitem()
        for al in param.aliases:
            if mat := re.fullmatch(f"{al}(?P<rest>.*?)", name):
                argv.pushback(mat.groupdict()['rest'], replace=True)
                break
        else:
            raise ParamsUnmatched(f"{name} dose not matched with {param.name}")
    else:
        name, _ = argv.popitem(param.separators)
        if name not in param.aliases:  # 先匹配选项名称
            raise ParamsUnmatched(f"{name} dose not matched with {param.name}")
    name = param.dest
    if param.nargs == 0:
        return name, OptionResult()
    return name, OptionResult(None, analyse_args(argv, param.args))


def analyse_param(analyser: SubAnalyser, argv: Argv, _text: Any, _str: bool):
    if _str and _text in argv.special:
        if _text in argv.completion_names:
            if argv.current_index < argv.ndata:
                argv.bak_data = argv.bak_data[:argv.current_index+1]
            last = argv.bak_data[-1]
            argv.bak_data[-1] = last[:last.rfind(_text)]
        raise SpecialOptionTriggered(argv.special[_text])
    if not _str or not _text:
        _param = None
    elif _text in analyser.compile_params:
        _param = analyser.compile_params[_text]
    else:
        _param = None if analyser.default_separate else analyse_unmatch_params(analyser, argv, _text)
    if not _param and not analyser.args_result:
        analyser.args_result = analyse_args(argv, analyser.self_args)
    elif isinstance(_param, list):
        for opt in _param:
            _data, _index = argv.data_set()
            try:
                opt_n, opt_v = analyse_option(analyser, argv, opt)
                analyser.options_result[opt_n] = opt_v
                _data.clear()
                break
            except Exception as e:
                exc = e
                argv.data_reset(_data, _index)
                continue
        else:
            raise exc  # type: ignore  # noqa
    elif isinstance(_param, Sentence):
        analyser.sentences.append(argv.popitem()[0])
    elif _param:
        _param.process(argv)
        analyser.subcommands_result.setdefault(_param.command.dest, _param.result())
    argv.context = None


def analyse_header(header: Header, argv: Argv, command: Alconna) -> HeadResult:
    content = header.content
    mapping = header.mapping
    head_text, _str = argv.popitem()
    if isinstance(content, TPattern) and _str and (mat := content.fullmatch(head_text)):
        return HeadResult(head_text, head_text, True, mat.groupdict(), mapping)
    elif isinstance(content, BasePattern) and (val := content(head_text, Empty)).success:
        return HeadResult(head_text, val.value, True, fixes=mapping)

    may_command, _m_str = argv.popitem()
    if isinstance(content, list) and _m_str:
        for pair in content:
            if res := pair.match(head_text, may_command):
                return HeadResult(*res, fixes=mapping)
    if isinstance(content, Double) and (
        res := content.match(head_text, may_command, _str, _m_str, argv.pushback)
    ):
        return HeadResult(*res, fixes=mapping)

    if _str and argv.fuzzy_match:
        headers_text = []
        if command.headers and command.headers != [""]:
            headers_text.extend(f"{i}{command.command}" for i in command.headers)
        elif command.command:
            headers_text.append(str(command.command))
        if isinstance(content, (TPattern, BasePattern)):
            source = head_text
        else:
            source = head_text + argv.separators[0] + str(may_command)
        if source == command.command:
            raise ParamsUnmatched(lang.header.error.format(target=head_text))
        for ht in headers_text:
            if levenshtein_norm(source, ht) >= config.fuzzy_threshold:
                raise FuzzyMatchSuccess(lang.common.fuzzy_matched.format(target=source, source=ht))
    raise ParamsUnmatched(lang.header.error.format(target=head_text))


def handle_help(analyser: Analyser, argv: Argv):
    _help_param = [str(i) for i in argv.release(recover=True) if str(i) not in argv.special]
    output_manager.send(
        analyser.command.name,
        lambda: analyser.command.formatter.format_node(_help_param),
    )
    return analyser.export(argv, True)


_args = Args["delete;?", "delete"]["name", str]["command", str, "_"]


def handle_shortcut(analyser: Analyser, argv: Argv):
    argv.popitem()
    opt_v = analyse_args(argv, _args)
    try:
        msg = analyser.command.shortcut(
            opt_v["name"],
            None if opt_v["command"] == "_" else {"command": analyser.converter(opt_v["command"])},
            bool(opt_v.get("delete"))
        )
        output_manager.send(analyser.command.name, lambda: msg)
    except Exception as e:
        output_manager.send(analyser.command.name, lambda: str(e))
    return analyser.export(argv, True)


def _prompt_unit(argv: Argv, trigger: Arg):
    if trigger.field.completion:
        comp = trigger.field.completion()
        if isinstance(comp, str):
            return [Prompt(comp, False)]
        releases = argv.release(recover=True)
        target = str(releases[-1]) or str(releases[-2])
        o = list(filter(lambda x: target in x, comp)) or comp
        return [Prompt(i, False, target) for i in o]
    default = trigger.field.default_gen
    o = f"[{trigger.name}]{trigger.value}{'' if default is None else f' default:({None if default is Empty else default})'}"
    return [Prompt(o, False)]


def _prompt_sentence(analyser: Analyser):
    res: list[str] = []
    s_len = len(stc := analyser.sentences)
    for opt in filter(
        lambda x: len(x.requires) >= s_len and x.requires[s_len - 1] == stc[-1],
        analyser.command.options,
    ):
        if len(opt.requires) > s_len:
            res.append(opt.requires[s_len])
        else:
            res.extend(opt.aliases if isinstance(opt, Option) else [opt.name])
    return [Prompt(i) for i in res]


def _prompt_none(analyser: Analyser, argv: Argv, got: list[str]):
    res: list[Prompt] = []
    if not analyser.args_result and analyser.self_args.argument:
        unit = analyser.self_args.argument[0]
        if gen := unit.field.completion:
            res.extend([Prompt(comp, False)] if isinstance(comp := gen(), str) else [Prompt(i, False) for i in comp])
        else:
            default = unit.field.default_gen
            res.append(
                Prompt(
                    f"[{unit.name}]{unit.value}{'' if default is None else f' ({None if default is Empty else default})'}",
                    False
                )
            )
    for opt in filter(
        lambda x: x.name not in argv.completion_names,
        analyser.command.options,
    ):
        if opt.requires and all(opt.requires[0] not in i for i in got):
            res.append(Prompt(opt.requires[0]))
        elif opt.dest not in got:
            res.extend([Prompt(al) for al in opt.aliases] if isinstance(opt, Option) else [Prompt(opt.name)])
    return res


def prompt(analyser: Analyser, argv: Argv, trigger: str | None = None):
    trigger = trigger or argv.context
    got = [*analyser.options_result.keys(), *analyser.subcommands_result.keys(), *analyser.sentences]
    if isinstance(trigger, Arg):
        return _prompt_unit(argv, trigger)
    elif isinstance(trigger, Subcommand):
        return [Prompt(i) for i in analyser.get_sub_analyser(trigger).compile_params]
    elif isinstance(trigger, str):
        res = list(filter(lambda x: trigger in x, analyser.compile_params))
        if not res:
            return []
        out = [i for i in res if i not in got]
        return [Prompt(i, True, trigger) for i in (out or res)]
    releases = argv.release(recover=True)
    target = str(releases[-1]) or str(releases[-2])
    if _res := list(filter(lambda x: target in x and target != x, analyser.compile_params)):
        out = [i for i in _res if i not in got]
        return [Prompt(i, True, target) for i in (out or _res)]
    return _prompt_sentence(analyser) if analyser.sentences else _prompt_none(analyser, argv, got)


def handle_completion(analyser: Analyser, argv: Argv, trigger: str | None = None):
    if res := prompt(analyser, argv, trigger):
        if comp_ctx.get(None):
            raise PauseTriggered(res)
        output_manager.send(
            analyser.command.name,
            lambda: f"{lang.common.completion_node}\n* " + "\n* ".join([i.text for i in res]),
        )
    return analyser.export(fail=True, exception='NoneType: None\n')  # type: ignore
