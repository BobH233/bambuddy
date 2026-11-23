import { afterEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import i18n, { i18nReady } from '../../i18n';
import { printerStatusText, queueReasonText, translateSystemText } from '../../utils/displayText';
import { chineseDisplayText } from '../../i18n/labChinese';

afterEach(async () => { await i18n.changeLanguage('en'); });

describe('lab Chinese presentation', () => {
  it('covers every firmware stage without changing unknown firmware codes', () => {
    const source = readFileSync('../backend/app/services/bambu_mqtt.py', 'utf8');
    const stages = source.split('STAGE_NAMES = {')[1].split('\n}')[0];
    const names = [...stages.matchAll(/\d+: "([^"]+)"/g)].map((match) => match[1]);
    expect(names.length).toBeGreaterThan(60);
    for (const name of names) expect(chineseDisplayText[name], name).toMatch(/[\u4e00-\u9fff]/);
    expect(translateSystemText('Unknown stage (999)', 'zh-CN')).toBe('未知阶段（999）');
  });

  it('translates status, maintenance, missing keys and dynamic queue explanations', async () => {
    await i18nReady;
    await i18n.changeLanguage('zh-CN');
    expect(printerStatusText('RUNNING', 'Auto bed leveling')).toBe('自动热床调平');
    expect(printerStatusText('PREPARE', 'Heating heatbed')).toBe('正在加热热床');
    expect(printerStatusText('IDLE')).toBe('空闲');
    expect(translateSystemText('- Lubricate Steel Rods (Soon)\n- Clean Carbon Rods (OVERDUE)'))
      .toBe('- 润滑钢杆（即将到期）\n- 清洁碳纤维杆（已到期）');
    expect(queueReasonText('Busy: Lamps-X2D | Offline: Lamps-P2S')).toBe('打印机忙碌：Lamps-X2D；打印机离线：Lamps-P2S');
    expect(queueReasonText('Waiting for filament: Lamps-P2S (needs PETG)')).toBe('等待耗材：Lamps-P2S （需要 PETG）');
    expect(i18n.t('slice.bedType.texturedPEI')).toBe('纹理 PEI 打印板');
    expect(i18n.t('support.description')).toMatch(/[\u4e00-\u9fff]/);
  });

  it('preserves names, filenames, codes, and the English language option', async () => {
    await i18n.changeLanguage('zh-CN');
    for (const name of ['Lamps-X2D', 'Lamps-P2S', 'PETG-CF', 'My English Model.gcode.3mf', '0500-0300-0002-000E']) {
      expect(translateSystemText(name)).toBe(name);
    }
    expect(translateSystemText('Auto bed leveling', 'en')).toBe('Auto bed leveling');
    expect(translateSystemText('Custom maintenance task', 'zh-CN')).toBe('Custom maintenance task');
  });
});
