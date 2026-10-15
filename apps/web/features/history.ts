import catalog from './history-timeline.json';
import type { TimelineEvent } from './api';

export const CURATED_EVENTS: TimelineEvent[] = catalog.map((event) => ({ ...event, published: 1 }));

// The exhibition follows the main historical turns; the full source chronology
// remains searchable and available through the compact supplementary list.
export const FEATURED_EVENT_IDS = new Set([
  'river-shimao', 'river-unification', 'river-road',
  'river-tongwan', 'river-tang', 'river-songxia',
  'river-city', 'river-zhenbeitai', 'river-uprising', 'river-fu', 'river-book',
  'river-republic', 'river-party', 'river-border', 'river-shajiadian',
  'river-liberation', 'river-sand', 'river-sanbei', 'river-energy',
  'river-city2000', 'river-hengshan', 'river-shenmu', 'river-livelihood', 'river-ecology',
]);

export interface Era {
  id: string;
  index: string;
  name: string;
  subtitle: string;
  range: string;
  start: number;
  end: number;
  tone: string;
  road: 'earth' | 'stone' | 'asphalt';
}

export const ERAS: Era[] = [
  { id: 'origins', index: '壹', name: '文明初曙', subtitle: '石城、边郡与黄河的记忆', range: '史前 — 秦汉', start: -10000, end: 220, tone: 'ancient', road: 'earth' },
  { id: 'frontier', index: '贰', name: '塞上春秋', subtitle: '州城更迭，千年战守', range: '魏晋 — 宋元', start: 220, end: 1368, tone: 'ancient', road: 'earth' },
  { id: 'walled-city', index: '叁', name: '九边重镇', subtitle: '驼城兴起，也见百姓疾苦', range: '明 — 清', start: 1368, end: 1912, tone: 'imperial', road: 'stone' },
  { id: 'revolution', index: '肆', name: '岁月峥嵘', subtitle: '新学启蒙，革命火种', range: '1912 — 1948', start: 1912, end: 1949, tone: 'republic', road: 'stone' },
  { id: 'renewal', index: '伍', name: '古城新生', subtitle: '人民建设，绿染黄沙', range: '1949 — 1999', start: 1949, end: 2000, tone: 'renewal', road: 'asphalt' },
  { id: 'today', index: '陆', name: '奋进榆林', subtitle: '生态筑底，发展惠民', range: '2000 — 至今', start: 2000, end: 10000, tone: 'modern', road: 'asphalt' },
];

export function architectureFor(year: number): number {
  if (year < -300) return 0;
  if (year < 220) return 1;
  if (year < 1368) return 2;
  if (year < 1645) return 3;
  if (year < 1912) return 4;
  if (year < 1949) return 5;
  if (year < 2000) return 6;
  return 7;
}

export function eventLabel(type: string): string {
  const labels: Record<string, string> = { name_change: '地名 · 建置', gazetteer: '方志 · 传承', event: '历史 · 大事', milestone: '整理 · 记录' };
  return labels[type] ?? '历史 · 大事';
}
