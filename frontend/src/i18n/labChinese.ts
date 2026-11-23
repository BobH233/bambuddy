import type { ResourceLanguage } from 'i18next';
import dictionary from './lab-zh-CN.json';

export const chineseDisplayText: Record<string, string> = dictionary.text;

export function withLabChinese(resource: ResourceLanguage): ResourceLanguage {
  const result = structuredClone(resource);
  for (const [path, value] of Object.entries(dictionary.keys)) {
    const segments = path.split('.');
    let current = result;
    for (const segment of segments.slice(0, -1)) {
      const child = current[segment];
      current[segment] = child && typeof child === 'object' ? child : {};
      current = current[segment] as ResourceLanguage;
    }
    current[segments[segments.length - 1]] = value;
  }
  return result;
}
