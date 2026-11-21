# -*- coding: utf-8 -*-
"""抽取闸门与批量精简共用的结构化可复用性判据。

只按结构特征做保留/剔除判断，不改写文本，也不用语义词表猜测开放语义。
"""

from __future__ import annotations

import re

TEMPLATE_PREFIX = "The assistant "
USER_TEMPLATE_PREFIX = "The user "
PATH_MARKERS = ("/Users/", "/apdcephfs", "~/", ".jsonl", ".log", ".csv", ".md", ".pdf")
# 命中即保留；这是清理规则的豁免，不是脱敏。
PERSONAL_HINT = re.compile(
    r"passport|DS-160|EQ\d{7}|AA00[A-Z0-9]{6}|npm_|Access Tokens?|护照|身份证号|朝阳区|亚运村",
    re.IGNORECASE,
)


def has_personal_identifier(content: str) -> bool:
    return bool(PERSONAL_HINT.search(content))


def is_one_off_profile(content: str) -> bool:
    # 中文短画像（如「用户偏好简体中文」）也是稳定属性，不用长度阈值误杀。
    return any(marker in content for marker in PATH_MARKERS)


def is_template_sentence(content: str) -> bool:
    return content.startswith(TEMPLATE_PREFIX) or content.startswith(USER_TEMPLATE_PREFIX)


def injection_noise(content: str) -> bool:
    """纯播报模板不进提示词，记录本身仍留在库里。"""
    return content.startswith(TEMPLATE_PREFIX)


def is_low_value_candidate(
    content: str, kind: str, durable: bool | None = None
) -> bool:
    """候选生成后、JEV 之前的结构化闸门。

    可复用性由抽取模型的结构化 durable 字段判定，避免用脆弱的文本启发式
    误杀助手提供的具体事实；profile 的路径/日志白名单是纯结构规则。
    """
    if has_personal_identifier(content):
        return False
    if durable is False:
        return True
    if kind == "profile":
        return is_one_off_profile(content)
    return False
