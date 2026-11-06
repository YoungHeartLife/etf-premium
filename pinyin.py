#!/usr/bin/env python3
"""轻量拼音转换：只覆盖样本池中出现的汉字，零第三方依赖。

用于搜索：support 全拼（nazhikeji）与首字母（nzkj）。
"""
from __future__ import annotations

PINYIN = {
    "零": "ling", "一": "yi", "二": "er", "五": "wu",
    "万": "wan", "业": "ye", "东": "dong", "中": "zhong", "互": "hu", "亚": "ya",
    "企": "qi", "克": "ke", "兴": "xing", "创": "chuang", "利": "li", "医": "yi",
    "华": "hua", "南": "nan", "博": "bo", "发": "fa", "商": "shang", "嘉": "jia",
    "国": "guo", "夏": "xia", "大": "da", "天": "tian", "太": "tai", "央": "yang",
    "安": "an", "实": "shi", "家": "jia", "富": "fu", "工": "gong", "巴": "ba",
    "广": "guang", "弘": "hong", "德": "de", "恒": "heng", "成": "cheng", "技": "ji",
    "招": "zhao", "指": "zhi", "摩": "mo", "数": "shu", "斯": "si", "新": "xin",
    "方": "fang", "日": "ri", "时": "shi", "易": "yi", "普": "pu", "景": "jing",
    "本": "ben", "柏": "bai", "标": "biao", "根": "gen", "概": "gai", "气": "qi",
    "汇": "hui", "沙": "sha", "油": "you", "法": "fa", "泰": "tai", "洲": "zhou",
    "消": "xiao", "添": "tian", "港": "gang", "物": "wu", "特": "te", "琼": "qiong",
    "瑞": "rui", "生": "sheng", "疗": "liao", "科": "ke", "精": "jing", "红": "hong",
    "纳": "na", "经": "jing", "网": "wang", "美": "mei", "联": "lian", "股": "gu",
    "药": "yao", "融": "rong", "西": "xi", "证": "zheng", "费": "fei", "达": "da",
    "选": "xuan", "通": "tong", "道": "dao", "金": "jin", "银": "yin", "顺": "shun",
    "香": "xiang", "鹏": "peng",
    # 区域/指数常见补充字
    "欧": "ou", "印": "yin", "度": "du", "越": "yue", "韩": "han", "台": "tai",
    "俄": "e", "澳": "ao", "加": "jia", "英": "ying", "国": "guo", "地": "di",
    "产": "chan", "消": "xiao", "金": "jin", "融": "rong",
    # 基金简称/全称中的补充字
    "交": "jiao", "京": "jing", "人": "ren", "伯": "bo", "低": "di", "保": "bao",
    "值": "zhi", "健": "jian", "元": "yuan", "全": "quan", "兰": "lan", "内": "nei",
    "准": "zhun", "务": "wu", "动": "dong", "均": "jun", "外": "wai", "尔": "er",
    "币": "bi", "市": "shi", "平": "ping", "息": "xi", "所": "suo", "投": "tou",
    "拉": "la", "有": "you", "服": "fu", "权": "quan", "民": "min", "泛": "fan",
    "波": "bo", "海": "hai", "球": "qiu", "碳": "tan", "福": "fu", "行": "xing",
    "资": "zi", "阿": "a", "高": "gao",
}


def full_pinyin(text: str) -> str:
    """汉字转全拼，保留 ASCII 字母数字，忽略标点与未知字符。"""
    parts = []
    for ch in (text or ""):
        if ch in PINYIN:
            parts.append(PINYIN[ch])
        elif ch.isascii() and ch.isalnum():
            parts.append(ch.lower())
    return "".join(parts)


def initials(text: str) -> str:
    """汉字取拼音首字母，保留 ASCII 字母数字。"""
    parts = []
    for ch in (text or ""):
        if ch in PINYIN:
            parts.append(PINYIN[ch][0])
        elif ch.isascii() and ch.isalnum():
            parts.append(ch.lower())
    return "".join(parts)
