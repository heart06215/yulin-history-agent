'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Shell } from '@/features/shell';
import { RunPanel } from '@/features/run-panel';
import {
  api,
  type Page,
  type Version,
  type ReadingOrderPage,
} from '@/features/api';
interface VersionMeta {
  id: string;
  method: string;
  model: string;
  reviewed: number;
  created_at: string;
  current: number;
}
const METHOD_LABELS: Record<string, string> = {
  vision_api: '视觉模型识别',
  pdf_text_layer: 'PDF 文字层提取',
  agent_transcription: '智能体转录',
  human_revision: '人工校勘',
};
const methodLabel = (m: string) => METHOD_LABELS[m] ?? m;
export default function Reader() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [index, setIndex] = useState(1);
  const [input, setInput] = useState('1');
  const [page, setPage] = useState<Page | null>(null);
  const [versions, setVersions] = useState<VersionMeta[]>([]);
  const [historyId, setHistoryId] = useState('');
  const [order, setOrder] = useState<ReadingOrderPage[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    const n = Number(new URLSearchParams(window.location.search).get('page'));
    if (Number.isInteger(n) && n > 0) {
      setIndex(n);
      setInput(String(n));
    }
  }, []);
  useEffect(() => {
    api<ReadingOrderPage[]>('/sources/' + id + '/reading-order')
      .then(setOrder)
      .catch(() => setOrder([]));
  }, [id]);
  useEffect(() => {
    let active = true;
    setError('');
    setPage(null);
    setVersions([]);
    setHistoryId('');
    api<Page>('/pages/' + id + '-p' + index)
      .then(async (p) => {
        const q = new URLSearchParams(window.location.search);
        const version = q.get('version');
        const draftVersion = q.get('draft');
        if (version) {
          p.version = await api<Version>(
            '/pages/' + p.id + '/versions/' + version,
          );
          setHistoryId(version);
        }
        if (!version && !p.version) {
          try {
            p.version = await api<Version>('/knowledge/pages/' + p.id + (draftVersion ? '?version_id=' + encodeURIComponent(draftVersion) : ''));
          } catch { /* The scan remains readable when no recognition exists. */ }
        }
        setVersions(await api<VersionMeta[]>('/pages/' + p.id + '/versions').catch(() => []));
        if (active) setPage(p);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [id, index]);
  function navigate(n: number) {
    if (n < 1 || !Number.isInteger(n) || (page && n > page.page_count)) return;
    setIndex(n);
    setInput(String(n));
    window.history.replaceState({}, '', `/library/${id}?page=${n}`);
  }
  function openVersion(versionId: string) {
    if (!versionId || versionId === historyId) return;
    window.location.assign(
      versionId === 'current'
        ? `/library/${id}?page=${index}`
        : `/library/${id}?page=${index}&version=${versionId}`,
    );
  }
  const current = order.find((o) => o.id === page?.id);
  const seq = current?.sequence ?? null;
  const total = order.length || null;
  function navigateBySequence(delta: number) {
    if (seq === null) return;
    const target = order[seq - 1 + delta];
    if (target) navigate(target.scan_index);
  }
  return (
    <Shell>
      <section className="intro">
        <p className="eyebrow">
          <a href="/library">文献库</a> / 原页阅读
        </p>
        <h1>{page ? `${page.title} · ${page.volume}` : '原页阅读器'}</h1>
        {current && (
          <p className="stat-note">
            卷内顺序 第 {seq} / {total} 页
            {current.leaf_label ? ` · ${current.leaf_label}` : ''}
            {current.side ? `（${current.side}）` : ''}
            {current.original_page_no
              ? ` · 原文件第 ${current.original_page_no} 页`
              : ''}
            {' · '}
            {current.page_type === 'cover'
              ? '封面'
              : current.page_type === 'blank'
                ? '空白/衬纸'
                : '正文'}
          </p>
        )}
        <div className="row">
          <Button
            variant="outline"
            onClick={() => navigateBySequence(-1)}
            disabled={seq === null || seq <= 1}
            title="按卷内阅读顺序上一页"
          >
            卷内上一页
          </Button>
          <Button
            variant="outline"
            onClick={() => navigateBySequence(1)}
            disabled={seq === null || total === null || seq >= total}
            title="按卷内阅读顺序下一页"
          >
            卷内下一页
          </Button>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              navigate(Number(input));
            }}
          >
            <label htmlFor="page-number">扫描页</label>
            <input
              id="page-number"
              className="field"
              style={{ width: 90 }}
              type="number"
              min={1}
              max={page?.page_count}
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
            <Button variant="outline" type="submit">
              跳转
            </Button>
          </form>
          <span>/ {page?.page_count ?? '—'}</span>
        </div>
      </section>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {page && (
        <div className="reader-grid">
          <section className="panel">
            <div className="row space">
              <h2>原书扫描页</h2>
              <a
                className="link"
                href={page.source_url}
                target="_blank"
                rel="noreferrer"
              >
                来源
              </a>
            </div>
            <a
              href={'/api/v1/pages/' + page.id + '/image'}
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="scan"
                src={'/api/v1/pages/' + page.id + '/image'}
                alt={`${page.title}${page.volume}扫描第${index}页`}
              />
            </a>
            <p className="muted">
              点击图像可在新窗口查看。本地扫描页号不等于原文件页号或原书页码，阅读请用上方“卷内上一页／下一页”。
            </p>
          </section>
          <div>
            <section className="panel">
              <h2>原文与白话初译</h2>
              {page.version ? (
                <>
                  <span className="badge">
                    {!page.version.published
                      ? '识别初稿 · 尚未人工校勘'
                      : historyId
                      ? '历史发布版本 · ' +
                        (page.version.reviewed ? '人工校勘' : 'AI 识别')
                      : page.version.reviewed
                        ? '人工校勘'
                        : 'AI 识别结果，不保证完全正确'}
                  </span>
                  {versions.length > 0 && (
                    <div className="row controls">
                      <label className="muted" htmlFor="version-select">
                        文本版本
                      </label>
                      <select
                        id="version-select"
                        className="field"
                        style={{ maxWidth: 340 }}
                        value={historyId || 'current'}
                        onChange={(e) => openVersion(e.target.value)}
                      >
                        <option value="current">当前发布版本</option>
                        {versions
                          .filter((v) => !v.current)
                          .map((v) => (
                            <option key={v.id} value={v.id}>
                              {new Date(v.created_at).toLocaleDateString()} ·{' '}
                              {v.reviewed ? '人工校勘' : 'AI 识别'}
                            </option>
                          ))}
                      </select>
                    </div>
                  )}
                  <Tabs defaultValue="translation">
                    <TabsList variant="line">
                      <TabsTrigger value="translation">白话初译</TabsTrigger>
                      <TabsTrigger value="original">识别原文</TabsTrigger>
                      <TabsTrigger value="notes">整理记录</TabsTrigger>
                    </TabsList>
                    <TabsContent value="translation">
                      <p className="pre reading-text">
                        {page.version.translation}
                      </p>
                    </TabsContent>
                    <TabsContent value="original">
                      <p className="pre reading-text">
                        {page.version.transcription}
                      </p>
                    </TabsContent>
                    <TabsContent value="notes">
                      <p className="pre">{page.version.notes}</p>
                      <p className="muted">处理方式：{methodLabel(page.version.method)}</p>
                    </TabsContent>
                  </Tabs>
                </>
              ) : (
                <p className="empty">
                  本页尚无可展示的识别文本。原页已可阅读，史料助手可按需读取扫描件。
                </p>
              )}
            </section>
            <RunPanel
              key={page.id + ':' + (historyId || page.version?.id || 'none')}
              kind="explain"
              pageId={page.id}
              sourceId={id}
              versionId={historyId || (page.version?.published ? page.version.id : undefined)}
            />
            <a className="link" href={`/workbench?source=${id}&page=${index}`}>
              在识别工作台处理本页 →
            </a>
          </div>
        </div>
      )}
    </Shell>
  );
}
