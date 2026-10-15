'use client';

import { useEffect, useId, useRef, type ReactElement } from 'react';

const ACTIONS = 'a[href], button, summary, select, [role="button"], label[for], input[type="checkbox"], input[type="radio"], input[type="range"], input[type="file"], input[type="color"]';
type CursorPoint = { x: number; y: number };
// Preserve the mouse position across Shell remounts without a React render per move.
let lastMousePoint: CursorPoint | undefined;

function pointerMode(target: Element | null): 'rest' | 'action' | 'hidden' {
  if (!target?.closest('.heritage-site')) return 'hidden';
  if (target.closest('[inert], :disabled, [aria-disabled="true"], [aria-busy="true"]')) return 'rest';
  if (target.closest(ACTIONS)) return 'action';
  return 'rest';
}

export function HeritageCursor(): ReactElement {
  const brushId = useId();
  const layer = useRef<HTMLDivElement>(null);
  const halo = useRef<HTMLDivElement>(null);
  const press = useRef<HTMLDivElement>(null);
  const imprint = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const root = layer.current;
    const follower = halo.current;
    const pressPoint = press.current;
    const mark = imprint.current;
    if (!root || !follower || !pressPoint || !mark) return;
    const enabled = window.matchMedia('(hover: hover) and (pointer: fine) and (forced-colors: none)');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let lastPoint = lastMousePoint;
    let impression: Animation | undefined;
    let surface: Element | null = null;
    const theme = getComputedStyle(root);
    const duration = parseFloat(theme.getPropertyValue('--h-fast')) || 140;
    const easing = theme.getPropertyValue('--h-ease').trim() || 'ease-out';

    function hide(): void {
      lastPoint = undefined;
      surface = null;
      root!.dataset.mode = 'hidden';
      delete document.documentElement.dataset.heritageCursor;
      if (typeof root!.hidePopover === 'function' && root!.matches(':popover-open')) root!.hidePopover();
      cancelAnimationFrame(frame);
      frame = 0;
      impression?.cancel();
    }

    function clearPosition(): void { lastMousePoint = undefined; hide(); }
    function motionPreference(): void { impression?.cancel(); schedule(); }

    function draw(): void {
      frame = 0;
      if (!enabled.matches || !lastPoint) { hide(); return; }
      const { x, y } = lastPoint;
      const target = document.elementFromPoint(x, y);
      const mode = pointerMode(target);
      if (mode === 'hidden') { hide(); return; }
      const nextSurface = target?.closest('dialog[open], [popover]:popover-open') ?? null;
      // A manual, non-interactive popover paints above native dialogs too.
      // Hide the system cursor only after our visible pointer is positioned.
      if (typeof root!.showPopover === 'function') {
        try {
          if (nextSurface !== surface && root!.matches(':popover-open')) root!.hidePopover();
          if (!root!.matches(':popover-open')) root!.showPopover();
        } catch { hide(); return; }
      } else if (nextSurface) { hide(); return; }
      surface = nextSurface;
      root!.dataset.mode = mode;
      follower!.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      document.documentElement.dataset.heritageCursor = 'ready';
    }

    function schedule(): void {
      if (!frame && lastPoint && enabled.matches) frame = requestAnimationFrame(draw);
    }

    function move(event: PointerEvent): void {
      if (event.pointerType !== 'mouse' || !enabled.matches) { clearPosition(); return; }
      lastPoint = lastMousePoint = { x: event.clientX, y: event.clientY };
      schedule();
    }

    function stamp(event: PointerEvent): void {
      if (!enabled.matches || event.pointerType !== 'mouse' || event.button !== 0) return;
      lastPoint = lastMousePoint = { x: event.clientX, y: event.clientY };
      draw();
      if (reducedMotion.matches) return;
      if (pointerMode(event.target instanceof Element ? event.target : null) !== 'action') return;
      pressPoint!.style.transform = `translate3d(${event.clientX}px, ${event.clientY}px, 0)`;
      impression?.cancel();
      impression = mark!.animate([
        { opacity: 0.8, transform: 'translate(-50%, -50%) rotate(-8deg) scale(.8)' },
        { opacity: 0, transform: 'translate(-50%, -50%) rotate(-8deg) scale(1.25)' },
      ], { duration, easing });
    }

    function leave(event: PointerEvent): void {
      if (event.relatedTarget === null) clearPosition();
    }

    function keyboard(event: KeyboardEvent): void {
      if (event.key === 'Tab' || event.key === 'Escape') { impression?.cancel(); schedule(); }
    }

    function visibility(): void {
      if (document.hidden) clearPosition();
    }

    window.addEventListener('pointermove', move, { passive: true });
    window.addEventListener('pointerdown', stamp, { passive: true });
    window.addEventListener('pointerout', leave, { passive: true });
    window.addEventListener('scroll', schedule, { passive: true, capture: true });
    window.addEventListener('blur', clearPosition);
    window.addEventListener('keydown', keyboard);
    document.addEventListener('visibilitychange', visibility);
    enabled.addEventListener('change', clearPosition);
    reducedMotion.addEventListener('change', motionPreference);
    const dialogs = new MutationObserver(schedule);
    dialogs.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open'] });
    schedule();
    return () => {
      hide();
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerdown', stamp);
      window.removeEventListener('pointerout', leave);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('blur', clearPosition);
      window.removeEventListener('keydown', keyboard);
      document.removeEventListener('visibilitychange', visibility);
      enabled.removeEventListener('change', clearPosition);
      reducedMotion.removeEventListener('change', motionPreference);
      dialogs.disconnect();
    };
  }, []);

  return <div className="heritage-cursor-layer" ref={layer} popover="manual" data-mode="hidden" aria-hidden="true">
    <div className="heritage-cursor-halo" ref={halo}>
      <svg className="heritage-cursor-pen" viewBox="0 0 32 42" focusable="false">
        <defs>
          <linearGradient id={`${brushId}-bamboo`} x1="0" y1="0" x2="1" y2="0">
            <stop className="cursor-bamboo-shade" offset="0" />
            <stop className="cursor-bamboo-light" offset=".4" />
            <stop className="cursor-bamboo-shade" offset="1" />
          </linearGradient>
          <linearGradient id={`${brushId}-hair`} x1="0" y1="0" x2="1" y2="0">
            <stop className="cursor-hair-shade" offset="0" />
            <stop className="cursor-hair-light" offset=".55" />
            <stop className="cursor-hair-shade" offset="1" />
          </linearGradient>
        </defs>
        <g transform="rotate(-38 2 2)">
          <path className="cursor-brush-outline" d="M2 2C1.6 5.3-1.7 7.8-1.3 10.8c.1 1.1.6 2.2 1.1 3v3.4l.1 26.6Q2 46.2 4.1 43.8L4 17.2v-3.4c.6-.8 1.1-1.9 1.3-3C5.7 7.8 2.4 5.3 2 2Z" />
          <path className="cursor-brush-handle" fill={`url(#${brushId}-bamboo)`} d="M.1 15.1h3.8l.2 28.7Q2 46.2-.1 43.8Z" />
          <path className="cursor-brush-hair" fill={`url(#${brushId}-hair)`} d="M2 2C1.6 5.3-1.7 7.8-1.3 10.8-1.1 12.9.2 14.7 2 15c1.8-.3 3.1-2.1 3.3-4.2C5.7 7.8 2.4 5.3 2 2Z" />
          <path className="cursor-brush-ink" d="M2 2C1.7 4.8-.1 6.9-.9 9.2c1.6-.5 3.2.9 5.8 0C4.1 6.9 2.3 4.8 2 2Z" />
          <path className="cursor-brush-strands" d="M.6 9.8q-.3 2.2 1 3.4m1.8-3.3q.5 1.9-.9 3.4" />
          <path className="cursor-brush-collar" d="M-.2 13.8q2.2 1.2 4.4 0v3.4q-2.2.8-4.4 0Zm.3 28.9q1.9.8 3.8 0V44q-1.9 1-4 0Z" />
          <path className="cursor-brush-bands" d="M-.2 15.7q2.2 1 4.4 0M.2 25q1.8 1.2 3.6 0M.1 35q1.9 1.2 3.8 0" />
          <path className="cursor-brush-grain" d="m1 19 .1 4m-.1 5 .1 4m-.1 6 .1 3" />
        </g>
      </svg>
      <svg className="heritage-cursor-seal" viewBox="0 0 32 32" focusable="false">
        <rect className="cursor-seal-body" x="3" y="3" width="26" height="26" rx="2" />
        <path className="cursor-seal-letter" d="M8 8h16v16H8zM11 13h10m-5-3v8m-5 0h10m-9 3h8" />
        <circle className="cursor-seal-center" cx="16" cy="16" r="1.5" />
      </svg>
      <svg className="heritage-cursor-frame" viewBox="0 0 32 32" focusable="false"><path d="M4 11V4h7m10 0h7v7m0 10v7h-7m-10 0H4v-7" /></svg>
    </div>
    <div className="heritage-cursor-press" ref={press}><svg className="heritage-cursor-imprint" ref={imprint} viewBox="0 0 32 32" focusable="false"><path d="M5 5h22v22H5zM9 9h14v14H9z" /><path d="M13 13h6v6h-6z" /></svg></div>
  </div>;
}
