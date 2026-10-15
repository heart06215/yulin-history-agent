'use client';
import { useCallback, useEffect, useState, type ReactElement } from 'react';
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
import { api, post, auth, ApiError, type Source } from '@/features/api';

interface Health {
  status: string;
  text_model: string;
  vision_model: string;
  model_configured: boolean;
  config_error: string;
  admin_configured: boolean;
}

interface ImportRow {
  id: string;
  filename: string;
  status: string;
  sha256: string;
  source_id: string | null;
  error: string;
  created_at: string;
}

interface Edition {
  id: string;
  title: string;
  completeness: string;
  materials: number;
  pages: number;
}

interface DedupGroup {
  kind: string;
  key: string;
  score: number;
  method: string;
  pages: {
    id: string;
    scan_index: number;
    title: string;
    volume: string;
    edition_id: string;
    superseded_by: string | null;
  }[];
  candidate_pairs: {
    page_id: string;
    version_id: string;
    other_page_id: string;
    other_version_id: string;
  }[];
}

interface BatchItem {
  id: string;
  page_id: string;
  scan_index: number;
  status: string;
  error: string;
  run_id: string | null;
}

interface Batch {
  id: string;
  source_id: string;
  control: string;
  result: string;
  total: number;
  created_at: string;
  items: BatchItem[];
  summary: {
    total: number;
    completed: number;
    failed: number;
    skipped: number;
    cancelled: number;
    pending: number;
    result: string;
  };
}

type Tab = 'import' | 'batch' | 'dedup';

const BATCH_STATUS_LABELS: Record<string, string> = {
  active: '运行中', paused: '已暂停', cancelled: '已取消',
  pending: '待处理', running: '识别中', completed: '已完成',
  failed: '失败', partial_failed: '部分失败', skipped: '已跳过',
};

function batchStatusLabel(status: string): string {
  return BATCH_STATUS_LABELS[status] ?? status;
}

function batchSourceLabel(sourceId: string, sources: Source[]): string {
  const source = sources.find((item) => item.id === sourceId);
  return source ? `${source.title} · ${source.volume}` : sourceId;
}

const RIGHTS: [string, string][] = [
  ['pending', '待核许可（不公开）'],
  ['source_declared_public_domain', '来源声明公有领域'],
  ['restricted', '受限（仅整理）'],
];

export default function OpsConsole() {
  const [tab, setTab] = useState<Tab>('batch');
  const [token, setToken] = useState('');
  const [health, setHealth] = useState<Health | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const authed = token.length > 0;

  const refreshSources = useCallback(async () => {
    setSources(await api<Source[]>('/sources'));
  }, []);

  useEffect(() => {
    api<Health>('/health')
      .then(setHealth)
      .catch((e) => setError((e as Error).message));
    refreshSources().catch((e) => setError((e as Error).message));
  }, [refreshSources]);

  function handleError(e: unknown) {
    if (e instanceof ApiError) setError(e.message);
    else setError((e as Error).message);
  }

  return (
    <Shell>
      <section className="intro">
        <p className="eyebrow">识别 · 导入 · 去重</p>
        <h1>运行台</h1>
        <p>
          上传导入材料、批量识别县志、检查重复页。令牌仅存于本页内存，模型密钥在后端 .env。
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
              placeholder="管理员访问令牌"
            />
          </label>
          <div>
            <p className="muted">模型状态</p>
            {health ? (
              <p>
                {health.model_configured ? '已配置' : '未配置'} · 文本{' '}
                {health.text_model || '—'} · 视觉 {health.vision_model || '—'}
              </p>
            ) : (
              <p className="muted">读取中…</p>
            )}
          </div>
        </div>
        {health?.config_error && (
          <p className="error" role="alert">
            {health.config_error}
          </p>
        )}
        <div className="row controls">
          <Button
            variant={tab === 'import' ? undefined : 'outline'}
            onClick={() => setTab('import')}
          >
            导入材料
          </Button>
          <Button
            variant={tab === 'batch' ? undefined : 'outline'}
            onClick={() => setTab('batch')}
          >
            批次识别
          </Button>
          <Button
            variant={tab === 'dedup' ? undefined : 'outline'}
            onClick={() => setTab('dedup')}
          >
            重复页去重
          </Button>
          <a className="link" href="/workbench">
            校勘与发布
          </a>
          <a className="link" href="/workbench/usage">
            用量
          </a>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="notice">{notice}</p>}
      </section>

      {tab === 'import' && (
        <ImportPanel
          token={token}
          authed={authed}
          onError={handleError}
          onNotice={setNotice}
          onImported={refreshSources}
        />
      )}
      {tab === 'batch' && (
        <BatchPanel
          token={token}
          authed={authed}
          sources={sources}
          health={health}
          onError={handleError}
          onNotice={setNotice}
          onChanged={refreshSources}
        />
      )}
      {tab === 'dedup' && (
        <DedupPanel
          token={token}
          authed={authed}
          onError={handleError}
          onNotice={setNotice}
          onChanged={refreshSources}
        />
      )}
    </Shell>
  );
}

function ImportPanel({
  token,
  authed,
  onError,
  onNotice,
  onImported,
}: {
  token: string;
  authed: boolean;
  onError: (e: unknown) => void;
  onNotice: (s: string) => void;
  onImported: () => void;
}) {
  const [editionId, setEditionId] = useState('');
  const [title, setTitle] = useState('');
  const [sourceVolume, setSourceVolume] = useState('');
  const [verifiedVolume, setVerifiedVolume] = useState('');
  const [year, setYear] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [rights, setRights] = useState('pending');
  const [licenseNote, setLicenseNote] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [imports, setImports] = useState<ImportRow[]>([]);
  const [editions, setEditions] = useState<Edition[]>([]);

  const refresh = useCallback(async () => {
    if (!authed) return;
    try {
      const history = await api<{ items: ImportRow[] }>('/admin/imports', {
        headers: auth(token),
      });
      setImports(history.items);
      setEditions(
        await api<Edition[]>('/admin/editions', { headers: auth(token) }),
      );
    } catch (e) {
      onError(e);
    }
  }, [authed, token, onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function upload() {
    if (!file || !editionId.trim()) {
      onError(new Error('请选择文件并填写版本标识（edition_id）。'));
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.set('file', file);
      form.set('edition_id', editionId.trim());
      form.set('edition_title', title.trim());
      form.set('title', title.trim());
      form.set('source_volume', sourceVolume.trim());
      form.set('verified_volume', verifiedVolume.trim());
      form.set('year', year.trim());
      form.set('source_url', sourceUrl.trim());
      form.set('rights_status', rights);
      form.set('license_note', licenseNote.trim());
      const r = await api<{
        status: string;
        page_count?: number;
        message?: string;
      }>('/admin/imports', {
        method: 'POST',
        body: form,
        headers: auth(token),
      });
      onNotice(
        r.status === 'duplicate_content'
          ? `相同内容已存在（${r.message}）`
          : `已导入 ${r.page_count} 页。`,
      );
      setFile(null);
      await refresh();
      onImported();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>上传材料（PDF / JPEG / PNG）</h2>
        <div className="two-col">
          <label>
            版本标识 edition_id（必填，相同标识归为同一版本）
            <input
              className="field"
              value={editionId}
              onChange={(e) => setEditionId(e.target.value)}
              placeholder="例如 mizhi-1681"
            />
          </label>
          <label>
            书名
            <input
              className="field"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="米脂县志"
            />
          </label>
          <label>
            来源卷名（文件标注）
            <input
              className="field"
              value={sourceVolume}
              onChange={(e) => setSourceVolume(e.target.value)}
              placeholder="卷三十九"
            />
          </label>
          <label>
            核验卷名（人工核对后）
            <input
              className="field"
              value={verifiedVolume}
              onChange={(e) => setVerifiedVolume(e.target.value)}
              placeholder="卷三十八"
            />
          </label>
          <label>
            刊行年
            <input
              className="field"
              type="number"
              value={year}
              onChange={(e) => setYear(e.target.value)}
              placeholder="1681"
            />
          </label>
          <label>
            许可状态
            <Select value={rights} onValueChange={(v) => setRights(v || 'pending')}>
              <SelectTrigger className="field" style={{ height: 46, width: '100%' }}>
                <SelectValue>
                  {RIGHTS.find(([v]) => v === rights)?.[1]}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {RIGHTS.map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
        <div className="two-col">
          <label>
            来源 URL
            <input
              className="field"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://…"
            />
          </label>
          <label>
            许可依据说明
            <input
              className="field"
              value={licenseNote}
              onChange={(e) => setLicenseNote(e.target.value)}
              placeholder="来源声明公有领域"
            />
          </label>
        </div>
        <label>
          文件
          <input
            className="field"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </label>
        <div className="row controls">
          <Button className="action" onClick={upload} disabled={busy || !authed}>
            {busy ? '正在导入…' : '上传并导入'}
          </Button>
          <Button variant="outline" onClick={refresh} disabled={!authed}>
            刷新
          </Button>
        </div>
        <p className="muted">
          按文件头判断真实格式，不信任扩展名；同内容按 SHA-256 去重；待核许可的材料不会公开。
        </p>
      </section>

      <section className="panel">
        <h2>版本（editions）</h2>
        {editions.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>edition_id</th>
                <th>书名</th>
                <th>完整性</th>
                <th>材料</th>
                <th>扫描页</th>
              </tr>
            </thead>
            <tbody>
              {editions.map((e) => (
                <tr key={e.id}>
                  <td>{e.id}</td>
                  <td>{e.title}</td>
                  <td>{e.completeness}</td>
                  <td>{e.materials}</td>
                  <td>{e.pages}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">尚无版本记录。</p>
        )}
      </section>

      <section className="panel">
        <h2>导入记录</h2>
        {imports.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>时间</th>
                <th>文件</th>
                <th>状态</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {imports.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td>{r.filename}</td>
                  <td>{r.status}</td>
                  <td>{r.error || r.source_id || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">尚无导入记录。</p>
        )}
      </section>
    </>
  );
}

function BatchPanel({
  token,
  authed,
  sources,
  health,
  onError,
  onNotice,
  onChanged,
}: {
  token: string;
  authed: boolean;
  sources: Source[];
  health: Health | null;
  onError: (e: unknown) => void;
  onNotice: (s: string) => void;
  onChanged: () => void;
}): ReactElement {
  const [sourceId, setSourceId] = useState("");
  const [from, setFrom] = useState(1);
  const [to, setTo] = useState(10);
  const [onlyUnrecognized, setOnlyUnrecognized] = useState(true);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [history, setHistory] = useState<Batch[]>([]);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const selected = sources.find((s) => s.id === sourceId);
  const live = batch && !["completed", "failed", "partial_failed", "cancelled"].includes(batch.result);

  const refreshHistory = useCallback(async (): Promise<void> => {
    if (!authed) return;
    try {
      const r = await api<{ items: Batch[] }>("/admin/batches", { headers: auth(token) });
      setHistory(r.items);
    } catch (e) {
      onError(e);
    }
  }, [authed, token, onError]);

  const refreshBatch = useCallback(
    async (id: string): Promise<void> => {
      try {
        setBatch(await api<Batch>("/admin/batches/" + id, { headers: auth(token) }));
      } catch (e) {
        onError(e);
      }
    },
    [token, onError],
  );

  useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  // Poll only while a batch is live; stop once terminal.
  useEffect(() => {
    if (!live || !batch) return;
    const timer = setInterval(() => refreshBatch(batch.id), 2000);
    return () => clearInterval(timer);
  }, [live, batch, refreshBatch]);

  async function createBatch(): Promise<void> {
    if (!authed || !sourceId) return;
    setBusy(true);
    try {
      const r = await api<{ batch_id: string }>("/admin/batches", {
        ...post({
          source_id: sourceId,
          from_scan: from,
          to_scan: to,
          only_unrecognized: onlyUnrecognized,
        }),
        headers: auth(token),
      });
      onNotice("批次已创建，识别任务将在后台依次执行。");
      await refreshBatch(r.batch_id);
      await refreshHistory();
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  async function control(state: "active" | "paused" | "cancelled"): Promise<void> {
    if (!batch) return;
    setBusy(true);
    try {
      await api("/admin/batches/" + batch.id, {
        ...post({ control: state }),
        method: "PATCH",
        headers: auth(token),
      });
      onNotice(
        state === "paused"
          ? "批次已暂停，正在处理的页面完成后停止继续识别。"
          : state === "cancelled"
            ? "批次已取消，已完成的识别成果会保留。"
            : "批次已恢复。",
      );
      await refreshBatch(batch.id);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  async function retryFailed(): Promise<void> {
    if (!batch) return;
    setBusy(true);
    try {
      const r = await api<{ retried: number }>("/admin/batches/" + batch.id + "/retries", {
        method: "POST",
        headers: auth(token),
        body: JSON.stringify({}),
      });
      onNotice(`已重新排队 ${r.retried} 个失败页面。`);
      await refreshBatch(batch.id);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  const s = batch?.summary;
  const items = batch?.items || [];
  const shown = expanded ? items : items.slice(0, 30);

  return (
    <>
      <section className="panel">
        <h2>批量识别原志扫描页</h2>
        <div className="two-col">
          <label>
            选择材料
            <Select
              value={sourceId}
              onValueChange={(v) => {
                setSourceId(v || "");
                setBatch(null);
              }}
            >
              <SelectTrigger className="field" style={{ height: 46, width: "100%" }}>
                <SelectValue>
                  {selected ? `${selected.title} · ${selected.volume}` : "请选择材料"}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {sources.map((x) => (
                  <SelectItem key={x.id} value={x.id}>
                    {x.title} · {x.volume}（{x.page_count} 页）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <div className="row">
            <label>
              起始页
              <input
                className="field"
                style={{ width: 100 }}
                type="number"
                min={1}
                value={from}
                onChange={(e) => setFrom(Number(e.target.value) || 1)}
              />
            </label>
            <label>
              结束页
              <input
                className="field"
                style={{ width: 100 }}
                type="number"
                min={1}
                value={to}
                onChange={(e) => setTo(Number(e.target.value) || 1)}
              />
            </label>
          </div>
        </div>
        <label className="check">
          <Checkbox
            checked={onlyUnrecognized}
            onCheckedChange={(v) => setOnlyUnrecognized(Boolean(v))}
          />
          仅识别尚未识别的页面
        </label>
        <div className="row controls">
          <Button
            className="action"
            onClick={createBatch}
            disabled={busy || !authed || !sourceId || !health?.model_configured}
          >
            创建批次
          </Button>
          <Button variant="outline" onClick={refreshHistory} disabled={!authed}>
            刷新批次记录
          </Button>
          {!health?.model_configured && (
            <span className="muted">模型尚未配置，暂不能开始识别</span>
          )}
        </div>
        <p className="muted">
          批次逐页执行，识别成果自动保存为初稿；暂停或取消不会删除已完成的成果。疑难字与白话初译仍需人工校勘。
        </p>
      </section>

      {batch && (
        <section className="panel">
          <div className="row space">
            <h2>当前批次</h2>
            <span className="badge">
              {batchStatusLabel(batch.control)} · {batchStatusLabel(batch.result)}
            </span>
          </div>
          {s && (
            <p className="stat-note">
              完成 {s.completed} · 失败 {s.failed} · 跳过 {s.skipped} · 取消{" "}
              {s.cancelled} · 待处理 {s.pending} / 共 {s.total} 页
            </p>
          )}
          <div className="row controls">
            {batch.control === "active" ? (
              <Button variant="outline" onClick={() => control("paused")} disabled={busy}>
                暂停批次
              </Button>
            ) : (
              batch.control === "paused" && (
                <Button variant="outline" onClick={() => control("active")} disabled={busy}>
                  继续识别
                </Button>
              )
            )}
            <Button
              variant="outline"
              onClick={() => control("cancelled")}
              disabled={busy || batch.control === "cancelled"}
            >
              取消批次
            </Button>
            <Button
              variant="outline"
              onClick={retryFailed}
              disabled={busy || (s?.failed || 0) === 0}
            >
              重试失败页面
            </Button>
          </div>

          <div className="batch-grid">
            {shown.map((item) => (
              <div key={item.id} className="batch-cell" title={item.error || batchStatusLabel(item.status)}>
                <img
                  src={"/api/v1/pages/" + item.page_id + "/image"}
                  alt={`原志第 ${item.scan_index} 页`}
                  loading="lazy"
                />
                <span className={"batch-tag batch-" + item.status}>
                  {item.scan_index}
                </span>
              </div>
            ))}
          </div>
          {items.length > 30 && (
            <div className="row controls">
              <Button variant="outline" onClick={() => setExpanded((v) => !v)}>
                {expanded ? "收起页面" : `展开全部 ${items.length} 页`}
              </Button>
            </div>
          )}

          <table className="table">
            <thead>
              <tr>
                <th>扫描页</th>
                <th>状态</th>
                <th>运行记录 / 错误</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((item) => (
                <tr key={item.id}>
                  <td>第 {item.scan_index} 页</td>
                  <td>{batchStatusLabel(item.status)}</td>
                  <td>{item.error || item.run_id || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="panel">
        <h2>批次记录</h2>
        {history.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>创建时间</th>
                <th>材料</th>
                <th>总页数</th>
                <th>控制状态</th>
                <th>执行结果</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {history.map((b) => (
                <tr key={b.id}>
                  <td>{new Date(b.created_at).toLocaleString()}</td>
                  <td>{batchSourceLabel(b.source_id, sources)}</td>
                  <td>{b.total}</td>
                  <td>{batchStatusLabel(b.control)}</td>
                  <td>{batchStatusLabel(b.result)}</td>
                  <td>
                    <Button variant="outline" onClick={() => refreshBatch(b.id)}>
                      查看批次
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">{authed ? "尚无批次记录。" : "输入管理员令牌后可查看批次记录。"}</p>
        )}
      </section>
    </>
  );
}

function DedupPanel({
  token,
  authed,
  onError,
  onNotice,
  onChanged,
}: {
  token: string;
  authed: boolean;
  onError: (e: unknown) => void;
  onNotice: (s: string) => void;
  onChanged: () => void;
}) {
  const [groups, setGroups] = useState<DedupGroup[]>([]);
  const [stats, setStats] = useState<{
    pages_total: number;
    pages_superseded: number;
    shared_hash_pages: number;
    reports: number;
    merge_enabled: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [judging, setJudging] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    if (!authed) return;
    try {
      const g = await api<{ items: DedupGroup[] }>('/admin/dedup', {
        headers: auth(token),
      });
      setGroups(g.items);
      setStats(
        await api('/admin/dedup/stats', { headers: auth(token) }) as typeof stats,
      );
    } catch (e) {
      onError(e);
    }
  }, [authed, token, onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function decide(
    pageId: string,
    otherPageId: string,
    decision: 'merged' | 'dismissed',
  ) {
    setBusy(true);
    try {
      await api('/admin/dedup/decisions', {
        ...post({ page_id: pageId, other_page_id: otherPageId, decision }),
        headers: auth(token),
      });
      onNotice(decision === 'merged' ? '已合并为同一页。' : '已标记为不重复。');
      await refresh();
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  async function judge(pageA: string, pageB: string) {
    const key = pageA + '|' + pageB;
    setBusy(true);
    try {
      const created = await api<{ run_id: string }>('/admin/dedup/judgements', {
        ...post({ page_a: pageA, page_b: pageB, idempotency_key: crypto.randomUUID() }),
        headers: auth(token),
      });
      for (;;) {
        const r = await api<{
          status: string;
          result?: { decision: string; reason: string };
          error?: string;
        }>('/admin/dedup/judgements/' + created.run_id, { headers: auth(token) });
        if (['completed', 'failed', 'cancelled'].includes(r.status)) {
          setJudging((old) => ({
            ...old,
            [key]: r.result
              ? `${r.result.decision}：${r.result.reason}`
              : r.error || r.status,
          }));
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="panel">
        <div className="row space">
          <h2>重复页检查</h2>
          <Button variant="outline" onClick={refresh} disabled={!authed}>
            重新扫描
          </Button>
        </div>
        {stats && (
          <div className="metrics">
            <article>
              <span>总页数</span>
              <strong>{stats.pages_total}</strong>
            </article>
            <article>
              <span>同内容共享页</span>
              <strong>{stats.shared_hash_pages}</strong>
            </article>
            <article>
              <span>已合并页</span>
              <strong>{stats.pages_superseded}</strong>
            </article>
            <article>
              <span>去重记录</span>
              <strong>{stats.reports}</strong>
            </article>
          </div>
        )}
        <p className="muted">
          同内容页在导入时已自动指向同一页记录（{stats?.merge_enabled ? '已启用' : '未启用'}）。
          跨版本同名或近似页用文本比对与模型判定提示，由你确认后合并；合并不删除任何文本。
        </p>
      </section>

      {groups.length === 0 ? (
        <section className="panel">
          <p className="empty">未发现重复页候选。</p>
        </section>
      ) : (
        groups.map((g) => (
          <section className="panel" key={g.key + g.method}>
            <div className="row space">
              <h3>
                {g.kind} · {g.method} · 相似度 {g.score}
              </h3>
            </div>
            <div className="row">
              {g.pages.map((p) => (
                <div key={p.id} className="panel" style={{ maxWidth: 220 }}>
                  <img
                    src={'/api/v1/pages/' + p.id + '/image'}
                    alt={`${p.title}${p.volume}第${p.scan_index}页`}
                    style={{ width: '100%', borderRadius: 4 }}
                    loading="lazy"
                  />
                  <p className="muted" style={{ fontSize: 13 }}>
                    {p.title} · {p.volume} · 第 {p.scan_index} 页
                    <br />
                    {p.edition_id}
                    {p.superseded_by ? ' · 已合并' : ''}
                  </p>
                  <a className="link" href={`/library/${p.id}`}>
                    查看
                  </a>
                </div>
              ))}
            </div>
            <div className="row controls">
              {g.pages.length === 2 && (
                <>
                  <Button
                    variant="outline"
                    onClick={() => decide(g.pages[0].id, g.pages[1].id, 'merged')}
                    disabled={busy || !authed || !!g.pages[0].superseded_by}
                  >
                    合并：第{g.pages[0].scan_index}页保留，第{g.pages[1].scan_index}
                    页标记重复
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => decide(g.pages[0].id, g.pages[1].id, 'dismissed')}
                    disabled={busy || !authed}
                  >
                    标记为不重复
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => judge(g.pages[0].id, g.pages[1].id)}
                    disabled={busy || !authed}
                  >
                    模型判定
                  </Button>
                </>
              )}
            </div>
            {g.pages.length === 2 && judging[g.pages[0].id + '|' + g.pages[1].id] && (
              <p className="notice">
                模型判定：{judging[g.pages[0].id + '|' + g.pages[1].id]}
              </p>
            )}
          </section>
        ))
      )}
    </>
  );
}
