'use client';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Shell } from '@/features/shell';
import { api, auth } from '@/features/api';

interface UsageSummary {
  day_start_utc: string;
  budget_timezone: string;
  daily_model_call_limit: number;
  session_model_call_limit: number;
  calls_today: number;
  calls_remaining: number;
  call_status: Record<string, number>;
  calls_by_stage: { stage: string; count: number }[];
  failures_by_class: { error_class: string; count: number }[];
  tokens_today: {
    input_tokens: number;
    output_tokens: number;
    estimated_cost: number;
    priced_calls: number;
  };
  pricing_configured: boolean;
  recent_runs: {
    id: string;
    kind: string;
    status: string;
    error: string | null;
    created_at: string;
  }[];
  runs_today_by_status: { status: string; count: number }[];
}

interface ModelConfig {
  text: {
    supplier: string;
    model: string;
    api_key: string;
    configured: boolean;
    capabilities: { tools: boolean; json: boolean; vision: boolean };
  };
  vision: {
    supplier: string;
    model: string;
    api_key: string;
    configured: boolean;
    capabilities: { tools: boolean; json: boolean; vision: boolean };
  };
  limits: Record<string, string | number>;
  pricing_configured: boolean;
  note: string;
}

export default function UsagePage() {
  const [token, setToken] = useState('');
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [error, setError] = useState('');
  const [probe, setProbe] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setError('');
    setBusy(true);
    try {
      setUsage(await api<UsageSummary>('/admin/usage', { headers: auth(token) }));
      setConfig(
        await api<ModelConfig>('/admin/model-config', { headers: auth(token) }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function runProbe(target: 'text' | 'vision') {
    setProbe('');
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; model?: string; error?: string }>(
        '/admin/model-probes',
        {
          method: 'POST',
          body: JSON.stringify({ target }),
          headers: auth(token),
        },
      );
      setProbe(
        r.ok
          ? `实际探测成功：${target} · ${r.model}`
          : `探测未通过：${r.error}`,
      );
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <section className="intro">
        <p className="eyebrow">智能体工作台 / 用量</p>
        <h1>理解额度与失败原因。</h1>
        <p>
          日预算按 {usage?.budget_timezone || 'Asia/Shanghai'} 计算；未配置价格时不显示为 0 元。
        </p>
      </section>
      <section className="panel">
        <label>
          管理员令牌
          <input
            type="password"
            className="field"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
          />
        </label>
        <div className="row controls">
          <Button onClick={refresh} disabled={!token || busy}>
            刷新用量
          </Button>
          <Button
            variant="outline"
            onClick={() => runProbe('text')}
            disabled={!token || busy}
          >
            探测文本服务（计入预算）
          </Button>
          <Button
            variant="outline"
            onClick={() => runProbe('vision')}
            disabled={!token || busy}
          >
            探测视觉服务（计入预算）
          </Button>
          <a className="link" href="/workbench">
            返回工作台
          </a>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {probe && <p className="notice">{probe}</p>}
      </section>
      {config && (
        <section className="panel">
          <h2>模型配置摘要（脱敏）</h2>
          <table className="table">
            <thead>
              <tr>
                <th>用途</th>
                <th>供应商</th>
                <th>模型</th>
                <th>密钥</th>
                <th>能力</th>
              </tr>
            </thead>
            <tbody>
              {(['text', 'vision'] as const).map((k) => (
                <tr key={k}>
                  <td>{k === 'text' ? '文本' : '视觉'}</td>
                  <td>{config[k].supplier}</td>
                  <td>{config[k].model}</td>
                  <td>{config[k].api_key || '未配置'}</td>
                  <td>
                    工具:{config[k].capabilities.tools ? '有' : '无'} · JSON:
                    {config[k].capabilities.json ? '有' : '无'} · 视觉:
                    {config[k].capabilities.vision ? '有' : '无'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">{config.note}</p>
        </section>
      )}
      {usage && (
        <>
          <section className="metrics">
            <article>
              <span>今日模型调用</span>
              <strong>{usage.calls_today}</strong>
            </article>
            <article>
              <span>剩余调用数</span>
              <strong>{usage.calls_remaining}</strong>
            </article>
            <article>
              <span>今日运行</span>
              <strong>
                {usage.runs_today_by_status.reduce((n, r) => n + r.count, 0)}
              </strong>
            </article>
            <article>
              <span>估算费用</span>
              <strong>
                {usage.pricing_configured
                  ? `${usage.tokens_today.estimated_cost}（已配置价格）`
                  : '未配置'}
              </strong>
            </article>
          </section>
          <section className="panel">
            <h2>按阶段统计</h2>
            {usage.calls_by_stage.length ? (
              <ul>
                {usage.calls_by_stage.map((s) => (
                  <li key={s.stage || 'unknown'}>
                    {s.stage || '未标注'}：{s.count}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">今日尚无模型调用。</p>
            )}
            <h2>失败原因分布</h2>
            {usage.failures_by_class.length ? (
              <ul>
                {usage.failures_by_class.map((f) => (
                  <li key={f.error_class || 'unknown'}>
                    {f.error_class || '未分类'}：{f.count}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">今日没有失败的模型调用。</p>
            )}
          </section>
          <section className="panel">
            <h2>最近运行</h2>
            <table className="table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>错误</th>
                </tr>
              </thead>
              <tbody>
                {usage.recent_runs.map((r) => (
                  <tr key={r.id}>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                    <td>{r.kind}</td>
                    <td>{r.status}</td>
                    <td>{r.error || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </Shell>
  );
}
