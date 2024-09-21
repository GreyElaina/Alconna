from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from .buffer import Buffer
from .err import OutOfData, ParsePanic, Rejected
from .model.snapshot import AnalyzeSnapshot, OptionTraverse, SubcommandTraverse

T = TypeVar("T")


class LoopflowDescription(str, Enum):
    completed = "completed"

    unsatisfied = "continuation@process#unsatisfied"
    out_of_data_subcommand = "continuation@subcommand#out-of-data"
    out_of_data_option = "continuation@subcommand#out-of-data"

    prefix_expect_str = "panic@prefix-match#expect-str"
    prefix_mismatch = "panic@prefix-match#mismatch"
    header_expect_str = "panic@header-match#expect-str"
    header_mismatch = "panic@header-match#mismatch"
    unsatisfied_switch_subcommand = "panic@subcommand-switch#unsatisfied"
    unexpected_segment = "panic@subcommand-process#unexpected-segment"
    option_duplicated = "panic@option-process#duplicated"
    switch_unsatisfied_option = "panic@option-switch#unsatisfied"
    option_switch_prohibited_direction = "panic@option-switch#prohibited-direction"

    def __str__(self):
        return self.value


@dataclass
class Analyzer(Generic[T]):
    # TODO: 这里可以放一些用于控制 Loopflow 的 options，但如果最后没有的话，就直接单写一个 analyze_loopflow 好了。

    complete_on_determined: bool = True

    def loopflow(self, snapshot: AnalyzeSnapshot[T], buffer: Buffer[T]) -> tuple[LoopflowDescription, AnalyzeSnapshot[T]]:
        while True:
            traverse = snapshot.traverses[-1]
            context = traverse.subcommand
            mix = traverse.mix

            if snapshot.determined and self.complete_on_determined and mix.satisfied:
                return LoopflowDescription.completed, snapshot

            pointer_type, pointer_val = traverse.ref.data[-1]

            try:
                token = buffer.next(traverse.subcommand.separators)
            except OutOfData:
                if mix.satisfied:
                    mix.complete_all()

                    # 在 option context 里面，因为 satisfied 了，所以可以直接返回 completed。
                    # 并且还得确保 option 也被记录于 activated_options 里面。
                    if pointer_type == "option":
                        mix.tracks[pointer_val].complete()
                        option_traverse = traverse.option_traverses[-1]
                        option_traverse.completed = True
                        traverse.ref = traverse.ref.parent

                        if context.options[pointer_val].allow_duplicate:
                            mix.pop_track(pointer_val)

                    snapshot.determine(traverse.ref)
                    return LoopflowDescription.completed, snapshot

                # 这里如果没有 satisfied，如果是 option 的 track，则需要 reset
                if pointer_type == "option":
                    mix.reset(pointer_val)

                return LoopflowDescription.unsatisfied, snapshot

            if pointer_type == "prefix":
                if not isinstance(token.val, str):
                    return LoopflowDescription.header_expect_str, snapshot

                if context.prefixes is not None:
                    matched = context.prefixes.get_closest_prefix(buffer.runes[0])  # type: ignore
                    if matched == "":
                        return LoopflowDescription.prefix_mismatch, snapshot

                    buffer.runes[0:1] = (
                        buffer.runes[0][: len(matched)],
                        buffer.runes[0][len(matched) :],
                    )
                    buffer.next().apply()
                    # FIXME: 现在这里会吃掉很多东西……比如说把第一个 segment 吃的只剩 header.
                    # ahead 不能处理这种情况，详情见下。

                    # 这种方式不优雅，想个好点的。

                traverse.ref = traverse.ref.parent.header()  # 直接进 header.
            elif pointer_type == "header":
                if not isinstance(token.val, str):
                    return LoopflowDescription.header_expect_str, snapshot

                token.apply()

                if token.val == context.header:
                    pass  # do nothing
                elif context.compact_header and token.val.startswith(context.header):
                    # ahead 似乎并不能很好的处理这种情况：这里目的不是为了分「完全已知」的段落，而是分一半 —— 去掉并留置。
                    # 再次重申，ahead 里的所有段落全都是「已经分好」了的。
                    # 在 perfix 和 header 中，我们需要对 buffer 的第一段进行直接替换，而 ahead 倾向于 **已经** 分好段的情况。
                    # ~~而除了 compact subcommand / option 外，对于 header 和 prefix，可以选择直接操作 runes[0]。~~

                    v = token.val[len(context.header) :]
                    if v:
                        buffer.runes.insert(0, v)
                else:
                    return LoopflowDescription.header_mismatch, snapshot

                traverse.ref = traverse.ref.parent
            else:
                if isinstance(token.val, str):
                    if pointer_type == "subcommand":
                        if token.val in context.subcommands:
                            subcommand = context.subcommands[token.val]

                            if mix.satisfied or not subcommand.satisfy_previous:
                                token.apply()
                                mix.complete_all()

                                # context hard switch
                                snapshot.traverses.append(
                                    SubcommandTraverse(
                                        subcommand,
                                        traverse.ref.subcommand(subcommand.header),
                                        subcommand.preset.new_mix(),
                                    )
                                )
                                continue
                            elif not subcommand.soft_keyword:
                                return (
                                    LoopflowDescription.unsatisfied_switch_subcommand,
                                    snapshot,
                                )
                            # else: soft keycmd，直接进 mainline
                        elif token.val in context.options:
                            origin_option = context.options[token.val]

                            # 之前的版本中如果 track 没有 satisfied，就会继续进入 track forward 流程。
                            # 这里有个问题，如果 Fragments 里有个 variadic，那么就会一直进入 forward 流程 —— 这就不太对了，所以我删掉了。
                            if not origin_option.soft_keyword or mix.satisfied:
                                if context.preset.tracks[origin_option.keyword]:
                                    # 仅当需要解析 fragments 时进行状态流转。
                                    traverse.ref = traverse.ref.option(token.val)

                                if not origin_option.allow_duplicate and origin_option.keyword in traverse.option_traverses:
                                    return LoopflowDescription.option_duplicated, snapshot
                                
                                traverse.option_traverses.append(
                                    OptionTraverse(
                                        trigger=token.val,
                                        is_compact=False,
                                        completed=not context.preset.tracks[origin_option.keyword],
                                        option=origin_option,
                                        track=mix.tracks[origin_option.keyword],
                                    )
                                )

                                token.apply()  # 在最后才 apply，因为上面会根据 duplicate 的情况判定 panic，不能一开始就吃掉。
                                continue
                    elif pointer_type == "option":
                        option_traverse = traverse.option_traverses[-1]

                        if token.val in context.subcommands:
                            # 当且仅当 option 已经 satisfied 时才能让状态流转进 subcommand。
                            # subcommand.satisfy_previous 处理起来比较复杂，这里先 reject。

                            # 对于 OptionPattern.allow_duplicate。
                            subcommand = context.subcommands[token.val]
                            option = context.options[pointer_val]
                            track = mix.tracks[option.keyword]

                            if not track.satisfied:
                                if not subcommand.soft_keyword:
                                    mix.reset(option.keyword)
                                    return (
                                        LoopflowDescription.switch_unsatisfied_option,
                                        snapshot,
                                    )
                            else:
                                track.complete()
                                traverse.ref = traverse.ref.parent
                                option_traverse.completed = True

                                if option.allow_duplicate:
                                    mix.pop_track(option.keyword)

                                if mix.satisfied:
                                    token.apply()
                                    mix.complete_all()

                                    # context hard switch
                                    snapshot.traverses.append(
                                        SubcommandTraverse(
                                            subcommand,
                                            traverse.ref.subcommand(token.val),
                                            subcommand.preset.new_mix(),
                                        )
                                    )
                                    continue
                                elif not subcommand.soft_keyword:  # and not phase.satisfied
                                    return (
                                        LoopflowDescription.unsatisfied_switch_subcommand,
                                        snapshot,
                                    )
                                
                        elif token.val in context.options:
                            target_option = context.options[token.val]
                            track = mix.tracks[target_option.keyword]

                            if not track.satisfied:
                                if not target_option.soft_keyword:
                                    mix.reset(target_option.keyword)
                                    return (
                                        LoopflowDescription.option_switch_prohibited_direction,
                                        snapshot,
                                    )
                            else:
                                track.complete()
                                traverse.ref = traverse.ref.parent
                                option_traverse.completed = True

                                if target_option.allow_duplicate:
                                    mix.pop_track(target_option.keyword)

                                continue
                    
                    if context.compacts is not None:
                        prefix = context.compacts.get_closest_prefix(token.val)
                        if prefix:
                            # 这里仍然需要关注 soft_keycmd 和 satisfied 的情况。
                            # 这里有个有趣的点……至少三方因素会参与到这里，所以逻辑关系会稍微复杂那么一点。
                            # 我加了一个 Track.assignable，这样我们就能知道是否还有 fragments 可供分配了。

                            # 我想了想，soft keyword 不会影响这个 —— token.val 根本不是关键字（如果是就不会在这个分支了）。
                            redirect = False

                            if prefix in context.subcommands:
                                # 老样子，需要 satisfied 才能进 subcommand，不然就进 track forward 流程。
                                redirect = mix.satisfied
                            elif prefix in context.options:
                                # NOTE: 这里其实有个有趣的点需要提及：pattern 中的 subcommands, options 和这里的 compacts 都是多对一的关系，
                                # 所以如果要取 track 之类的，就需要先绕个路，因为数据结构上的主索引总是采用的 node 上的单个 keyword。
                                option = context.options[prefix]
                                track = mix.tracks[option.keyword]

                                redirect = track.assignable or option.allow_duplicate
                                # 这也排除了没有 fragments 设定的情况，因为这里 token.val 是形如 "-xxx11112222"，已经传了一个 fragment 进去。

                            # else: 你是不是手动构造了 TrieHard？
                            # 由于默认 redirect 是 False，所以这里不会准许跳转。

                            if redirect:
                                token.apply()
                                prefix_len = len(prefix)
                                buffer.ahead.append(token.val[:prefix_len])
                                buffer.pushleft(token.val[prefix_len:])
                                continue


                if pointer_type == "subcommand":
                    track = mix.tracks[context.header]

                    try:
                        response = track.forward(buffer, context.separators)
                    except OutOfData:
                        # 称不上是 context switch，continuation 不改变 context。
                        return LoopflowDescription.out_of_data_subcommand, snapshot
                    except Rejected:
                        raise
                    except ParsePanic:
                        raise
                    except Exception as e:
                        raise ParsePanic from e
                    else:
                        if response is None:
                            # track 上没有 fragments 可供分配了，此时又没有再流转到其他 traverse
                            return LoopflowDescription.unexpected_segment, snapshot
                elif pointer_type == "option":
                    # option fragments 的处理是原子性的，整段成功才会 apply changes，否则会被 reset。
                    origin_option = context.options[pointer_val]
                    track = mix.tracks[origin_option.keyword]

                    if traverse.option_traverses.count(origin_option.keyword) > 1:
                        rx_getter = traverse.option_traverses[-2].track._assign_getter(
                            track.fragments[0].name
                        )
                        print(traverse.option_traverses[-2])
                    else:
                        rx_getter = None

                    try:
                        response = track.forward(buffer, origin_option.separators, rx_getter)
                    except OutOfData:
                        mix.reset(origin_option.keyword)
                        return LoopflowDescription.out_of_data_option, snapshot
                    except Rejected:
                        raise
                    except ParsePanic:
                        raise
                    except Exception as e:
                        raise ParsePanic from e
                    else:
                        if response is None:
                            # track 上没有 fragments 可供分配了。
                            # 这里没必要 complete。

                            traverse.ref = traverse.ref.parent
                            traverse.option_traverses[-1].completed = True

                            if origin_option.allow_duplicate:
                                mix.pop_track(origin_option.keyword)

                            # TODO: option.receiver 的处理
