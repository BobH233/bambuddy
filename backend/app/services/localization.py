"""Display-only Chinese localization for the lab deployment."""

import json
import os
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_TEXT = json.loads((_DATA / "lab_zh_CN.json").read_text(encoding="utf-8"))["text"]


def chinese_enabled() -> bool:
    return os.environ.get("BAMBUDDY_UI_LANGUAGE", "").lower() in {"zh", "zh-cn", "zh-hans"}


def system_text(value: str) -> str:
    if not chinese_enabled() or not value:
        return value
    if value in _TEXT:
        return _TEXT[value]
    lines = []
    for line in value.split("\n"):
        text = line.strip()
        if text in _TEXT:
            lines.append(line.replace(text, _TEXT[text]))
        elif item := re.fullmatch(r"([-•*]\s*)(.+?)\s*\((Soon|OVERDUE)\)", text):
            lines.append(f"{item[1]}{_TEXT.get(item[2], item[2])}（{_TEXT[item[3]]}）")
        else:
            lines.append(line)
    return "\n".join(lines)


def queue_reason(value: str) -> str:
    if not chinese_enabled():
        return value
    patterns = (
        (r"No matching material/color\. Waiting on (.+)", r"没有匹配的材料或颜色，等待装入：\1"),
        (r"Waiting for filament: (.+)", r"等待耗材：\1"),
        (r"Waiting for plate confirmation: (.+)", r"等待确认打印平台已清空：\1"),
        (r"Waiting on (.+)", r"等待解除阻止条件：\1"),
        (r"Needs (.+)", r"需要耗材：\1"),
        (r"Busy: (.+)", r"打印机忙碌：\1"),
        (r"Offline, no Auto On smart plug: (.+)", r"打印机离线，未配置自动开机插座：\1"),
        (r"Offline: (.+)", r"打印机离线：\1"),
        (r"No available (.+) printers(.*)", r"没有可用的 \1 打印机\2"),
    )
    parts = []
    for part in value.split(" | "):
        for pattern, replacement in patterns:
            if re.fullmatch(pattern, part):
                part = re.sub(pattern, replacement, part)
                part = re.sub(r"\(needs (.+?)\)", r"（需要 \1）", part)
                break
        parts.append(system_text(part))
    return "；".join(parts)


def notification_values(variables: dict) -> None:
    if not chinese_enabled():
        return
    for key in ("items", "reason", "waiting_reason", "error_type", "error_detail", "state", "action"):
        if isinstance(variables.get(key), str):
            variables[key] = queue_reason(variables[key]) if key in {"reason", "waiting_reason"} else system_text(variables[key])
    for key in ("duration", "remaining_time", "estimated_time", "time_to_pause"):
        value = variables.get(key)
        if isinstance(value, str):
            if re.fullmatch(r"(?:\d+(?:\.\d+)?[dhms]\s*)+", value):
                variables[key] = re.sub(r"(\d+(?:\.\d+)?)([dhms])", lambda m: m[1] + {"d": "天", "h": "小时", "m": "分钟", "s": "秒"}[m[2]], value)
            else:
                variables[key] = system_text(value)
