import type { CSSProperties, ReactElement } from 'react';
import type { TimelineEvent } from './api';
import { architectureFor } from './history';

const NAMES = ['史前石城', '秦汉边塞烽燧', '唐宋城楼', '明代镇北台', '清代城楼与街巷', '民国校舍与窑洞', '防护林与乡村', '现代公共建筑与城市绿化'];
const VARIANTS: Record<number, string> = { 0: 'stone-ruins.webp', 1: 'han-beacon.webp', 2: 'tang-gate.webp', 7: 'civic-centre.webp' };

const EVENT_SCENES: Record<string, string> = Object.fromEntries([
  ['river-shimao', 'river-unification', 'river-road', 'river-tongwan', 'river-tang', 'river-songxia', 'river-city', 'river-zhenbeitai'].map((id, index) => [id, `ancient-${index + 1}`]),
  ['river-uprising', 'river-fu', 'river-book', 'river-republic', 'river-party', 'river-border', 'river-shajiadian', 'river-liberation'].map((id, index) => [id, `heritage-${index + 1}`]),
  ['river-sand', 'river-sanbei', 'river-energy', 'river-city2000', 'river-hengshan', 'river-shenmu', 'river-livelihood', 'river-ecology'].map((id, index) => [id, `modern-${index + 1}`]),
].flat());

export function EventArchitecture({ event, className = '' }: { event: TimelineEvent; className?: string }): ReactElement {
  const scene = EVENT_SCENES[event.id];
  if (!scene) return <Architecture scene={architectureFor(event.sort_year)} className={className} />;
  return <img className={`event-architecture ${className}`} src={`/images/nodes/${scene}.webp`} width={640} height={320} alt={`${event.title}的时代景观艺术示意`} loading="lazy" decoding="async" />;
}

export function Architecture({ scene, className = '' }: { scene: number; className?: string }): ReactElement {
  const variant = VARIANTS[scene];
  const style = { '--scene-x': `${(scene % 2) * 100}%`, '--scene-y': `${Math.floor(scene / 2) * 100 / 3}%`, ...(variant ? { '--scene-image': `url('/images/${variant}')` } : {}) } as CSSProperties;
  return <div className={`architecture scene-${scene} ${variant ? 'has-variant' : ''} ${className}`} style={style} role="img" aria-label={`${NAMES[scene]}建筑艺术示意`} />;
}
