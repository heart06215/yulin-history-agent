'use client';
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { Shell } from '@/features/shell';
import { RunPanel } from '@/features/run-panel';
import { TimelineAdmin } from '@/features/timeline-admin';
import { diffSegments } from '@/features/text-diff';
import {
  api,
  post,
  auth,
  ApiError,
  type Source,
  type Page,
  type Version,
} from '@/features/api';

interface AdminVersion {
  id: string;
  method: string;
  model: string;
  reviewed: number;
  published: number;
  ever_published: number;
  created_at: string;
  base_version_id: string | null;
  revision_note: string;
  transcription_length: number;
  translation_length: number;
}

function parseNotes(raw?: string): string {
  if (!raw) return '';
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value.join('\n') : String(value);
  } catch {
    return raw;
  }
}

function toNotes(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 30);
}

export default function Workbench() {
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [index, setIndex] = useState(1);
  const [token, setToken] = useState('');
  const [page, setPage] = useState<Page | null>(null);
  const [text, setText] = useState('');
  const [translation, setTranslation] = useState('');
  const [notes, setNotes] = useState('');
  const [revisionNote, setRevisionNote] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const [base, setBase] = useState<{
    id: string;
    transcription: string;
    translation: string;
    notes: string;
  } | null>(null);
  const [versions, setVersions] = useState<AdminVersion[]>([]);
  const [showDiff, setShowDiff] = useState(false);
  const [conflictId, setConflictId] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const selected = sources.find((s) => s.id === sourceId);
  const pageId = sourceId ? `${sourceId}-p${index}` : '';
  const dirty =
    !!base &&
    (text !== base.transcription ||
      translation !== base.translation ||
      notes !== base.notes);
  const diff = useMemo(
    () => (base ? diffSegments(base.transcription, text) : []),
    [base, text],
  );

  useEffect(() => {
    api<Source[]>('/sources')
      .then((s) => {
        setSources(s);
        const p = new URLSearchParams(window.location.search);
        setSourceId(p.get('source') || s[0]?.id || '');
        const n = Number(p.get('page'));
        if (n > 0 && Number.isInteger(n)) setIndex(n);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    setPage(null);
    setReviewed(false);
    setNotice('');
    setVersions([]);
  }, [sourceId, index, token]);
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  function handleError(e: unknown) {
    if (e instanceof ApiError && e.status === 409) {
      const current = (
        e.payload as { detail?: { current_version_id?: string } } | null
      )?.detail?.current_version_id;
      setConflictId(current || '');
      setError(
        '保存冲突：该页已有更新的草稿版本' +
          (current ? `（${current.slice(0, 8)}…）` : '') +
          '。你的修改仍保留在编辑框中，可比较后用最新版本作基线重试。',
      );
      return;
    }
    setError((e as Error).message);
  }

  async function load() {
    setError('');
    setConflictId('');
    try {
      const p = await api<Page>('/admin/pages/' + pageId, {
        headers: auth(token),
      });
      setPage(p);
      setText(p.version?.transcription || '');
      setTranslation(p.version?.translation || '');
      setNotes(parseNotes(p.version?.notes));
      setBase(
        p.version
          ? {
              id: p.version.id,
              transcription: p.version.transcription,
              translation: p.version.translation,
              notes: parseNotes(p.version.notes),
            }
          : null,
      );
      setRevisionNote('');
      setReviewed(false);
      setVersions(
        await api<AdminVersion[]>('/admin/pages/' + pageId + '/versions', {
          headers: auth(token),
        }),
      );
    } catch (e) {
      handleError(e);
    }
  }

  async function loadVersionIntoEditor(versionId: string) {
    setError('');
    try {
      const v = await api<Version>('/admin/versions/' + versionId, {
        headers: auth(token),
      });
      setText(v.transcription);
      setTranslation(v.translation);
      setNotes(parseNotes(v.notes));
      setBase({
        id: v.id,
        transcription: v.transcription,
        translation: v.translation,
        notes: parseNotes(v.notes),
      });
      setNotice(
        '已将版本 ' + versionId.slice(0, 8) + '… 载入编辑框作为基线。',
      );
    } catch (e) {
      handleError(e);
    }
  }

  async function reviseFrom(baseVersionId: string) {
    return api<{ version_id: string }>(
      '/admin/versions/' + baseVersionId + '/revise',
      {
        ...post({
          transcription: text,
          translation,
          notes: toNotes(notes),
          base_version_id: baseVersionId,
          revision_note: revisionNote,
        }),
        headers: auth(token),
      },
    );
  }

  async function saveDraft() {
    if (!base || !text.trim()) return;
    setSaving(true);
    setError('');
    try {
      const v = await reviseFrom(base.id);
      setNotice(
        '已保存为未发布草稿（' +
          v.version_id.slice(0, 8) +
          '…）。公开阅读器不会因此变化。',
      );
      await load();
    } catch (e) {
      handleError(e);
    } finally {
      setSaving(false);
    }
  }

  async function rebaseToLatest() {
    if (!conflictId) return;
    setError('');
    try {
      const latest = await api<Page>('/admin/pages/' + pageId, {
        headers: auth(token),
      });
      setBase(
        latest.version
          ? {
              id: latest.version.id,
              transcription: latest.version.transcription,
              translation: latest.version.translation,
              notes: parseNotes(latest.version.notes),
            }
          : null,
      );
      setConflictId('');
      setNotice(
        '已切换到最新草稿作基线，编辑内容未改动；请比较差异后再保存。',
      );
    } catch (e) {
      handleError(e);
    }
  }

  async function publish(reviewedFlag: boolean) {
    if (!base || !text.trim()) return;
    setSaving(true);
    setError('');
    try {
      let id = base.id;
      if (dirty) {
        const v = await reviseFrom(base.id);
        id = v.version_id;
      }
      await api('/admin/versions/' + id + '/publish', {
        ...post({ reviewed: reviewedFlag, reason: revisionNote }),
        headers: auth(token),
      });
      setNotice(
        reviewedFlag
          ? '已发布人工校勘版本。'
          : '已发布 AI 初译，并明确标记未人工校勘。',
      );
      await load();
    } catch (e) {
      handleError(e);
    } finally {
      setSaving(false);
    }
  }

  async function withdrawPublication() {
    setSaving(true);
    setError('');
    try {
      await api('/admin/pages/' + pageId + '/publication', {
        method: 'DELETE',
        headers: auth(token),
      });
      setNotice('已撤回当前公开文本；历史版本仍保留，不会自动恢复上一版。');
      await load();
    } catch (e) {
      handleError(e);
    } finally {
      setSaving(false);
    }
  }

  async function republish(versionId: string) {
    setSaving(true);
    setError('');
    try {
      await api('/admin/pages/' + pageId + '/publication', {
        ...post({ version_id: versionId }),
        method: 'PUT',
        headers: auth(token),
      });
      setNotice('已将该历史版本重新设为当前公开文本。');
      await load();
    } catch (e) {
      handleError(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Shell>
      <section className="intro">
        <p className="eyebrow">智能体工作台</p>
        <h1>先整理史料，再让助手讲解。</h1>
        <p>
          保存草稿、发布 AI 初译与确认校勘后发布是三个独立动作；公开内容只在发布时改变。
        </p>
      </section>
      <section className="panel">
        <div className="two-col">
          <label>
            管理员令牌
            <input
              type="password"
              className="field"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              placeholder="填写管理员访问令牌"
            />
          </label>
          <label>
            文献材料
            <Select
              value={sourceId}
              onValueChange={(v) => {
                setSourceId(v || '');
                setIndex(1);
              }}
            >
              <SelectTrigger
                className="field"
                style={{ height: 46, width: '100%' }}
              >
                <SelectValue>
                  {selected
                    ? selected.title + ' · ' + selected.volume
                    : '选择文献'}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {sources.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.title} · {s.volume}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
        <div className="row">
          <label>
            扫描页号
            <input
              className="field"
              style={{ width: 120 }}
              type="number"
              min={1}
              max={selected?.page_count}
              value={index}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (
                  Number.isInteger(n) &&
                  n > 0 &&
                  n <= (selected?.page_count || 1)
                )
                  setIndex(n);
              }}
            />
          </label>
          <span className="muted">共 {selected?.page_count ?? 0} 页</span>
          <Button variant="outline" onClick={load} disabled={!token || !pageId}>
            读取最新草稿
          </Button>
          {sourceId && (
            <a className="link" href={`/library/${sourceId}?page=${index}`}>
              查看原页
            </a>
          )}
        </div>
        <p className="muted">
          令牌仅保存在当前页面内存中。模型密钥在后端 .env 配置，不填写在网页。
        </p>
      </section>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="notice">{notice}</p>}
      <RunPanel
        key={pageId}
        kind="recognize"
        pageId={pageId}
        sourceId={sourceId}
        adminToken={token}
        onComplete={load}
      />
      {page && (
        <section className="panel">
          <h2>审校与发布</h2>
          {page.version ? (
            <>
              <div className="two-col">
                <label>
                  识别原文
                  <textarea
                    className="field reading-text"
                    rows={14}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                  />
                </label>
                <label>
                  白话初译
                  <textarea
                    className="field reading-text"
                    rows={14}
                    value={translation}
                    onChange={(e) => setTranslation(e.target.value)}
                  />
                </label>
              </div>
              <div className="two-col">
                <label>
                  疑难说明（每行一条）
                  <textarea
                    className="field"
                    rows={3}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                </label>
                <label>
                  本次修订原因
                  <input
                    className="field"
                    value={revisionNote}
                    onChange={(e) => setRevisionNote(e.target.value)}
                    maxLength={500}
                    placeholder="例如：对照原图订正两处异体字"
                  />
                </label>
              </div>
              <label className="check">
                <Checkbox
                  checked={reviewed}
                  onCheckedChange={(v) => setReviewed(Boolean(v))}
                />
                我已逐字核对原页，并完成原文与译文的人工校勘
              </label>
              <div className="row controls">
                <Button
                  variant="outline"
                  onClick={saveDraft}
                  disabled={saving || !base || !text.trim()}
                >
                  保存草稿（不发布）
                </Button>
                <Button
                  variant="outline"
                  onClick={() => publish(false)}
                  disabled={saving || !base || !text.trim()}
                >
                  发布 AI 初译
                </Button>
                <Button
                  className="action"
                  onClick={() => publish(true)}
                  disabled={saving || !base || !text.trim() || !reviewed}
                  title={reviewed ? '' : '需先勾选人工校勘'}
                >
                  {saving ? '正在处理…' : '确认校勘后发布'}
                </Button>
                {conflictId && (
                  <Button variant="outline" onClick={rebaseToLatest}>
                    用最新草稿作基线重试
                  </Button>
                )}
                <Button
                  variant="outline"
                  onClick={withdrawPublication}
                  disabled={saving}
                >
                  撤回当前公开文本
                </Button>
              </div>
              <p className="muted">
                保存草稿只生成新版本，公开阅读器保持不变。保存失败时输入框内容不会被清空。
              </p>
              {dirty && (
                <p className="notice">编辑框内容尚未保存（与最近版本不同）。</p>
              )}
              <div className="row controls">
                <Button
                  variant="outline"
                  onClick={() => setShowDiff((v) => !v)}
                  disabled={!dirty}
                >
                  {showDiff ? '隐藏原文差异' : '查看原文差异'}
                </Button>
              </div>
              {showDiff && dirty && (
                <p className="pre reading-text" aria-label="原文差异">
                  {diff.map((part, i) => (
                    <span
                      key={i}
                      className={
                        part.type === 'removed'
                          ? 'diff-removed'
                          : part.type === 'added'
                            ? 'diff-added'
                            : undefined
                      }
                    >
                      {part.text}
                    </span>
                  ))}
                </p>
              )}
              <details className="panel">
                <summary>版本历史（{versions.length}）</summary>
                <table className="table">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>状态</th>
                      <th>方式</th>
                      <th>修订说明</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((v) => (
                      <tr key={v.id}>
                        <td>{new Date(v.created_at).toLocaleString()}</td>
                        <td>
                          {v.published
                            ? '当前发布'
                            : v.ever_published
                              ? '历史发布'
                              : '草稿'}
                          {v.reviewed ? ' · 人工校勘' : ' · AI 初译'}
                        </td>
                        <td>{v.method}</td>
                        <td>{v.revision_note || '—'}</td>
                        <td>
                          <div className="row">
                            <Button
                              variant="outline"
                              onClick={() => loadVersionIntoEditor(v.id)}
                              disabled={v.id === base?.id}
                            >
                              以此为基线
                            </Button>
                            {!v.published && (
                              <Button
                                variant="outline"
                                onClick={() => republish(v.id)}
                                disabled={saving}
                              >
                                重新发布此版
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </>
          ) : (
            <p className="empty">本页尚无草稿，可运行上方识别 Agent。</p>
          )}
        </section>
      )}
      <TimelineAdmin token={token} />
    </Shell>
  );
}
