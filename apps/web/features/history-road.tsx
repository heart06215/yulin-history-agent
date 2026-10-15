'use client';

import { useEffect, useId, useState, type ReactElement, type RefObject } from 'react';
import type { TimelineEvent } from './api';
import type { Era } from './history';

export const ROAD_ROW_HEIGHT = 216;
export interface RoadPoint { x: number; y: number; mobileX: number }
export interface RoadAnchor { x: number; y: number }
interface RoadMaterial { kind: Era['road']; start: number; end: number }
interface RoadLayout { width: number; height: number; mobile: boolean; anchors: RoadAnchor[]; materials: RoadMaterial[]; continues: boolean }

function seed(value: string): number {
  let result = 2166136261;
  for (const character of value) result = Math.imul(result ^ character.charCodeAt(0), 16777619);
  return result >>> 0;
}

export function roadPoints(events: TimelineEvent[]): RoadPoint[] {
  return events.map((event, index) => {
    const value = seed(event.id);
    return { x: 435 + value % 131, y: index * ROAD_ROW_HEIGHT + 86 + (value >>> 8) % 44, mobileX: 22 + value % 36 };
  });
}

// A shared derivative at every anchor keeps the tangent continuous, including
// through chapter headings. All materials use exactly the same entire path.
export function smoothRoadPath(anchors: RoadAnchor[]): string {
  if (!anchors.length) return '';
  const slopes = anchors.map((anchor, index) => {
    const previous = anchors[Math.max(0, index - 1)];
    const next = anchors[Math.min(anchors.length - 1, index + 1)];
    return (next.x - previous.x) / Math.max(1, next.y - previous.y);
  });
  let path = `M ${anchors[0].x} ${anchors[0].y}`;
  for (let index = 1; index < anchors.length; index += 1) {
    const previous = anchors[index - 1];
    const current = anchors[index];
    const third = (current.y - previous.y) / 3;
    path += ` C ${previous.x + slopes[index - 1] * third} ${previous.y + third}, ${current.x - slopes[index] * third} ${current.y - third}, ${current.x} ${current.y}`;
  }
  return path;
}

export function HistoryRoad({ containerRef, revision }: { containerRef: RefObject<HTMLDivElement | null>; revision: string }): ReactElement | null {
  const [layout, setLayout] = useState<RoadLayout | null>(null);
  const id = useId().replace(/:/g, '');
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let frame = 0;
    function measure(): void {
      if (!container) return;
      const bounds = container.getBoundingClientRect();
      const nodes = Array.from(container.querySelectorAll<HTMLElement>('.river-node')).map((node) => {
        const rect = node.getBoundingClientRect();
        return { x: rect.left + rect.width / 2 - bounds.left, y: rect.top + rect.height / 2 - bounds.top };
      });
      if (!nodes.length) { setLayout(null); return; }
      const materials: RoadMaterial[] = [];
      for (const chapter of container.querySelectorAll<HTMLElement>('[data-road]')) {
        const rect = chapter.getBoundingClientRect();
        const kind = chapter.dataset.road as Era['road'];
        const previous = materials[materials.length - 1];
        if (previous?.kind === kind) previous.end = rect.bottom - bounds.top;
        else materials.push({ kind, start: rect.top - bounds.top, end: rect.bottom - bounds.top });
      }
      const mobile = window.innerWidth < 768;
      const anchors = [{ x: nodes[0].x, y: 0 }, ...nodes];
      const horizon = container.querySelector('.terminus-heading');
      const last = nodes[nodes.length - 1];
      // Continue beyond today's marker and dissolve into the city horizon.
      anchors.push({ x: last.x + (horizon ? (mobile ? 16 : 32) : (mobile ? 8 : -24)), y: horizon ? Math.min(bounds.height, horizon.getBoundingClientRect().bottom - bounds.top + 72) : bounds.height });
      setLayout({ width: bounds.width, height: bounds.height, mobile, anchors, materials, continues: Boolean(horizon) });
    }
    function schedule(): void { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); }
    const observer = new ResizeObserver(schedule);
    observer.observe(container);
    window.addEventListener('resize', schedule);
    schedule();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); window.removeEventListener('resize', schedule); };
  }, [containerRef, revision]);
  if (!layout) return null;
  const path = smoothRoadPath(layout.anchors);
  const feather = layout.mobile ? 28 : 42;
  const end = layout.anchors[layout.anchors.length - 1].y;
  return <svg className={`history-road continuous-road ${layout.mobile ? 'is-mobile' : ''}`} viewBox={`0 0 ${layout.width} ${layout.height}`} width={layout.width} height={layout.height} aria-hidden="true" data-anchor-count={layout.anchors.length}>
    <defs>
      {layout.continues && <><linearGradient id={`${id}-horizon-fade`} x1="0" x2="0" y1={Math.max(0, end - 180)} y2={end} gradientUnits="userSpaceOnUse"><stop offset="0" stopColor="white" /><stop offset="1" stopColor="white" stopOpacity="0" /></linearGradient><mask id={`${id}-horizon`} maskUnits="userSpaceOnUse" x="0" y="0" width={layout.width} height={layout.height}><rect width={layout.width} height={layout.height} fill={`url(#${id}-horizon-fade)`} /></mask></>}
      {(['earth', 'stone'] as const).map((material) => <pattern key={material} id={`${id}-${material}`} width={material === 'earth' ? 160 : 112} height={material === 'earth' ? 160 : 112} patternUnits="userSpaceOnUse"><image href={`/images/road-${material}.webp`} width={material === 'earth' ? 160 : 112} height={material === 'earth' ? 160 : 112} /></pattern>)}
      {layout.materials.map((material, index) => {
        const top = Math.max(0, material.start - feather);
        const bottom = Math.min(layout.height, material.end + feather);
        return <g key={index}>
          <linearGradient id={`${id}-fade-${index}`} x1="0" x2="0" y1={top} y2={bottom} gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="white" stopOpacity={index === 0 ? 1 : 0} /><stop offset={(material.start + feather - top) / (bottom - top)} stopColor="white" /><stop offset={(material.end - feather - top) / (bottom - top)} stopColor="white" /><stop offset="1" stopColor="white" stopOpacity={index === layout.materials.length - 1 ? 1 : 0} />
          </linearGradient>
          <mask id={`${id}-material-${index}`} maskUnits="userSpaceOnUse" x="0" y={top} width={layout.width} height={bottom - top}><rect x="0" y={top} width={layout.width} height={bottom - top} fill={`url(#${id}-fade-${index})`} /></mask>
        </g>;
      })}
    </defs>
    <g mask={layout.continues ? `url(#${id}-horizon)` : undefined}>
    <path className="road-underlay" d={path} />
    {layout.materials.map((material, index) => <g key={index} className={`road-${material.kind}`} mask={`url(#${id}-material-${index})`}>
      <path className="road-soft-shoulder" d={path} /><path className="road-edge" d={path} /><path className="road-surface" d={path} />
      {material.kind === 'asphalt' ? <path className="road-markings" d={path} /> : <path className="road-texture" stroke={`url(#${id}-${material.kind})`} d={path} />}
    </g>)}
    </g>
  </svg>;
}
