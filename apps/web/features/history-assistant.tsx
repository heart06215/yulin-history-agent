'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactElement } from 'react';
import { ArrowRight, BookOpen, LoaderCircle, MessageCircle, Send, Square, X } from 'lucide-react';
import { api, post, type Citation, type Result, type RunEvent } from './api';

interface Message {
  id: string;
  role: 'question' | 'answer';
  text: string;
  citations?: Citation[];
  uncertainties?: string[];
}

interface SavedRun { id: string; cursor: number }
const STORAGE = 'yulin:history-assistant';
const RUN_STORAGE = 'yulin:history-assistant-run';
const EVIDENCE_LABELS: Record<string, string> = { recognition_draft: '识别初稿 · 未校勘', live_scan: '本轮原页转录 · 未校勘', curated_history: '历史编年 · 查看依据', published: '已发布识别文本', catalog_metadata: '资料库目录 · 实时统计' };

function saveSession(key: string, value: unknown): void {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* The chat remains usable when storage is disabled. */ }
}

export function HistoryAssistant(): ReactElement {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [runId, setRunId] = useState('');
  const [ready, setReady] = useState(false);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const stream = useRef<EventSource | null>(null);
  const cursor = useRef(0);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const messageList = useRef<HTMLDivElement>(null);

  const complete = useCallback((id: string, result: Result): void => {
    setMessages((old) => old.some((message) => message.id === id) ? old : [...old, { id, role: 'answer', text: result.answer ?? '本轮没有生成讲解正文。', citations: result.citations, uncertainties: result.uncertainties }]);
    setBusy(false);
    setReconnecting(false);
    try { sessionStorage.removeItem(RUN_STORAGE); } catch {}
  }, []);

  const connectStream = useCallback((id: string, after = 0): void => {
    stream.current?.close();
    const source = new EventSource(`/api/v1/agent/runs/${id}/stream?after_event_id=${after}`);
    stream.current = source;
    source.onopen = (): void => setReconnecting(false);
    source.onerror = (): void => { setReconnecting(true); setProgress('连接暂时中断，正在恢复本轮问答…'); };
    source.addEventListener('update', (event: Event) => {
      const item = JSON.parse((event as MessageEvent<string>).data) as RunEvent;
      cursor.current = item.id;
      saveSession(RUN_STORAGE, { id, cursor: item.id });
      if (item.type === 'tool.started') setProgress(({ list_gazetteer_catalog: '查找方志目录…', list_gazetteer_pages: '定位原志扫描页…', search_gazetteer_materials: '检索原文与识别初稿…', read_unrecognized_scan: '读取尚未识别的原页…', search_history_nodes: '核对历史编年与来源…' } as Record<string, string>)[item.tool ?? ''] ?? '读取相关史料…');
      if (item.type === 'tool.completed') setProgress(item.summary ?? '正在整理讲解与原页引用…');
      if (item.type === 'run.completed') { complete(id, item.result ?? { mode: 'insufficient_evidence' }); source.close(); }
      if (item.type === 'run.failed' || item.type === 'run.cancelled') { setError(item.error ?? '本轮未完成，可保留问题后重试。'); setBusy(false); setReconnecting(false); source.close(); try { sessionStorage.removeItem(RUN_STORAGE); } catch {} }
    });
    source.addEventListener('end', (): void => {
      source.close();
      // Reconcile terminal state even when the terminal event was missed.
      void api<{ status: string; result?: Result; error?: string }>(`/agent/runs/${id}`).then((run) => {
        if (run.status === 'completed' && run.result) complete(id, run.result);
        else { setBusy(false); setError(run.error ?? '本轮问答已结束，可重新提问。'); }
      }).catch(() => { setBusy(false); setError('问答连接未能恢复，问题已保留，可再次发送。'); });
    });
  }, [complete]);

  useEffect(() => {
    let active = true;
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE) ?? 'null') as { draft?: string; messages?: Message[] } | null;
      if (saved?.draft) setDraft(saved.draft);
      if (Array.isArray(saved?.messages)) setMessages(saved.messages);
      const pending = JSON.parse(sessionStorage.getItem(RUN_STORAGE) ?? 'null') as SavedRun | null;
      if (pending?.id) {
        void api<{ status: string; result?: Result; error?: string }>(`/agent/runs/${pending.id}`).then((run) => {
          if (!active) return;
          setRunId(pending.id);
          if (run.status === 'completed' && run.result) complete(pending.id, run.result);
          else if (run.status === 'queued' || run.status === 'running') { setBusy(true); setProgress('恢复上一轮问答…'); connectStream(pending.id, pending.cursor); }
          else { setError(run.error ?? '上一轮问答已结束。'); sessionStorage.removeItem(RUN_STORAGE); }
        }).catch(() => { if (active) { setError('上一轮问答暂时无法恢复，可重新发送问题。'); sessionStorage.removeItem(RUN_STORAGE); } });
      }
    } catch {}
    setReady(true);
    void api<{ model_configured: boolean }>('/health').then((health) => { if (active) setConfigured(health.model_configured); }).catch(() => { if (active) setConfigured(false); });
    return () => { active = false; stream.current?.close(); };
  }, [complete, connectStream]);

  useEffect(() => { if (ready) saveSession(STORAGE, { draft, messages }); }, [draft, messages, ready]);
  useEffect(() => { if (open && !dialog.current?.open) { dialog.current?.showModal(); questionRef.current?.focus(); } }, [open]);
  useEffect(() => { if (open) messageList.current?.scrollTo({ top: messageList.current.scrollHeight, behavior: 'auto' }); }, [open, messages.length, busy]);
  useEffect(() => {
    const ask = (event: Event): void => { setDraft(String((event as CustomEvent<string>).detail ?? '')); setOpen(true); };
    window.addEventListener('yulin:ask', ask);
    return () => window.removeEventListener('yulin:ask', ask);
  }, []);

  function close(): void { dialog.current?.close(); setOpen(false); }

  async function send(event?: FormEvent<HTMLFormElement>): Promise<void> {
    event?.preventDefault();
    if (!draft.trim() || busy) return;
    const question = draft.trim();
    setBusy(true);
    setError('');
    setProgress('正在连接项目问答服务…');
    setReconnecting(false);
    setMessages((old) => [...old, { id: crypto.randomUUID(), role: 'question', text: question }]);
    try {
      await api('/sessions', post({}));
      // Carry a bounded conversation context; database evidence is still fetched
      // anew by the backend and earlier model answers are never evidence.
      const context = messages.slice(-4).map((message) => `${message.role === 'question' ? '此前问题' : '此前回答（仅作语境，不是证据）'}：${message.text.slice(0, 240)}`).join('\n');
      const result = await api<{ run_id: string }>('/agent/runs', post({ kind: 'explain', question: context ? `${context}\n本轮问题：${question}`.slice(-2000) : question, idempotency_key: crypto.randomUUID() }));
      setRunId(result.run_id);
      cursor.current = 0;
      saveSession(RUN_STORAGE, { id: result.run_id, cursor: 0 });
      setDraft('');
      setProgress('正在检索自有方志资料…');
      connectStream(result.run_id);
    } catch (failure) { setError((failure as Error).message); setBusy(false); }
  }

  async function cancel(): Promise<void> {
    if (!runId) return;
    try { await api(`/agent/runs/${runId}/cancel`, post({})); setProgress('正在停止本轮问答…'); } catch (failure) { setError((failure as Error).message); }
  }

  function retryLast(): void {
    const last = [...messages].reverse().find((message) => message.role === 'question');
    if (last) { setDraft(last.text); setError(''); questionRef.current?.focus(); }
  }

  function startConversation(): void {
    if (busy) return;
    stream.current?.close();
    setMessages([]);
    setDraft('');
    setError('');
    setRunId('');
    setReconnecting(false);
    try { sessionStorage.removeItem(RUN_STORAGE); } catch {}
    questionRef.current?.focus();
  }

  return <><button className="assistant-launcher" onClick={() => setOpen(true)} aria-label="打开榆林史料助手" aria-haspopup="dialog" aria-expanded={open}><span className="assistant-launcher-seal" aria-hidden="true">志</span><span>问一问史料<small>方志里的答案</small></span><MessageCircle size={18} /></button>
    <dialog ref={dialog} className="assistant-dialog" aria-labelledby="assistant-title" onCancel={close}>
      <header className="assistant-heading"><span className="assistant-mark" aria-hidden="true">志</span><div><h2 id="assistant-title">榆林史料助手</h2><p>以自有方志为据，循原页寻找答案</p></div><button onClick={close} aria-label="收起史料助手"><X size={20} /></button></header>
      <div className="assistant-scope"><BookOpen size={14} /><span>识别成果 + 未识别原页 + 历史编年</span><button type="button" onClick={startConversation} disabled={busy || !messages.length}>新对话</button></div>
      <div ref={messageList} className="assistant-messages" role="log" aria-live="polite" aria-relevant="additions">
        {!messages.length && <div className="assistant-welcome"><span className="section-kicker">有疑问，问原志</span><h3>榆林的历史，<br />你想从哪里读起？</h3><p>先检索识别文本，需要时读取原页。回答附上资料来源，识别初稿会注明未校勘。</p>{['榆林卫何时改为榆林府？', '李自成起义与陕北灾荒有什么关系？', '榆林的治沙建设取得了哪些成效？'].map((question) => <button key={question} onClick={() => { setDraft(question); questionRef.current?.focus(); }}>{question}<ArrowRight size={14} /></button>)}</div>}
        {messages.map((message) => <article key={message.id} className={`assistant-message message-${message.role}`}><span className="message-label">{message.role === 'question' ? '你的问题' : '史料讲解'}</span><p>{message.text}</p>{message.citations?.length ? <div className="assistant-citations"><strong>本轮依据</strong>{message.citations.map((citation, index) => <a href={citation.reader_url} key={citation.citation_id} target="_blank" rel="noreferrer"><span>{index + 1}</span><div>《{citation.title}》{citation.volume}{citation.scan_index > 0 ? ` · 第${citation.scan_index}页` : ''}<small>{EVIDENCE_LABELS[citation.evidence_status ?? ''] ?? (citation.reviewed ? '人工校勘' : '识别文本 · 未校勘')}</small><blockquote>{citation.quote}</blockquote></div><ArrowRight size={14} /></a>)}</div> : null}{message.uncertainties?.length ? <details className="assistant-uncertainties"><summary>仍待核实的部分</summary><ul>{message.uncertainties.map((note) => <li key={note}>{note}</li>)}</ul></details> : null}</article>)}
        {busy && <div className="assistant-progress" role="status"><LoaderCircle size={16} className={reconnecting ? '' : 'busy-icon'} />{progress}</div>}
        {error && <div className="assistant-error" role="alert"><p>{error}</p><button onClick={retryLast} disabled={busy}>修改问题后重试</button></div>}
      </div>
      <form className="assistant-form" onSubmit={(event) => void send(event)}><label htmlFor="history-question">你的问题</label><textarea ref={questionRef} id="history-question" placeholder="例如：榆林这个地名是怎样演变的？" maxLength={1000} rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void send(); } }} /><div><small>{configured === false ? '服务尚未就绪，可到整理工作台查看配置' : 'Ctrl / ⌘ + Enter 发送 · 项目 API'}</small>{busy ? <button className="heritage-outline" type="button" onClick={() => void cancel()}><Square size={14} />停止回答</button> : <button className="heritage-button" type="submit" disabled={!draft.trim()} title={!draft.trim() ? '输入问题后即可发送' : '检索资料并生成讲解'}><Send size={14} />发送问题</button>}</div></form>
    </dialog>
  </>;
}
