'use client';

import { useEffect, useMemo, useState, type FormEvent, type ReactElement } from 'react';
import { ArrowRight, BookOpen, Check, FileText, Search, X } from 'lucide-react';
import { Shell } from './shell';
import { api, type Source, type SearchHit } from './api';

type Status = 'all' | 'complete' | 'partial' | 'pending';
const PAGE_SIZE = 12;

function materialStatus(source: Source): Status {
  if (source.recognized_pages === 0) return 'pending';
  if (source.recognized_pages >= source.page_count) return 'complete';
  return 'partial';
}

export function GazetteerLibrary(): ReactElement {
  const [sources, setSources] = useState<Source[]>([]);
  const [query, setQuery] = useState('');
  const [edition, setEdition] = useState('all');
  const [status, setStatus] = useState<Status>('all');
  const [mode, setMode] = useState<'catalog' | 'text'>('catalog');
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [page, setPage] = useState(1);

  function load(): void {
    setError('');
    setLoaded(false);
    void api<Source[]>('/knowledge/catalog').then(setSources).catch((failure: Error) => setError(failure.message)).finally(() => setLoaded(true));
  }

  useEffect(load, []);
  const editions = useMemo(() => [...new Map(sources.map((source) => [source.edition_id, { id: source.edition_id, title: source.title, year: source.year }])).values()], [sources]);
  const totals = useMemo(() => sources.reduce((result, source) => ({ pages: result.pages + source.page_count, recognized: result.recognized + source.recognized_pages, translated: result.translated + source.translated_pages, reviewed: result.reviewed + source.reviewed_pages }), { pages: 0, recognized: 0, translated: 0, reviewed: 0 }), [sources]);
  const filtered = useMemo(() => sources.filter((source) => (edition === 'all' || edition === source.edition_id) && (status === 'all' || materialStatus(source) === status) && (mode === 'text' || `${source.title}${source.volume}${source.year}`.includes(query.trim()))), [sources, edition, status, query, mode]);
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const pages = Math.ceil(filtered.length / PAGE_SIZE);

  async function search(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (mode === 'catalog') { setPage(1); return; }
    if (!query.trim()) return;
    setSearching(true);
    setError('');
    try { setHits(await api<SearchHit[]>(`/knowledge/search?q=${encodeURIComponent(query.trim())}`)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setSearching(false); }
  }

  function clearFilters(): void { setQuery(''); setStatus('all'); setEdition('all'); setPage(1); setHits(null); }

  return <Shell fullWidth>
    <section className="library-hero"><div className="library-hero-copy"><span className="section-kicker">榆林地方志 · 数字文献库</span><h1>纸上千年，<br />如今可读。</h1><p>保留一页原志，打开一段历史。<br />在原页、识别原文与白话初译之间，读懂榆林的来路。</p><span className="library-hero-note"><BookOpen size={14} />国家图书馆藏本 · 保留来源与逐页对照</span></div><div className="library-books" aria-hidden="true"><div className="exhibition-book book-mizhi"><span>康熙二十年刻本</span><strong>米脂縣志</strong><small>地方文献 · 原志存真</small></div><div className="exhibition-book book-yulin"><span>道光二十一年刻本</span><strong>榆林府志</strong><small>五十卷 · 一方山河</small></div><p>存史，亦为今日留下回响。</p></div></section>
    <section className="library-metrics" aria-label="方志整理进度">{[['材料文件', sources.length], ['原志扫描页', totals.pages], ['已识别页', totals.recognized], ['白话初译页', totals.translated], ['人工校勘页', totals.reviewed]].map(([label, value]) => <div key={label}><strong>{loaded && !error ? Number(value).toLocaleString('zh-CN') : '—'}<small>{label === '材料文件' ? '卷 / 件' : '页'}</small></strong><span>{label}</span></div>)}</section>
    <section className="library-catalog" aria-labelledby="library-catalog-title"><div className="library-section-heading"><div><span className="section-kicker">原志有据 · 整理有序</span><h2 id="library-catalog-title">方志典藏</h2></div><p>收录、识别、初译与校勘，分别记录。<br />识别初稿可直接阅读，尚未人工校勘。</p></div>
      <form className="library-search" onSubmit={(event) => void search(event)}><label className="sr-only" htmlFor="library-query">检索方志资料</label><Search size={20} /><input id="library-query" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder={mode === 'catalog' ? '检索书名、卷次或刊刻年份，例如：榆林府志' : '检索识别原文与初译，例如：城池、米脂、李自成'} maxLength={100} />{query && <button type="button" onClick={() => { setQuery(''); setHits(null); }} aria-label="清除方志检索"><X size={16} /></button>}<label className="sr-only" htmlFor="library-mode">检索范围</label><select id="library-mode" value={mode} onChange={(event) => { setMode(event.target.value as 'catalog' | 'text'); setHits(null); setPage(1); }}><option value="catalog">目录检索</option><option value="text">全文检索</option></select><button type="submit" className="heritage-button" disabled={searching || (mode === 'text' && !query.trim())}>{searching ? '正在检索…' : '检索资料'}</button></form>
      {error && <div className="library-error" role="alert"><p>资料读取失败：{error}</p><button className="heritage-outline" onClick={load}>重新读取文献库</button></div>}
      {mode === 'text' && hits && <section className="library-search-results" aria-label="全文检索结果"><h3>找到 {hits.length.toLocaleString('zh-CN')} 条相关文本</h3>{hits.length === 0 && <p>暂无对应识别文本，可修改关键词，或向史料助手询问未识别原页。</p>}{hits.map((hit) => <a href={hit.reader_url} key={hit.citation_id} className="library-search-hit"><div><BookOpen size={16} /><strong>《{hit.title}》{hit.volume} · 第 {hit.scan_index} 页</strong><span>{hit.evidence_status === 'recognition_draft' ? '识别初稿 · 未校勘' : hit.reviewed ? '人工校勘' : '已发布识别文本'}</span></div><p>{(hit.translation || hit.transcription).replace(/\s+/g, ' ').slice(0, 180)}…</p><span className="text-link">对照原页<ArrowRight size={14} /></span></a>)}</section>}
      <div className="library-filters"><div className="edition-filters" aria-label="方志版本"><button aria-pressed={edition === 'all'} onClick={() => { setEdition('all'); setPage(1); }}>全部典藏</button>{editions.map((item) => <button key={item.id} aria-pressed={edition === item.id} onClick={() => { setEdition(item.id); setPage(1); }}>{item.title}<small>{item.year}年版</small></button>)}</div><label>整理状态<select value={status} onChange={(event) => { setStatus(event.target.value as Status); setPage(1); }}><option value="all">全部状态</option><option value="complete">本卷已识别</option><option value="partial">部分已识别</option><option value="pending">待识别</option></select></label></div>
      <div className="catalog-caption"><p>{loaded ? `${filtered.length.toLocaleString('zh-CN')} 个材料文件` : '正在读取方志目录…'}</p><span><i />识别成果随整理进度更新</span></div>
      <div className="gazetteer-grid" aria-busy={!loaded}>{!loaded && Array.from({ length: 6 }, (_, index) => <div className="gazetteer-skeleton" key={`skeleton-${index}`}><div /><span /><span /></div>)}{visible.map((source) => { const state = materialStatus(source); const href = `/library/${source.id}?page=${source.first_recognized_page ?? 1}`; return <article className="gazetteer-card" key={source.id}><a className={`catalog-book ${source.year < 1800 ? 'catalog-mizhi' : 'catalog-yulin'}`} href={href} aria-label={`阅读${source.title}${source.volume}`}><small>{source.year}年刻本</small><strong>{source.title}</strong><span>{source.volume}</span><BookOpen size={20} /></a><div className="gazetteer-card-copy"><span className={`recognition-status status-${state}`}>{state === 'complete' ? <Check size={12} /> : <FileText size={12} />}{state === 'complete' ? '本卷已识别' : state === 'partial' ? '部分已识别' : '原页待识别'}</span><h3><a href={href}>{source.volume}</a></h3><p>{source.title} · {source.page_count.toLocaleString('zh-CN')} 扫描页</p><div className="recognition-progress"><span>识别进度</span><strong>{source.recognized_pages} / {source.page_count}</strong></div><meter min={0} max={source.page_count || 1} value={source.recognized_pages} aria-label={`${source.volume}识别进度`} /><p className="recognition-caption">初译 {source.translated_pages} 页 · 校勘 {source.reviewed_pages} 页</p><a className="text-link" href={href}>{source.recognized_pages ? '阅读识别成果' : '查看原志扫描'}<ArrowRight size={14} /></a></div></article>; })}</div>
      {loaded && !filtered.length && !error && <div className="heritage-empty"><h3>{sources.length ? '没有匹配的典藏' : '方志库尚未收录材料'}</h3><p>{sources.length ? '试试其他卷次，或清除筛选条件。' : '在整理工作台导入原志扫描件后，这里会展示目录与成果。'}</p><button className="heritage-outline" onClick={clearFilters}>清除筛选</button></div>}
      {pages > 1 && <nav className="catalog-pagination" aria-label="方志目录分页"><button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button>{Array.from({ length: pages }, (_, index) => <button key={index + 1} aria-current={page === index + 1 ? 'page' : undefined} onClick={() => setPage(index + 1)}>{index + 1}</button>)}<button disabled={page === pages} onClick={() => setPage(page + 1)}>下一页</button></nav>}
    </section>
    <section className="library-footnote"><BookOpen size={24} /><div><h3>每一份识别，都能回到原页。</h3><p>识别初稿含疑难字与存疑记录；已发布文本保留版本引用，未识别页保留扫描影像。史料助手可检索成果，按需读取尚未识别的原页。</p></div><button className="heritage-outline" onClick={() => window.dispatchEvent(new CustomEvent('yulin:ask', { detail: '请介绍方志库已收录的资料及识别进度。' }))}>询问史料助手<ArrowRight size={16} /></button></section>
  </Shell>;
}
