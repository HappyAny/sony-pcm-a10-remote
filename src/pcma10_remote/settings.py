"""Recorder setting facts independently mapped from REC Remote's control paths.

Capability descriptors describe supported choices, not current setting values.
Unknown capability IDs are reported for diagnostics and are never writable.
"""
from dataclasses import dataclass


RECORD_MODES = {1: "双格式录音", 2: "LPCM 96 kHz / 24 bit", 3: "LPCM 44.1 kHz / 16 bit",
                4: "MP3 320 kbps", 5: "MP3 192 kbps", 6: "MP3 128 kbps", 7: "MP3 48 kbps（单声道）",
                8: "LPCM 192 kHz / 24 bit", 9: "LPCM 176.4 kHz / 24 bit", 10: "LPCM 96 kHz / 16 bit",
                11: "LPCM 88.2 kHz / 24 bit", 12: "LPCM 88.2 kHz / 16 bit", 13: "LPCM 48 kHz / 24 bit",
                14: "LPCM 48 kHz / 16 bit", 15: "LPCM 44.1 kHz / 24 bit"}
MIC_LEVELS = {1: "人声 · 自动（AGC）", 2: "人声 · 高", 3: "人声 · 中", 4: "人声 · 低",
              5: "音乐 · 高", 6: "音乐 · 中", 7: "音乐 · 低", 8: "手动"}
AUDIO_LEVELS = {1: "高", 2: "中", 3: "低", 4: "手动"}
SCENES = {1: "关闭场景", 2: "自动人声", 3: "会议", 4: "演讲 / 讲座", 5: "语音备忘",
          6: "采访", 7: "乐队", 8: "独唱 / 乐器", 9: "My Scene 1", 10: "My Scene 2", 11: "口袋录音"}
SWITCH = {1: "开启", 2: "关闭"}
INTERVALS = {300: "5 分钟", 600: "10 分钟", 900: "15 分钟", 1800: "30 分钟"}
MARK_TYPES = {1: "普通标记", 2: "重要", 3: "问题", 4: "新话题"}


@dataclass(frozen=True)
class Setting:
    key: str
    item: int
    title: str
    group: str
    choices: dict[int, str]
    hint: str = ""
    # A type-2 descriptor names a control format. It is not a fixed value.
    fixed_type: int | None = None
    kind: str = "enum"


SETTINGS = (
    Setting("scene", 0x0403, "录音场景", "格式与场景", SCENES, "切换场景可能同时改变录音格式、灵敏度和滤波。"),
    Setting("rec-mode", 0x0101, "录音格式", "格式与场景", RECORD_MODES),
    Setting("channels", 0x0104, "声道", "格式与场景", {1: "单声道", 2: "立体声"}),
    Setting("internal-mic", 0x0201, "内置麦克风灵敏度", "输入与电平", MIC_LEVELS, "选“手动”后，可调整录音增益并使用试音。"),
    Setting("external-input", 0x0301, "外部输入类型", "输入与电平", {1: "麦克风输入", 2: "音频 / Line 输入", 3: "电话麦克风"}),
    Setting("external-mic", 0x0202, "外接麦克风灵敏度", "输入与电平", MIC_LEVELS),
    Setting("audio-input", 0x0203, "音频输入灵敏度", "输入与电平", AUDIO_LEVELS),
    Setting("focus-wide", 0x0205, "聚焦 / 宽域录音", "输入与电平", {1: "关闭", 2: "聚焦", 3: "宽域"}, fixed_type=12),
    Setting("rec-filter", 0x0401, "录音滤波", "录音处理", {1: "关闭", 3: "NCF 降噪滤波", 4: "LCF 低切滤波"}),
    Setting("limiter", 0x0402, "限幅器", "录音处理", SWITCH, "是否可用还取决于输入和电平模式。", fixed_type=1),
    Setting("low-cut", 0x0404, "低切频率", "录音处理", {1: "关闭", 2: "75 Hz", 3: "150 Hz"}),
    Setting("high-sn", 0x0405, "高信噪比模式", "录音处理", SWITCH, fixed_type=1),
    Setting("pre-record", 0x0509, "预录音", "自动录音与储存", SWITCH, "待机时缓存录音开始之前的声音。", fixed_type=1),
    Setting("vor", 0x0502, "VOR 声控录音", "自动录音与储存", SWITCH, "根据输入声音自动录音或暂停。", fixed_type=1),
    Setting("sync-record", 0x0503, "同步录音", "自动录音与储存", SWITCH, "用于外部音频输入；设备可能限制试音等功能。", fixed_type=1),
    Setting("cross-memory", 0x0501, "跨存储器录音", "自动录音与储存", SWITCH, "当前存储器用满后切换到另一存储器。", fixed_type=1),
    Setting("auto-mark", 0x0507, "自动添加标记", "自动标记", SWITCH, fixed_type=1),
    Setting("auto-mark-interval", 0x0506, "自动标记间隔", "自动标记", INTERVALS,
            "应用间隔时，按 Sony 的操作顺序同时开启自动标记。", fixed_type=0),
    Setting("auto-mark-time", 0x0505, "标记附加时间信息", "自动标记", SWITCH, fixed_type=13),
    Setting("auto-mark-legacy", 0x0504, "自动标记间隔", "自动标记", {0: "关闭", **INTERVALS}, fixed_type=0),
    Setting("peak-hold", 0x0508, "设备峰值保持", "录音处理", {1: "自动", 2: "手动"}, fixed_type=14),
    Setting("save-scene", 0x0602, "保存当前设置到 My Scene", "自定义场景", {1: "My Scene 1", 2: "My Scene 2"},
            "保存将覆盖所选场景中的旧设置。", kind="action"),
)
BY_KEY = {setting.key: setting for setting in SETTINGS}
BY_ITEM = {setting.item: setting for setting in SETTINGS}


def choices_for(setting, capabilities):
    cap = capabilities.get(setting.item)
    if not cap:
        return {}
    if setting.key == "auto-mark-legacy" and choices_for(BY_KEY["auto-mark"], capabilities):
        return {}
    if setting.key == "auto-mark-interval" and not choices_for(BY_KEY["auto-mark"], capabilities):
        return {}
    if cap["type"] == 1:
        choices = setting.choices
        if setting.key == "limiter":
            choices = {1: "关闭", 2: "150 毫秒", 3: "1 秒", 4: "1 分钟"}
        return {value: choices[value] for value in cap["values"] if value in choices}
    if cap["type"] == 2 and cap["value"] == setting.fixed_type:
        return setting.choices.copy()
    return {}


def catalog(capabilities, values):
    result = []
    for setting in SETTINGS:
        choices = choices_for(setting, capabilities)
        if not choices:
            continue
        current = values.get(setting.item)
        result.append({"key": setting.key, "item": f"{setting.item:04x}", "title": setting.title,
                       "group": setting.group, "kind": setting.kind, "hint": setting.hint,
                       "choices": [{"value": value, "label": label} for value, label in choices.items()],
                       "value": current, "display_value": choices.get(current, "未读取" if current is None else f"设备值 {current}")})
    return result


def features(capabilities):
    return {"rehearsal": capabilities.get(0x0605, {}).get("type") == 1,
            "remote_peak_reset": bool(choices_for(BY_KEY["peak-hold"], capabilities)),
            "save_scene": bool(choices_for(BY_KEY["save-scene"], capabilities))}
