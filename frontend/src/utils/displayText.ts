import i18n from '../i18n';
import { chineseDisplayText } from '../i18n/labChinese';

export function translateSystemText(value: string | null | undefined, language = i18n.resolvedLanguage || i18n.language): string {
  if (!value) return '';
  if (!/^zh(?:-CN|-Hans)?$/i.test(language || '')) return value;
  if (chineseDisplayText[value]) return chineseDisplayText[value];
  return value.split(/\r?\n/).map((line) => {
    const trimmed = line.trim();
    if (chineseDisplayText[trimmed]) return line.replace(trimmed, chineseDisplayText[trimmed]);
    const item = trimmed.match(/^([-•*]\s*)(.+?)\s*\((Soon|OVERDUE)\)$/);
    if (item) return `${item[1]}${chineseDisplayText[item[2]] || item[2]}（${chineseDisplayText[item[3]]}）`;
    const stage = trimmed.match(/^Unknown stage \((.+)\)$/);
    if (stage) return `未知阶段（${stage[1]}）`;
    const duration = trimmed.match(/^(\d+(?:\.\d+)?)(mo|[mhdw])$/);
    if (duration) return duration[1] + ({ m: '分钟', h: '小时', d: '天', w: '周', mo: '个月' }[duration[2]] || duration[2]);
    return line;
  }).join('\n');
}

export function queueReasonText(value: string | null | undefined): string {
  if (!value) return '';
  if (!/^zh(?:-CN|-Hans)?$/i.test(i18n.resolvedLanguage || i18n.language || '')) return value;
  const patterns: [RegExp, string][] = [
    [/^No matching material\/color\. Waiting on (.+)$/, '没有匹配的材料或颜色，等待装入：$1'],
    [/^Waiting for filament: (.+)$/, '等待耗材：$1'],
    [/^Waiting for plate confirmation: (.+)$/, '等待确认打印平台已清空：$1'],
    [/^Waiting on (.+)$/, '等待解除阻止条件：$1'],
    [/^Needs (.+)$/, '需要耗材：$1'],
    [/^Busy: (.+)$/, '打印机忙碌：$1'],
    [/^Offline, no Auto On smart plug: (.+)$/, '打印机离线，未配置自动开机插座：$1'],
    [/^Offline: (.+)$/, '打印机离线：$1'],
    [/^No available (.+) printers(.*)$/, '没有可用的 $1 打印机$2'],
  ];
  return value.split(' | ').map((part) => {
    for (const [pattern, replacement] of patterns) {
      if (pattern.test(part)) return part.replace(pattern, replacement).replace(/\(needs (.+?)\)/g, '（需要 $1）');
    }
    return translateSystemText(part);
  }).join('；');
}

const STATE_NAMES: Record<string, string> = {
  RUNNING: 'Printing', PRINTING: 'Printing', PREPARE: 'Preparing', PREPARING: 'Preparing',
  PAUSE: 'Paused', PAUSED: 'Paused', FINISH: 'Finished', COMPLETED: 'Finished',
  FAILED: 'Failed', IDLE: 'Idle', OFFLINE: 'Offline', CONNECTING: 'Connecting',
};

export function printerStatusText(state: string | null | undefined, stage?: string | null): string {
  if (stage) return translateSystemText(stage);
  return translateSystemText(STATE_NAMES[state || 'IDLE'] || state || 'Idle');
}
