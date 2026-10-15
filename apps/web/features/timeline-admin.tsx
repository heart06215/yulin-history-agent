'use client';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { api, post, auth, type TimelineEvent } from './api';

const emptyForm = {
  sort_year: '',
  year_label: '',
  title: '',
  body: '',
  event_type: 'gazetteer',
  source_note: '',
  source_url: '',
};

const TYPE_LABELS: Record<string, string> = {
  name_change: '地名建置',
  gazetteer: '志书刊行',
  event: '历史事件',
  milestone: '整理节点',
};

export function TimelineAdmin({ token }: { token: string }) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setEvents(await api<TimelineEvent[]>('/admin/timeline', { headers: auth(token) }));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, [token]);

  useEffect(() => {
    if (token) void load();
  }, [token, load]);

  function set(key: keyof typeof emptyForm, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit() {
    const year = Number(form.sort_year);
    if (!Number.isInteger(year) || year < -9999 || year > 9999) {
      setError('排序年份必须是 -9999–9999 的整数（例如公元前 221 年填 -221，康熙二十年填 1681）。');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api(
        '/admin/timeline',
        post({
          sort_year: year,
          year_label: form.year_label || String(year),
          title: form.title,
          body: form.body,
          event_type: form.event_type,
          source_note: form.source_note,
          source_url: form.source_url,
        }),
      );
      setNotice('节点已创建，默认未发布；确认依据后自行发布。');
      setForm(emptyForm);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(e: TimelineEvent) {
    setError('');
    try {
      await api(
        '/admin/timeline/' + e.id + '/publish',
        { ...post({ published: !e.published }) },
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(e: TimelineEvent) {
    setError('');
    try {
      await api('/admin/timeline/' + e.id, { method: 'DELETE' });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <section className="panel">
      <h2>历史长卷节点</h2>
      <p className="muted">
        只收录能注明依据的事件（来源元数据、已核对志书内容、通史通说或整理记录）；通说条目须在依据中注明待核对。节点默认未发布，公开页面只显示已发布节点。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="notice">{notice}</p>}
      {token && events.length > 0 && (
        <div className="timeline-admin-list">
          {events.map((e) => (
            <div className="row space" key={e.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0', gap: 10 }}>
              <div style={{ minWidth: 0 }}>
                <strong>
                  {e.year_label} · {e.title}
                </strong>{' '}
                <span className="badge">{TYPE_LABELS[e.event_type] ?? e.event_type}</span>{' '}
                <span className="badge">{e.published ? '已发布' : '未发布'}</span>
              </div>
              <div className="row" style={{ flexWrap: 'nowrap' }}>
                <Button variant="outline" onClick={() => toggle(e)}>
                  {e.published ? '取消发布' : '发布'}
                </Button>
                <Button variant="outline" onClick={() => remove(e)}>
                  删除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
      {token && events.length === 0 && <p className="empty">还没有节点，可用下方表单创建。</p>}
      <h3 style={{ marginTop: 18 }}>新增节点</h3>
      <div className="two-col">
        <label>
          排序年份（如 1681，公元前填负数）
          <input
            className="field"
            type="number"
            min={-9999}
            max={9999}
            value={form.sort_year}
            onChange={(e) => set('sort_year', e.target.value)}
          />
        </label>
        <label>
          显示年代（如 清康熙二十年（1681））
          <input
            className="field"
            value={form.year_label}
            onChange={(e) => set('year_label', e.target.value)}
            placeholder="留空时显示排序年份"
          />
        </label>
        <label>
          节点类型
          <select
            className="field"
            value={form.event_type}
            onChange={(e) => set('event_type', e.target.value)}
          >
            <option value="gazetteer">志书刊行</option>
            <option value="event">历史事件</option>
            <option value="milestone">整理节点</option>
          </select>
        </label>
        <label>
          依据出处（必填）
          <input
            className="field"
            value={form.source_note}
            onChange={(e) => set('source_note', e.target.value)}
            placeholder="例如：来源文件元数据 / 原书卷首题名"
          />
        </label>
      </div>
      <label>
        标题
        <input className="field" value={form.title} onChange={(e) => set('title', e.target.value)} />
      </label>
      <label>
        正文
        <textarea
          className="field"
          rows={4}
          value={form.body}
          onChange={(e) => set('body', e.target.value)}
        />
      </label>
      <label>
        来源链接（可选）
        <input
          className="field"
          value={form.source_url}
          onChange={(e) => set('source_url', e.target.value)}
          placeholder="https://…"
        />
      </label>
      <div className="row controls">
        <Button
          className="action"
          onClick={submit}
          disabled={busy || !token || !form.title.trim() || !form.body.trim() || !form.source_note.trim()}
        >
          {busy ? '正在保存…' : '创建节点'}
        </Button>
        {!token && <span className="muted">填写上方管理员令牌后可管理节点。</span>}
      </div>
    </section>
  );
}
