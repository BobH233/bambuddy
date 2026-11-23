import json
from pathlib import Path

import pytest

from backend.app.services.hms_errors import describe_fault, lookup_fault
from backend.app.services.localization import notification_values, system_text


def test_fault_language_retains_model_specific_meaning(monkeypatch):
    monkeypatch.setenv('BAMBUDDY_UI_LANGUAGE', 'zh-CN')
    assert describe_fault('03008004') == '耗材已用尽，请装入新的耗材。'
    assert '用户' in describe_fault('03008001', '20P')
    assert '暂停' in describe_fault('03008001', '093')
    assert describe_fault('03008001', '20P') != describe_fault('03008001', '093')
    assert describe_fault('FFFFFFFFFFFFFFFF') is None
    assert describe_fault('0500060000020070') is None
    assert lookup_fault('03008004') == 'Filament ran out. Please load new filament.'
    monkeypatch.delenv('BAMBUDDY_UI_LANGUAGE')
    assert describe_fault('03008004') == lookup_fault('03008004')


def test_notification_values_translate_only_system_fields(monkeypatch):
    monkeypatch.setenv('BAMBUDDY_UI_LANGUAGE', 'zh-CN')
    values = dict(filename='Clean Carbon Rods', printer='Printing', items='- Lubricate Steel Rods (Soon)',
                  waiting_reason='Busy: Lamps-X2D | Offline: Lamps-P2S', duration='1h 2m')
    notification_values(values)
    assert values['filename'] == 'Clean Carbon Rods'
    assert values['printer'] == 'Printing'
    assert values['items'] == '- 润滑钢杆（即将到期）'
    assert values['duration'] == '1小时 2分钟'
    assert values['waiting_reason'] == '打印机忙碌：Lamps-X2D；打印机离线：Lamps-P2S'
    assert system_text('Custom task') == 'Custom task'


@pytest.mark.parametrize('section', ['hms', 'error'])
def test_known_fault_catalog_has_chinese_coverage(section):
    data = Path(__file__).resolve().parents[3] / 'app' / 'data'
    en = json.loads((data / 'hms_catalog.json').read_text())
    zh = json.loads((data / 'hms_catalog_zh_CN.json').read_text())
    for code, text in en[section].items():
        if text:
            assert zh[section].get(code), code
