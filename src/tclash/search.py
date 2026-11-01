"""节点搜索：中文名、全拼、首字母、小鹤双拼都能命中。

节点「日本01」可以被这些查询命中：日本 / 01 / riben / riben01 / ribf / rb。

双拼映射照抄 rime 的 `double_pinyin_flypy.schema.yaml` 的 algebra 规则
（zh/ch/sh 落 v/i/u 键，in→b、en→f、uo→o、ing|uai→k、iang|uang→l ……），
所以键位和真正的小鹤输入法一致：`mwgo` → meiguo → 美国、`ribf` → riben → 日本。
"""

from __future__ import annotations

import re
from functools import lru_cache

from pypinyin import Style, pinyin

#: rime 小鹤双拼 algebra 的等价规则，顺序即应用顺序。
#: 替换结果大写，避免后续规则把已经换掉的声母/韵母再匹配一次（和 rime 一致）。
_FLYPY_RULES: tuple[tuple[str, str], ...] = (
    (r"iu$", "Q"),
    (r"(.)ei$", r"\1W"),
    (r"uan$", "R"),
    (r"[uv]e$", "T"),
    (r"un$", "Y"),
    (r"^sh", "U"),
    (r"^ch", "I"),
    (r"^zh", "V"),
    (r"uo$", "O"),
    (r"ie$", "P"),
    (r"i?ong$", "S"),
    (r"ing$|uai$", "K"),
    (r"(.)ai$", r"\1D"),
    (r"(.)en$", r"\1F"),
    (r"(.)eng$", r"\1G"),
    (r"[iu]ang$", "L"),
    (r"(.)ang$", r"\1H"),
    (r"ian$", "M"),
    (r"(.)an$", r"\1J"),
    (r"(.)ou$", r"\1Z"),
    (r"[iu]a$", "X"),
    (r"iao$", "N"),
    (r"(.)ao$", r"\1C"),
    (r"ui$", "V"),
    (r"in$", "B"),
)

#: 零声母音节：小鹤里敲双字母（ang→ah、eng→eg 是压缩写法，其余原样或叠写）
_ZERO_INITIAL = {
    "a": "aa",
    "ai": "ai",
    "an": "an",
    "ang": "ah",
    "ao": "ao",
    "e": "ee",
    "ei": "ei",
    "en": "en",
    "eng": "eg",
    "er": "er",
    "o": "oo",
    "ou": "ou",
}


@lru_cache(maxsize=8192)
def flypy_syllable(syllable: str) -> str:
    """把一个拼音音节压成小鹤双拼的两键（无声母的返回 2~3 键）。"""
    if syllable in _ZERO_INITIAL:
        return _ZERO_INITIAL[syllable]
    text = syllable
    for pattern, repl in _FLYPY_RULES:
        text = re.sub(pattern, repl, text, count=1)
    return text.lower()


def _syllables(text: str) -> list[tuple[str, bool]]:
    """逐字给出 (拼音音节, 是否汉字)。非汉字原样返回，避免被双拼规则改写。"""
    syllables = pinyin(text, style=Style.NORMAL, errors="default")
    if len(syllables) != len(text):
        # 词组模式下长度对不上时退化成逐字处理
        syllables = [pinyin(ch, style=Style.NORMAL, errors="default")[0] for ch in text]
    out: list[tuple[str, bool]] = []
    for char, candidates in zip(text, syllables):
        syllable = candidates[0] if candidates else char
        out.append((syllable, not char.isascii()))
    return out


@lru_cache(maxsize=8192)
def search_keys(text: str) -> tuple[str, ...]:
    """一个名字的可搜索形式：原名、全拼、小鹤双拼、声母首字母。"""
    full: list[str] = []
    fly: list[str] = []
    initials: list[str] = []
    for syllable, hanzi in _syllables(text):
        if hanzi:
            full.append(syllable)
            fly.append(flypy_syllable(syllable))
            initials.append(syllable[:1])
        else:
            full.append(syllable.lower())
            fly.append(syllable.lower())
            initials.append(syllable.lower())
    return (
        text.casefold(),
        "".join(full),
        "".join(fly),
        "".join(initials),
    )


def matches(query: str, text: str) -> bool:
    """查询是否命中：任一可搜索形式里包含查询串即可。"""
    needle = query.strip().casefold()
    if not needle:
        return True
    return any(needle in key for key in search_keys(text))
