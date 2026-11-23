"""Chinese presentation for Bambuddy's structured notification events."""
import re
import json
from pathlib import Path
from string import Formatter

_TEXT = json.loads(Path(__file__).with_name('lab_zh_CN.json').read_text(encoding='utf-8'))['text']


def system_text(value):
    if value in _TEXT:
        return _TEXT[value]
    lines = []
    for line in value.split('\n'):
        text = line.strip()
        if text in _TEXT:
            lines.append(line.replace(text, _TEXT[text]))
        elif item := re.fullmatch(r'([-•*]\s*)(.+?)\s*\((Soon|OVERDUE)\)', text):
            lines.append(f'{item[1]}{_TEXT.get(item[2], item[2])}（{_TEXT[item[3]]}）')
        else:
            lines.append(line)
    return '\n'.join(lines)


def queue_reason(value):
    patterns = (
        (r'No matching material/color\. Waiting on (.+)', r'没有匹配的材料或颜色，等待装入：\1'),
        (r'Waiting for filament: (.+)', r'等待耗材：\1'),
        (r'Waiting for plate confirmation: (.+)', r'等待确认打印平台已清空：\1'),
        (r'Waiting on (.+)', r'等待解除阻止条件：\1'),
        (r'Needs (.+)', r'需要耗材：\1'),
        (r'Busy: (.+)', r'打印机忙碌：\1'),
        (r'Offline, no Auto On smart plug: (.+)', r'打印机离线，未配置自动开机插座：\1'),
        (r'Offline: (.+)', r'打印机离线：\1'),
        (r'No available (.+) printers(.*)', r'没有可用的 \1 打印机\2'),
    )
    parts = []
    for part in value.split(' | '):
        for pattern, replacement in patterns:
            if re.fullmatch(pattern, part):
                part = re.sub(pattern, replacement, part)
                part = re.sub(r'\(needs (.+?)\)', r'（需要 \1）', part)
                break
        parts.append(system_text(part))
    return '；'.join(parts)


TEMPLATES = {
    'print_start': ('开始打印', '打印机：{printer}\n任务：{filename}\n预计耗时：{estimated_time}'),
    'print_complete': ('打印完成', '打印机：{printer}\n任务：{filename}\n打印耗时：{duration}\n耗材用量：{filament_grams} g'),
    'print_failed': ('打印失败', '打印机：{printer}\n任务：{filename}\n已用时间：{duration}\n原因：{reason}'),
    'print_stopped': ('打印已停止', '打印机：{printer}\n任务：{filename}\n已用时间：{duration}'),
    'print_progress': ('打印进度 {progress}%', '打印机：{printer}\n任务：{filename}\n剩余时间：{remaining_time}'),
    'print_confirm_request': ('请确认打印结果', '打印机：{printer}\n任务：{filename}\n确认页面：{confirm_url}'),
    'print_missing_spool_assignment': ('耗材卷未关联', '打印机：{printer}\n打印已开始，但以下槽位尚未关联库存耗材卷：{missing_slots}\n预期耗材：\n{missing_slot_details}'),
    'billing_charge_failed': ('打印费用记账失败', '打印机：{printer}\n任务：{filename}\n费用未能入账，预算预留已保留。\n打印记录：{archive_id}'),
    'printer_offline': ('打印机离线', '打印机：{printer}\n与打印机的连接已断开。'),
    'printer_error': ('打印机告警：{error_type}', '打印机：{printer}\n详情：{error_detail}'),
    'ai_failure_detection': ('AI 检测到疑似打印异常', '打印机：{printer}\n任务：{task_name}\n检测服务：{provider}\n质量评分：{print_quality}\n置信度：{confidence}\n已采取措施：{action}'),
    'plate_not_empty': ('平台有异物，打印已暂停', '打印机：{printer}\n检测到平台上有物体，请清理平台后恢复打印。'),
    'plate_clear_required': ('请清理打印平台', '打印机：{printer}\n打印已结束，请确认平台已清空后继续队列。'),
    'filament_low': ('耗材余量不足', '打印机：{printer}\n槽位：{slot}\n剩余比例：{remaining_percent}%'),
    'maintenance_due': ('打印机维护提醒', '打印机：{printer}\n维护项目：\n{items}'),
    'ams_humidity_high': ('AMS 湿度过高', '打印机：{printer}\n模块：{ams_label}\n当前湿度：{humidity}%\n告警阈值：{threshold}%'),
    'ams_temperature_high': ('AMS 温度过高', '打印机：{printer}\n模块：{ams_label}\n当前温度：{temperature}℃\n告警阈值：{threshold}℃'),
    'ams_drying_suspended': ('AMS 自动烘干已暂停', '打印机：{printer}\n模块：{ams_label}\n已完成 {cycles} 轮烘干，湿度仍为 {humidity}%，高于 {threshold}% 阈值。\nAMS 温热时湿度读数可能偏高，请检查阈值或单独烘干耗材。'),
    'bed_cooled': ('热床已冷却', '打印机：{printer}\n当前热床温度：{bed_temp}℃\n提醒阈值：{threshold}℃'),
    'ha_sensor_alert': ('打印机传感器告警', '打印机：{printer}\n传感器：{sensor}\n状态：{state}'),
    'location_ha_sensor_alert': ('存放位置传感器告警', '位置：{location}\n传感器：{sensor}\n状态：{state}'),
    'first_layer_complete': ('首层打印完成', '打印机：{printer}\n任务：{filename}\n已完成第 1 层，共 {total_layers} 层。'),
    'print_pause_upcoming': ('即将到达预设暂停点', '打印机：{printer}\n任务：{filename}\n距暂停约：{time_to_pause}\n预计时间：{eta}\n暂停层：{layer}/{total_layers}'),
    'queue_job_added': ('任务已加入队列', '任务：{job_name}\n目标：{target}'),
    'queue_job_assigned': ('队列任务已分配', '任务：{job_name}\n打印机：{printer}\n来源：{target_model} 自动分配队列'),
    'queue_job_started': ('队列任务开始打印', '打印机：{printer}\n任务：{job_name}\n预计耗时：{estimated_time}'),
    'queue_job_waiting': ('队列任务等待中', '任务：{job_name}\n等待机型：{target_model}\n原因：{waiting_reason}'),
    'queue_job_skipped': ('队列任务已跳过', '打印机：{printer}\n任务：{job_name}\n原因：{reason}'),
    'queue_job_failed': ('队列任务启动失败', '打印机：{printer}\n任务：{job_name}\n原因：{reason}'),
    'queue_completed': ('打印队列已完成', '队列中的 {completed_count} 个任务均已结束。'),
    'stock_reorder_alert': ('耗材补货提醒：{material}', '耗材：{material} {subtype} {color}（{brand}）\n库存：{stock_g} g\n日均消耗：{rate_g_day} g\n预计可用：{days_left} 天\n已达到补货点，请及时采购。'),
    'stock_break_alert': ('耗材断供风险：{material}', '耗材：{material} {subtype} {color}（{brand}）\n库存：{stock_g} g\n日均消耗：{rate_g_day} g\n采购交期：{lead_time_days} 天\n预计可用：{days_left} 天\n可能在补货到达前用尽，请及时处理。'),
    'test': ('通知连接测试', '这是一条 Bambuddy 通知测试。'),
}
TEMPLATES['ams_ht_humidity_high'] = TEMPLATES['ams_humidity_high']
TEMPLATES['ams_ht_temperature_high'] = TEMPLATES['ams_temperature_high']
EVENT_FIELDS = {field for pair in TEMPLATES.values() for template in pair
                for _, field, _, _ in Formatter().parse(template) if field}
EVENT_FIELDS.add('filament_details')


class Values(dict):
    def __missing__(self, key):
        return '未知'


def localized(event):
    pair = TEMPLATES.get(event.get('event'))
    if not pair:
        # App-authored content and new events retain their original meaning.
        return str(event.get('title') or 'Bambuddy 通知'), str(event.get('message') or '')
    values = Values({k: str(v) if v is not None else '未知' for k, v in event.items()})
    for key in ('items', 'reason', 'waiting_reason', 'error_type', 'error_detail', 'state'):
        if key in values:
            values[key] = queue_reason(values[key]) if key in ('reason', 'waiting_reason') else system_text(values[key])
    for key in EVENT_FIELDS - {'filename', 'job_name', 'printer', 'task_name', 'brand', 'material', 'color', 'subtype'}:
        if values.get(key) in ('Unknown', 'unknown', 'N/A', 'No details available'):
            values[key] = '未知'
    for key in ('duration', 'remaining_time', 'estimated_time', 'time_to_pause'):
        if key in values:
            value = values[key]
            if re.fullmatch(r'(?:\d+(?:\.\d+)?[dhms]\s*)+', value):
                values[key] = re.sub(r'(\d+(?:\.\d+)?)([dhms])',
                                     lambda m: m[1] + {'d': '天', 'h': '小时', 'm': '分钟', 's': '秒'}[m[2]], value)
    if values.get('action') in ('paused', 'pause', 'none', 'notify', 'notification', 'cancelled', 'cancel'):
        values['action'] = {'paused': '已暂停', 'pause': '暂停', 'none': '无', 'notify': '仅通知',
                            'notification': '仅通知', 'cancelled': '已取消', 'cancel': '取消'}[values['action']]
    title, body = (template.format_map(values) for template in pair)
    if event.get('event') == 'print_complete' and event.get('filament_details'):
        body += '\n耗材明细：' + str(event['filament_details'])
    return title, body
