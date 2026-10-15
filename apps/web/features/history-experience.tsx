'use client';

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactElement } from 'react';
import { ArrowDown, ArrowRight, ArrowUp, BookOpen, ExternalLink, Headphones, Search, Volume2, X } from 'lucide-react';
import { Shell } from './shell';
import { EventArchitecture } from './architecture';
import { api, type TimelineEvent } from './api';
import { CURATED_EVENTS, ERAS, eventLabel, FEATURED_EVENT_IDS, type Era } from './history';
import { HistoryRoad, roadPoints, ROAD_ROW_HEIGHT } from './history-road';
import { OasisFeature } from './oasis-feature';
import { HistoryTerminus } from './history-terminus';

const ROAD_NAMES = { earth: '大漠土路', stone: '古城石路', asphalt: '时代大道' };
const ERA_PAPERS: Record<string, string> = { ancient: 'var(--h-desert-paper)', imperial: 'var(--h-aged)', republic: 'var(--h-paper-light)', renewal: 'var(--h-modern-paper)', modern: 'var(--h-modern-paper)' };

function EraChapter({ era, events, nextPaper, onSelect }: { era: Era; events: TimelineEvent[]; nextPaper: string; onSelect: (event: TimelineEvent) => void }): ReactElement {
  const points = roadPoints(events);
  const modern = era.road === 'asphalt';
  return (
    <section className={`era-chapter tone-${era.tone} scenery-${era.id}`} id={era.id} data-road={era.road} aria-labelledby={`${era.id}-title`} style={{ '--chapter-next-paper': nextPaper } as CSSProperties}>
      <div className="chapter-scenery" aria-hidden="true"><img className="scenery-distance" src={modern ? '/images/city-layers.webp' : '/images/desert-layers.webp'} alt="" width={2172} height={724} loading="lazy" decoding="async" /><img className="scenery-ground" src={modern ? '/images/yulin-oasis.webp' : '/images/desert-layers.webp'} alt="" width={2172} height={724} loading="lazy" decoding="async" /></div>
      <header className="chapter-heading"><span className="chapter-index">{era.index}</span><div><p className="chapter-range">{era.range}</p><h2 id={`${era.id}-title`}>{era.name}</h2><p>{era.subtitle}</p></div><span className="chapter-count">{ROAD_NAMES[era.road]}<small>{events.length.toLocaleString('zh-CN')} 个节点</small></span></header>
      <div className="river-events">
        {events.map((event, index) => (
          <article className={`river-event ${index % 2 === 0 ? 'river-left' : 'river-right'} ${event.id === 'river-ecology' ? 'river-ecology' : ''}`} key={event.id} data-event={event.id} id={event.id === 'river-ecology' ? 'green-yulin' : undefined} style={{ '--node-left': `${points[index].x / 10}%`, '--node-top': `${points[index].y - index * ROAD_ROW_HEIGHT}px`, '--mobile-node-left': `${points[index].mobileX}px` } as CSSProperties}>
            {event.id === 'river-ecology' ? <OasisFeature event={event} onSelect={onSelect} /> : <div className="river-event-copy"><span className="node-kind">{eventLabel(event.event_type)}</span><p className="node-year">{event.year_label}</p><h3><button onClick={() => onSelect(event)}>{event.title}</button></h3><p className="node-excerpt">{event.body.length > 48 ? `${event.body.slice(0, 48)}…` : event.body}</p><button className="node-read" onClick={() => onSelect(event)}><BookOpen size={14} />读这一段历史<ArrowRight size={14} /></button></div>}
            <button className="river-node" onClick={() => onSelect(event)} aria-label={`查看${event.title}的讲解`}><span /></button>
            {event.id !== 'river-ecology' && <div className="node-architecture"><EventArchitecture event={event} className="landmark-main" /></div>}
          </article>
        ))}
      </div>
    </section>
  );
}

export function HistoryExperience(): ReactElement {
  const [events, setEvents] = useState<TimelineEvent[]>(CURATED_EVENTS);
  const [stats, setStats] = useState<Record<string, number> | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [activeEra, setActiveEra] = useState(ERAS[0].id);
  const [selected, setSelected] = useState<TimelineEvent | null>(null);
  const [offline, setOffline] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [speechError, setSpeechError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const river = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void api<TimelineEvent[]>('/timeline', { signal: controller.signal }).then(setEvents).catch((error: Error) => { if (error.name !== 'AbortError') setOffline(true); });
    void api<Record<string, number>>('/stats', { signal: controller.signal }).then(setStats).catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const nodeId = new URLSearchParams(window.location.search).get('node');
    const event = events.find((item) => item.id === nodeId);
    if (event) setSelected(event);
  }, [events]);

  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting) setActiveEra(['green-yulin', 'journey-end'].includes(entry.target.id) ? 'today' : entry.target.id);
    }, { rootMargin: '-15% 0px -65% 0px' });
    for (const era of ERAS) { const element = document.getElementById(era.id); if (element) observer.observe(element); }
    const oasis = document.getElementById('green-yulin');
    if (oasis) observer.observe(oasis);
    const terminus = document.getElementById('journey-end');
    if (terminus) observer.observe(terminus);
    return () => observer.disconnect();
  }, [events, filter, query]);

  useEffect(() => {
    if (selected && !dialog.current?.open) dialog.current?.showModal();
    return () => { if ('speechSynthesis' in window) window.speechSynthesis.cancel(); };
  }, [selected]);

  const featured = useMemo(() => events.filter((event) => FEATURED_EVENT_IDS.has(event.id)), [events]);
  const supplementary = useMemo(() => events.filter((event) => !FEATURED_EVENT_IDS.has(event.id)).sort((a, b) => a.sort_year - b.sort_year || a.id.localeCompare(b.id)), [events]);
  const exploring = filter !== 'all' || query.trim().length > 0;
  const filtered = useMemo(() => (exploring ? events : featured).filter((event) => (filter === 'all' || event.event_type === filter) && `${event.title}${event.year_label}${event.body}`.includes(query.trim())).sort((a, b) => a.sort_year - b.sort_year || a.id.localeCompare(b.id)), [events, featured, exploring, filter, query]);
  const chapters = useMemo(() => ERAS.map((era) => ({ era, events: filtered.filter((event) => event.sort_year >= era.start && event.sort_year < era.end) })).filter((chapter) => chapter.events.length > 0), [filtered]);
  const ongoing = !exploring && filtered.some((event) => event.id === 'river-ecology');

  function closeDetail(): void {
    dialog.current?.close();
    setSelected(null);
    setSpeaking(false);
    setSpeechError('');
  }

  function readAloud(): void {
    if (!selected) return;
    if (!('speechSynthesis' in window)) { setSpeechError('当前浏览器不支持朗读，可直接阅读正文。'); return; }
    window.speechSynthesis.cancel();
    if (speaking) { setSpeaking(false); return; }
    const speech = new SpeechSynthesisUtterance(`${selected.year_label}。${selected.title}。${selected.body}`);
    speech.lang = 'zh-CN';
    speech.rate = 0.9;
    speech.onend = (): void => setSpeaking(false);
    speech.onerror = (): void => { setSpeaking(false); setSpeechError('朗读未能完成，可直接阅读正文或再次尝试。'); };
    setSpeaking(true);
    window.speechSynthesis.speak(speech);
  }

  function askAbout(): void {
    const title = selected?.title;
    closeDetail();
    window.dispatchEvent(new CustomEvent('yulin:ask', { detail: `请结合资料库讲解“${title}”，注明依据和仍待核实的部分。` }));
  }

  return (
    <Shell fullWidth>
      <section className="history-hero" id="top">
        <div className="hero-topline"><span>榆林地方历史文化长卷</span><span>YULIN · A CHRONICLE OF TIME</span></div>
        <div className="hero-title"><div className="hero-kicker"><span />黄河奔流，文脉不息<span /></div><h1>一河千年<span>榆林有志</span></h1><p>循岁月长河，读塞上春秋。<br />从古老石城到今日榆林，每一个时代，都有可追溯的回响。</p><a className="heritage-button" href="#chronicle">展开历史长卷<ArrowDown size={16} /></a></div>
        <div className="hero-side-note" aria-hidden="true">山河为卷<br />岁月为章</div>
        <img className="hero-engraving" src="/images/desert-layers.webp" alt="参考镇北台形制创作的西北大漠、长城与古城艺术意象" width={2172} height={724} fetchPriority="high" />
        <div className="hero-bottom"><span><i />从史前至当代 · {featured.length.toLocaleString('zh-CN')} 个关键节点</span><span>循时代之路，向下读榆林<ArrowDown size={14} /></span></div>
      </section>

      <section className="chronicle-intro" id="chronicle"><div><span className="section-kicker">纵览千年 · 读懂榆林</span><h2>一段路，一个时代。</h2></div><p>从大漠土路，到古城石路，再到今日大道。<br className="desktop-only" />沿着 {featured.length} 个关键节点，读城池兴废与百姓生活。</p><div className="legend"><span><i className="legend-event" />历史大事</span><span><i className="legend-name" />地名建置</span><span><i className="legend-book" />方志传承</span></div></section>

      <nav className="era-navigation" aria-label="时代目录">{ERAS.map((era) => <a key={era.id} href={`#${era.id}`} aria-current={activeEra === era.id ? 'location' : undefined}><span>{era.index}</span>{era.name}<small>{era.range}</small></a>)}</nav>

      <div className="chronicle-toolbar"><div className="history-filters" aria-label="节点类型">{[['all', '关键节点'], ['name_change', '地名沿革'], ['event', '历史大事'], ['gazetteer', '方志传承']].map(([value, label]) => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div><label className="history-search"><Search size={16} /><span className="sr-only">检索历史节点</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="检索全部编年资料" maxLength={100} />{query && <button onClick={() => setQuery('')} aria-label="清除历史检索"><X size={14} /></button>}</label></div>
      {offline && <p className="catalog-notice" role="status">实时服务暂未连接，当前展示随项目保存的历史编年。启动服务后刷新即可读取最新节点。</p>}
      {filter === 'name_change' && <p className="catalog-notice">古代地域建置的范围各不相同；这里展示沿革线索，并非同一行政区的连续改名。</p>}
      {filtered.length === 0 && <div className="heritage-empty"><h2>{events.length ? '没有找到对应节点' : '暂无已发布历史节点'}</h2><p>{events.length ? '换个年代或地名，或恢复完整长卷。' : '可先阅读方志资料，待整理后再查看长卷。'}</p>{events.length ? <button className="heritage-button" onClick={() => { setQuery(''); setFilter('all'); }}>恢复完整长卷</button> : <a className="heritage-button" href="/library">阅读方志资料</a>}</div>}
      <div className="chronicle-river" ref={river}><HistoryRoad containerRef={river} revision={`${filtered.map((event) => event.id).join('|')}|${exploring ? 'filtered' : 'complete'}`} />{chapters.map((chapter, index) => <EraChapter key={chapter.era.id} era={chapter.era} events={chapter.events} nextPaper={chapters[index + 1] ? ERA_PAPERS[chapters[index + 1].era.tone] : ongoing ? 'var(--h-oasis-paper)' : ERA_PAPERS[chapter.era.tone]} onSelect={setSelected} />)}{ongoing && <HistoryTerminus />}</div>

      {!exploring && supplementary.length > 0 && <details className="supplementary-chronicle"><summary>还有 {supplementary.length} 条编年资料<span>展开简表，按年代查阅<ArrowDown size={16} /></span></summary><div>{supplementary.map((event) => <button key={event.id} onClick={() => setSelected(event)}><span>{event.year_label}</span><strong>{event.title}</strong><ArrowRight size={14} /></button>)}</div></details>}

      <section className="archive-invitation"><div className="archive-invitation-copy"><span className="section-kicker">纸上千年 · 数字新生</span><h2>长卷之外，原志之中。</h2><p>打开《榆林府志》《米脂县志》，对照扫描原页与识别成果，让每一次讲解都有据可查。</p><a href="/library">走进方志文献库<ArrowRight size={16} /></a></div><div className="archive-numbers"><div><strong>{stats?.volumes?.toLocaleString('zh-CN') ?? '—'}</strong><span>收录材料文件</span></div><div><strong>{stats?.pages?.toLocaleString('zh-CN') ?? '—'}</strong><span>原志扫描页</span></div><div><strong>{stats?.recognized_pages?.toLocaleString('zh-CN') ?? '—'}</strong><span>已有识别成果</span></div></div></section>
      <a className="back-to-top" href="#top" aria-label="回到长卷顶部"><ArrowUp size={18} /></a>

      <dialog ref={dialog} className="history-detail" aria-labelledby="history-detail-title" onCancel={closeDetail} onClick={(event) => { if (event.target === event.currentTarget) closeDetail(); }}>
        {selected && <><button className="detail-close" onClick={closeDetail} aria-label="关闭节点讲解"><X size={20} /></button><div className={`detail-visual tone-${ERAS.find((era) => selected.sort_year >= era.start && selected.sort_year < era.end)?.tone}`}><EventArchitecture event={selected} /></div><div className="detail-copy"><span className="node-kind">{eventLabel(selected.event_type)}</span><p className="node-year">{selected.year_label}</p><h2 id="history-detail-title">{selected.title}</h2><p className="detail-body">{selected.body}</p><div className="detail-source"><BookOpen size={18} /><div><strong>史料依据</strong><p>{selected.source_note}</p>{selected.source_url && <a href={selected.source_url} target={selected.source_url.startsWith('/') ? undefined : '_blank'} rel="noreferrer">{selected.source_url.startsWith('/') ? '对照方志原页' : '阅读来源材料'}<ExternalLink size={14} /></a>}</div></div><div className="detail-actions"><button className="heritage-button" onClick={askAbout}><Headphones size={16} />询问史料助手</button><button className="heritage-outline" onClick={readAloud}><Volume2 size={16} />{speaking ? '停止朗读' : '朗读讲解'}</button></div>{speechError && <p role="status">{speechError}</p>}</div></>}
      </dialog>
    </Shell>
  );
}
