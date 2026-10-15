'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { api, post, auth, type Result, type RunEvent } from './api';

interface SavedRun {
  run_id: string;
  cursor: number;
}

const terminal = new Set(['run.completed', 'run.failed', 'run.cancelled']);

export function RunPanel({
  kind,
  pageId,
  sourceId,
  versionId,
  adminToken = '',
  onComplete,
}: {
  kind: 'explain' | 'recognize';
  pageId?: string;
  sourceId?: string;
  versionId?: string;
  adminToken?: string;
  onComplete?: () => void;
}) {
  const [question, setQuestion] = useState(
    '请解释这一页，并引用原文作为依据。',
  );
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [runId, setRunId] = useState('');
  const [reconnecting, setReconnecting] = useState(false);
  const stream = useRef<EventSource | null>(null);
  const callback = useRef(onComplete);
  callback.current = onComplete;
  const storageKey = `yulin-run:${kind}:${pageId ?? sourceId ?? 'default'}`;

  useEffect(() => () => stream.current?.close(), []);

  const handleEvent = useCallback(
    (v: RunEvent) => {
      setEvents((old) => (old.some((x) => x.id === v.id) ? old : [...old, v]));
      if (terminal.has(v.type)) {
        sessionStorage.removeItem(storageKey);
        setBusy(false);
        setReconnecting(false);
      }
      if (v.type === 'run.completed') {
        setResult(v.result || null);
        callback.current?.();
      }
      if (v.type === 'run.failed' || v.type === 'run.cancelled') {
        setError(v.error || '运行未完成');
      }
    },
    [storageKey],
  );

  const openStream = useCallback(
    (id: string, cursor: number) => {
      stream.current?.close();
      if (cursor > 0) {
        sessionStorage.setItem(storageKey, JSON.stringify({ run_id: id, cursor }));
      }
      const suffix = cursor > 0 ? `?after_event_id=${cursor}` : '';
      const es = new EventSource('/api/v1/agent/runs/' + id + '/stream' + suffix);
      stream.current = es;
      es.onopen = () => setReconnecting(false);
      es.addEventListener('update', (e) => {
        const v = JSON.parse((e as MessageEvent).data) as RunEvent;
        sessionStorage.setItem(
          storageKey,
          JSON.stringify({ run_id: id, cursor: v.id }),
        );
        handleEvent(v);
      });
      es.addEventListener('end', () => {
        es.close();
        setBusy(false);
        setReconnecting(false);
      });
      es.onerror = () => setReconnecting(true);
    },
    [handleEvent, storageKey],
  );

  // Resume the same run after a refresh instead of starting a second one.
  useEffect(() => {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return;
    let cancelled = false;
    (async () => {
      let saved: SavedRun;
      try {
        saved = JSON.parse(raw) as SavedRun;
      } catch {
        sessionStorage.removeItem(storageKey);
        return;
      }
      try {
        const run = await api<{
          status: string;
          result?: Result | null;
          error?: string;
        }>('/agent/runs/' + saved.run_id);
        if (cancelled) return;
        setRunId(saved.run_id);
        if (run.status === 'completed') {
          setResult(run.result || null);
          sessionStorage.removeItem(storageKey);
        } else if (run.status === 'failed' || run.status === 'cancelled') {
          setError(run.error || '运行未完成');
          sessionStorage.removeItem(storageKey);
        } else {
          setBusy(true);
          openStream(saved.run_id, saved.cursor || 0);
        }
      } catch {
        sessionStorage.removeItem(storageKey);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [storageKey, openStream]);

  async function start() {
    setError('');
    setEvents([]);
    setResult(null);
    setBusy(true);
    setReconnecting(false);
    stream.current?.close();
    sessionStorage.removeItem(storageKey);
    try {
      await api('/sessions', post({}));
      const r = await api<{ run_id: string }>(
        kind === 'recognize' ? '/admin/agent/runs' : '/agent/runs',
        {
          ...post({
            kind,
            question: kind === 'explain' ? question : '',
            page_id: pageId || null,
            source_id: sourceId || null,
            version_id: versionId || null,
            idempotency_key: crypto.randomUUID(),
          }),
          headers: kind === 'recognize' ? auth(adminToken) : {},
        },
      );
      setRunId(r.run_id);
      sessionStorage.setItem(
        storageKey,
        JSON.stringify({ run_id: r.run_id, cursor: 0 }),
      );
      openStream(r.run_id, 0);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function cancel() {
    try {
      await api('/agent/runs/' + runId + '/cancel', post({}));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <section className="panel">
      <h2>
        {kind === 'explain' ? '问一问史料讲解助手' : '运行县志识别 Agent'}
      </h2>
      <p className="muted">
        {kind === 'explain'
          ? versionId
            ? '实时读取已发布资料，本轮问题固定到当前所选文本版本。'
            : '读取资料库史料与识别初稿，回答附原页引用及校勘状态。'
          : '检查原页 → 选择识别工具 → 白话初译 → 保存待审草稿。'}
      </p>
      {kind === 'explain' && (
        <label>
          你的问题
          <textarea
            className="field"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={2000}
          />
        </label>
      )}
      <div className="row controls">
        <Button
          className="action"
          onClick={start}
          disabled={
            busy ||
            (!question.trim() && kind === 'explain') ||
            (kind === 'recognize' && (!adminToken || !pageId))
          }
        >
          {busy
            ? '智能体运行中…'
            : kind === 'recognize'
              ? '识别并生成初译草稿'
              : '读取史料并讲解'}
        </Button>
        {busy && (
          <Button variant="outline" onClick={cancel}>
            取消后续步骤
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {reconnecting && busy && (
        <p className="notice">连接中断，正在重连同一次运行。</p>
      )}
      {events.length > 0 && (
        <div className="events" aria-live="polite">
          {events.map((e) => (
            <div className="event" key={e.id}>
              <time>{new Date(e.created_at).toLocaleTimeString()}</time>
              {e.summary ||
                e.tool ||
                e.model ||
                ({
                  'run.started': '开始运行',
                  'run.completed': '运行完成',
                  'run.failed': '运行失败',
                  'run.cancelled': '已取消',
                }[e.type] ??
                  e.type)}
            </div>
          ))}
        </div>
      )}
      {result && (
        <div>
          <div className="notice">
            {result.mode === 'preanalysis'
              ? '已完成实际预分析，草稿尚未发布。'
              : result.mode === 'insufficient_evidence'
                ? '未取得可用证据，没有生成无依据的历史解释。'
                : '本次回答来自实时模型调用；引用已经过原文匹配校验。'}
          </div>
          {result.answer && <p className="pre reading-text">{result.answer}</p>}
          {result.citations?.map((c, i) => (
            <div className="panel" key={c.citation_id + i}>
              <a className="link" href={c.reader_url}>
                《{c.title}》{c.volume} · 扫描第 {c.scan_index} 页
              </a>
              <blockquote className="pre">{c.quote}</blockquote>
              <span className="badge">
                {c.reviewed ? '人工校勘' : 'AI 识别结果，不保证完全正确'}
              </span>
            </div>
          ))}
          {result.uncertainties?.map((u, i) => (
            <p className="muted" key={i}>
              {u}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
