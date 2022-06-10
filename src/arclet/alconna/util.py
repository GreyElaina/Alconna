"""杂物堆"""
import contextlib
import random

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import TypeVar, Optional, Dict, Any, Iterator, Generic, Hashable, Tuple, Set, Union, get_origin, get_args

R = TypeVar('R')


class Singleton(type):
    """单例模式"""
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

    @classmethod
    def remove(mcs, cls):
        mcs._instances.pop(cls, None)


def split_once(text: str, separates: Union[str, Set[str]]):  # 相当于另类的pop, 不会改变本来的字符串
    """单次分隔字符串"""
    out_text = ""
    quotation = ""
    is_split = True
    separates = separates if isinstance(separates, set) else {separates}
    for char in text:
        if char in {"'", '"'}:  # 遇到引号括起来的部分跳过分隔
            if not quotation:
                is_split = False
                quotation = char
            elif char == quotation:
                is_split = True
                quotation = ""
        if char in separates and is_split:
            break
        out_text += char
    result = "".join(out_text)
    return result, text[len(result) + 1:]


def split(text: str, separates: Optional[Set[str]] = None):
    """尊重引号与转义的字符串切分

    Args:
        text (str): 要切割的字符串
        separates (Set(str)): 切割符. 默认为 " ".

    Returns:
        List[str]: 切割后的字符串, 可能含有空格
    """
    separates = separates or {" "}
    result = []
    quotation = ""
    cache = ""
    for index, char in enumerate(text):
        if char in {"'", '"'}:
            if not quotation:
                quotation = char
                if index and text[index - 1] == "\\":
                    cache += char
            elif char == quotation:
                quotation = ""
                if index and text[index - 1] == "\\":
                    cache += char
        elif char in {"\n", "\r"} or (not quotation and char in separates and cache):
            result.append(cache)
            cache = ""
        elif char != "\\" and (char not in separates or quotation):
            cache += char
    if cache:
        result.append(cache)
    return result


def levenshtein_norm(source: str, target: str) -> float:
    """编辑距离算法, 计算源字符串与目标字符串的相似度, 取值范围[0, 1], 值越大越相似"""
    l_s, l_t = len(source), len(target)
    s_range, t_range = range(l_s + 1), range(l_t + 1)
    matrix = [[(i if j == 0 else j) for j in t_range] for i in s_range]

    for i in s_range[1:]:
        for j in t_range[1:]:
            sub_distance = matrix[i - 1][j - 1] + (0 if source[i - 1] == target[j - 1] else 1)
            matrix[i][j] = min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, sub_distance)

    return 1 - float(matrix[l_s][l_t]) / max(l_s, l_t)


_K = TypeVar("_K", bound=Hashable)
_V = TypeVar("_V")


class LruCache(Generic[_K, _V]):
    max_size: int
    cache: OrderedDict
    __size: int
    record: Dict[_K, Tuple[datetime, timedelta]]

    __slots__ = ("max_size", "cache", "record", "__size")

    def __init__(self, max_size: int = -1) -> None:
        self.max_size = max_size
        self.cache = OrderedDict()
        self.record = {}
        self.__size = 0

    def __getitem__(self, key: _K) -> _V:
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        raise KeyError(key)

    def get(self, key: _K, default: Any = None) -> _V:
        try:
            return self[key]
        except KeyError:
            return default

    def query_time(self, key: _K) -> datetime:
        if key in self.cache:
            return self.record[key][0]
        raise KeyError(key)

    def set(self, key: _K, value: Any, expiration: int = 0) -> None:
        if key in self.cache:
            return
        self.cache[key] = value
        self.__size += 1
        if 0 < self.max_size < self.__size:
            _k = self.cache.popitem(last=False)[0]
            self.record.pop(_k)
            self.__size -= 1
        self.record[key] = (datetime.now(), timedelta(seconds=expiration))

    def delete(self, key: _K) -> None:
        if key not in self.cache:
            raise KeyError(key)
        self.cache.pop(key)
        self.record.pop(key)

    def size(self) -> int:
        return self.__size

    def has(self, key: _K) -> bool:
        return key in self.cache

    def clear(self) -> None:
        self.cache.clear()
        self.record.clear()

    def __len__(self) -> int:
        return len(self.cache)

    def __contains__(self, key: _K) -> bool:
        return key in self.cache

    def __iter__(self) -> Iterator[_K]:
        return iter(self.cache)

    def __repr__(self) -> str:
        return repr(self.cache)

    def update(self) -> None:
        now = datetime.now()
        key = random.choice(list(self.cache.keys()))
        expire = self.record[key][1]
        if expire.total_seconds() > 0 and now > self.record[key][0] + expire:
            self.delete(key)

    def update_all(self) -> None:
        now = datetime.now()
        for key in self.cache.keys():
            expire = self.record[key][1]
            if expire.total_seconds() > 0 and now > self.record[key][0] + expire:
                self.delete(key)

    @property
    def recent(self) -> Optional[_V]:
        with contextlib.suppress(KeyError):
            return self.cache[list(self.cache.keys())[-1]]
        return None

    def items(self, size: int = -1) -> Iterator[Tuple[_K, _V]]:
        if size > 0:
            with contextlib.suppress(IndexError):
                return iter(list(self.cache.items())[:-size:-1])
        return iter(self.cache.items())


def generic_issubclass(cls: type, par: Union[type, Any, Tuple[type, ...]]) -> bool:
    """
    检查 cls 是否是 par 中的一个子类, 支持泛型, Any, Union, GenericAlias
    """
    if par is Any:
        return True
    with contextlib.suppress(TypeError):
        if isinstance(par, (type, tuple)):
            return issubclass(cls, par)
        if issubclass(cls, get_origin(par)):  # type: ignore
            return True
        if get_origin(par) is Union:
            return any(generic_issubclass(cls, p) for p in get_args(par))
        if isinstance(par, TypeVar):
            if par.__constraints__:
                return any(generic_issubclass(cls, p) for p in par.__constraints__)
            if par.__bound__:
                return generic_issubclass(cls, par.__bound__)
    return False


def generic_isinstance(obj: Any, par: Union[type, Any, Tuple[type, ...]]) -> bool:
    """
    检查 obj 是否是 par 中的一个类型, 支持泛型, Any, Union, GenericAlias
    """
    if par is Any:
        return True
    with contextlib.suppress(TypeError):
        if isinstance(par, (type, tuple)):
            return isinstance(obj, par)
        if isinstance(obj, get_origin(par)):   # type: ignore
            return True
        if get_origin(par) is Union:
            return any(generic_isinstance(obj, p) for p in get_args(par))
        if isinstance(par, TypeVar):
            if par.__constraints__:
                return any(generic_isinstance(obj, p) for p in par.__constraints__)
            if par.__bound__:
                return generic_isinstance(obj, par.__bound__)
    return False
