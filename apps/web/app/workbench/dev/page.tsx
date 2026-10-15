'use client';
import { useEffect, useRef, useState } from 'react';
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
import { api, post, auth, type Source, type Page, type Result } from '@/features/api';

interface Health {
  status: string;
  text_model: string;
  vision_model: string;
  text_supplier: string;
  vision_supplier: string;
  model_configured: boolean;
  config_error: string;
  admin_configured: boolean;
}

interface RunRecord {
  status: string;
  result?: (Result & { version_id?: string; method?: string }) | null;
  error?: string | null;
}

interface PageStatus {
  scan_index: number;
  state: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  versionId?: string;
  message?: string;
}

const ENDPOINTS: [string, string][] = [
  ['GET /api/v1/health', '进程与模型配置状态（不含密钥）'],
  ['GET /api/v1/info/version', 'API 语义版本'],
  ['GET /api/v1/sources', '材料文件列表'],
  ['GET /api/v1/sources/{id}/pages', '材料内扫描页登记'],
  ['GET /api/v1/pages/{page_id}', '当前公开文本与原页信息'],
  ['GET /api/v1/pages/{page_id}/image', '原页 JPEG（受来源状态约束）'],
  ['POST /api/v1/admin/agent/runs', '管理员创建识别/讲解运行'],
  ['GET /api/v1/agent/runs/{id}', '读取运行状态与结果'],
  ['GET /api/v1/agent/runs/{id}/stream', 'SSE 事件（支持 after_event_id）'],
  ['GET /api/v1/admin/pages/{page_id}', '读取最新草稿（含未发布）'],
  ['GET /api/v1/admin/model-config', '脱敏模型配置与能力'],
  ['GET /api/v1/admin/usage', '当日用量与失败分布'],
];

export default function DevConsole() {
  const [token, setToken] = useState('');
  const [health, setHealth] = useState<Health | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [index, setIndex] = useState(1);
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [from, setFrom] = useState(1);
  const [to, setTo] = useState(3);
  const [skipExisting, setSkipExisting] = useState(true);
  const [queue, setQueue] = useState<PageStatus[]>([]);
  const [running, setRunning] = useState(false);
  const stopRef = useRef(false);
  const selected = sources.find((s) => s.id === sourceId);
  const pageId = sourceId ? `${sourceId}-p${index}` : '';

  useEffect(() => {
    api<Health>('/health')
      .then(setHealth)
      .catch((e) => setError((e as Error).message));
    api<Source[]>('/sources')
      .then((s) => {
        setSources(s);
        setSourceId(s[0]?.id || '');
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  async function loadPage() {
    if (!token || !pageId) return;
    setError('');
    setNotice('');
    try {
      setPage(await api<Page>('/admin/pages/' + pageId, { headers: auth(token) }));
    } catch (e) {
      setPage(null);
      setError((e as Error).message);
    }
  }

  async function waitForRun(runId: string): Promise<RunRecord> {
    for (;;) {
      const r = await api<RunRecord>('/agent/runs/' + runId, {
        headers: auth(token),
      });
      if (['completed', 'failed', 'cancelled'].includes(r.status)) return r;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  async function runOne(scanIndex: number): Promise<PageStatus> {
    const target = `${sourceId}-p${scanIndex}`;
    const created = await api<{ run_id: string }>('/admin/agent/runs', {
      ...post({
        kind: 'recognize',
        page_id: target,
        source_id: sourceId,
        idempotency_key: crypto.randomUUID(),
      }),
      headers: auth(token),
    });
    const done = await waitForRun(created.run_id);
    if (done.status === 'completed') {
      return {
        scan_index: scanIndex,
        state: 'completed',
        versionId: done.result?.version_id,
      };
    }
    return {
      scan_index: scanIndex,
      state: done.status === 'cancelled' ? 'failed' : 'failed',
      message: done.error || done.status,
    };
  }

  async function runRange() {
    if (!token || !sourceId) return;
    const start = Math.max(1, Math.min(from, to));
    const end = Math.min(selected?.page_count || to, Math.max(from, to));
    stopRef.current = false;
    setRunning(true);
    setError('');
    setNotice('');
    setQueue([]);
    try {
      await api('/sessions', post({}));
      for (let n = start; n <= end; n += 1) {
        if (stopRef.current) {
          setNotice(`已停止：在第 ${n} 页前中断，已完成项保留。`);
          break;
        }
        if (skipExisting) {
          try {
            const info = await api<Page>('/admin/pages/' + `${sourceId}-p${n}`, {
              headers: auth(token),
            });
            if (info.version) {
              setQueue((q) => [
                ...q,
                { scan_index: n, state: 'skipped', versionId: info.version?.id },
              ]);
              continue;
            }
          } catch {
            /* fall through and attempt recognition */
          }
        }
        setQueue((q) => [...q, { scan_index: n, state: 'running' }]);
        let outcome: PageStatus;
        try {
          outcome = await runOne(n);
        } catch (e) {
          outcome = {
            scan_index: n,
            state: 'failed',
            message: (e as Error).message,
          };
        }
        setQueue((q) =>
          q.map((item) => (item.scan_index === n ? outcome : item)),
        );
        // Opening the next page refreshes the reader image context.
        setIndex(n);
      }
      if (!stopRef.current) setNotice('批次顺序执行结束。');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const done = queue.filter((q) => q.state === 'completed').length;
  const failed = queue.filter((q) => q.state === 'failed').length;
  const skipped = queue.filter((q) => q.state === 'skipped').length;

  return (
    <Shell>
      <section className="intro">
        <p className="eyebrow">开发者接口控制台</p>
        <h1>接入模型后，从这里开始逐页识别。</h1>
        <p>
          面向本机开发者的最小操作台：选择材料与页范围，顺序运行识别 Agent，查看真实事件与原始结果。
        </p>
      </section>

      <section className="panel">
        <div className="row space">
          <h2>连接状态</h2>
          <a className="link" href="/docs" target="_blank" rel="noreferrer">
            打开 OpenAPI 文档
          </a>
        </div>
        {health ? (
          <>
            <div className="metrics">
              <article>
                <span>模型配置</span>
                <strong>{health.model_configured ? '已配置' : '未配置'}</strong>
              </article>
              <article>
                <span>文本模型</span>
                <strong>{health.text_model || '—'}</strong>
              </article>
              <article>
                <span>视觉模型</span>
                <strong>{health.vision_model || '—'}</strong>
              </article>
              <article>
                <span>管理员令牌</span>
                <strong>{health.admin_configured ? '已配置' : '未配置'}</strong>
              </article>
            </div>
            {health.config_error && (
              <p className="error" role="alert">
                {health.config_error}
              </p>
            )}
            {!health.model_configured && (
              <p className="notice">
                尚未配置模型密钥：识别与讲解入口会明确提示，不会返回模拟结果。请在项目根目录 `.env`
                填写 `GLM_API_KEY`（或 `TEXT_*`）与视觉模型后重启服务。
              </p>
            )}
          </>
        ) : (
          <p className="muted">正在读取 /health…</p>
        )}
      </section>

      <section className="panel">
        <h2>目标页</h2>
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
          <label>
            文献材料
            <Select
              value={sourceId}
              onValueChange={(v) => {
                setSourceId(v || '');
                setIndex(1);
                setPage(null);
              }}
            >
              <SelectTrigger className="field" style={{ height: 46, width: '100%' }}>
                <SelectValue>
                  {selected ? `${selected.title} · ${selected.volume}` : '选择文献'}
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
                if (Number.isInteger(n) && n > 0) setIndex(n);
              }}
            />
          </label>
          <span className="muted">共 {selected?.page_count ?? 0} 页</span>
          <Button variant="outline" onClick={loadPage} disabled={!token || !pageId}>
            载入原页与草稿
          </Button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="notice">{notice}</p>}
        {page && (
          <div className="reader-grid">
            <div>
              <img
                className="scan"
                src={'/api/v1/pages/' + page.id + '/image'}
                alt={`${page.title}${page.volume}扫描第${index}页`}
              />
            </div>
            <div>
              <p className="muted">
                页 ID：<code>{page.id}</code>
              </p>
              <p className="muted">
                {page.version
                  ? `已有版本 ${page.version.id.slice(0, 8)}…（${page.version.method}，${page.version.reviewed ? '人工校勘' : '未校勘'}）`
                  : '尚无保存的文本版本'}
              </p>
            </div>
          </div>
        )}
      </section>

      {page && (
        <RunPanel
          key={page.id}
          kind="recognize"
          pageId={page.id}
          sourceId={sourceId}
          adminToken={token}
          onComplete={loadPage}
        />
      )}

      <section className="panel">
        <h2>顺序识别（开发用简易批次）</h2>
        <p className="muted">
          逐页提交识别并等待上一页结束，避免一次性塞满队列；每页都会真实调用模型并计入当日预算。
        </p>
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
          <label className="check">
            <Checkbox
              checked={skipExisting}
              onCheckedChange={(v) => setSkipExisting(Boolean(v))}
            />
            跳过已有草稿的页
          </label>
          {running ? (
            <Button
              variant="outline"
              onClick={() => {
                stopRef.current = true;
              }}
            >
              停止（完成后不再继续）
            </Button>
          ) : (
            <Button
              className="action"
              onClick={runRange}
              disabled={!token || !sourceId || !health?.model_configured}
            >
              开始顺序识别
            </Button>
          )}
          <a className="link" href="/workbench/usage">
            查看当日用量
          </a>
        </div>
        {queue.length > 0 && (
          <>
            <p className="stat-note">
              完成 {done} · 失败 {failed} · 跳过 {skipped} · 共 {queue.length}
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>扫描页</th>
                  <th>状态</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {queue.map((q) => (
                  <tr key={q.scan_index}>
                    <td>{q.scan_index}</td>
                    <td>{q.state}</td>
                    <td>{q.versionId || q.message || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="panel">
        <h2>接口速查</h2>
        <table className="table">
          <thead>
            <tr>
              <th>接口</th>
              <th>用途</th>
            </tr>
          </thead>
          <tbody>
            {ENDPOINTS.map(([path, purpose]) => (
              <tr key={path}>
                <td>
                  <code>{path}</code>
                </td>
                <td>{purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </Shell>
  );
}
