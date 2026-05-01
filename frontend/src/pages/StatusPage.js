import React, { useEffect, useRef, useState } from 'react';
import api from '../services/api';

const REFRESH_MS = 5000;

const formatUptime = (sec) => {
  if (sec == null) return '—';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m ${s}s`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
};

const fmtBytes = (n, unit = 'mb') => {
  if (n == null) return '—';
  return `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit.toUpperCase()}`;
};

const Bar = ({ percent }) => {
  const pct = Math.max(0, Math.min(100, percent || 0));
  const tone = pct > 90 ? 'var(--neon-red)'
    : pct > 75 ? 'var(--neon-gold)'
    : 'var(--neon-cyan)';
  return (
    <div className="status-bar" aria-valuenow={pct} aria-valuemax="100" role="progressbar">
      <div className="status-bar-fill" style={{ width: `${pct}%`, background: tone }} />
      <span className="status-bar-label">{pct.toFixed(0)}%</span>
    </div>
  );
};

const StatusPage = () => {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState('');
  const [error, setError] = useState(null);
  const [paused, setPaused] = useState(false);
  const tickRef = useRef(0);

  const load = async () => {
    try {
      const [s, l] = await Promise.all([
        api.getStatus(),
        api.getStatusLogs(100, logFilter || undefined),
      ]);
      setData(s.data);
      setLogs(l.data);
      setError(null);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load status');
    }
  };

  useEffect(() => {
    load();
    if (paused) return undefined;
    const id = setInterval(() => { tickRef.current += 1; load(); }, REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, logFilter]);

  if (error) return <div className="error">Error: {error}</div>;
  if (!data) return <div className="loading">Loading status…</div>;

  const sys = data.system || {};
  const db = data.database || {};
  const lastUpdate = data.last_price_update_at;
  const lastUpdateAge = lastUpdate
    ? (Date.now() - new Date(lastUpdate).getTime()) / 1000
    : null;
  const updateOk = !data.last_price_update_error;

  return (
    <section>
      <h2>Server Status</h2>

      <div className="status-strip">
        <span className={`status-dot ${updateOk ? 'ok' : 'bad'}`} />
        <strong>{updateOk ? 'Healthy' : 'Degraded'}</strong>
        <span className="status-strip-divider">·</span>
        <span>Uptime: <code>{formatUptime(data.uptime_seconds)}</code></span>
        <span className="status-strip-divider">·</span>
        <span>Host: <code>{data.hostname}</code></span>
        <span className="status-strip-divider">·</span>
        <button className="ghost" onClick={() => setPaused((p) => !p)}>
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>

      <div className="status-grid">
        <div className="status-card">
          <h4>Collection</h4>
          <dl>
            <dt>Cards</dt><dd>{db.cards}</dd>
            <dt>Sealed</dt><dd>{db.sealed_products}</dd>
            <dt>History rows</dt><dd>{db.price_history_rows}</dd>
            <dt>Total value</dt><dd>${(db.total_value || 0).toFixed(2)}</dd>
          </dl>
        </div>

        <div className="status-card">
          <h4>Scheduler</h4>
          <dl>
            <dt>Last refresh</dt>
            <dd>{lastUpdate ? new Date(lastUpdate).toLocaleString() : 'never'}</dd>
            <dt>Age</dt>
            <dd>{lastUpdate ? formatUptime(lastUpdateAge) : '—'}</dd>
            <dt>Last status</dt>
            <dd className={updateOk ? 'ok-text' : 'bad-text'}>
              {updateOk ? 'success' : data.last_price_update_error}
            </dd>
          </dl>
        </div>

        <div className="status-card">
          <h4>CPU</h4>
          {sys.psutil_available ? (
            <>
              <Bar percent={sys.cpu_percent} />
              <dl>
                <dt>Cores</dt><dd>{sys.cpu_count}</dd>
                <dt>Load 1m</dt><dd>{sys.load_avg?.['1m']?.toFixed(2) ?? '—'}</dd>
                <dt>Load 5m</dt><dd>{sys.load_avg?.['5m']?.toFixed(2) ?? '—'}</dd>
                <dt>Load 15m</dt><dd>{sys.load_avg?.['15m']?.toFixed(2) ?? '—'}</dd>
              </dl>
            </>
          ) : (
            <p className="muted">psutil not installed.</p>
          )}
        </div>

        <div className="status-card">
          <h4>Memory</h4>
          {sys.memory ? (
            <>
              <Bar percent={sys.memory.percent} />
              <dl>
                <dt>Used</dt><dd>{fmtBytes(sys.memory.used_mb)}</dd>
                <dt>Total</dt><dd>{fmtBytes(sys.memory.total_mb)}</dd>
              </dl>
            </>
          ) : <p className="muted">unavailable</p>}
        </div>

        <div className="status-card">
          <h4>Disk</h4>
          {sys.disk ? (
            <>
              <Bar percent={sys.disk.percent} />
              <dl>
                <dt>Used</dt><dd>{fmtBytes(sys.disk.used_gb, 'gb')}</dd>
                <dt>Total</dt><dd>{fmtBytes(sys.disk.total_gb, 'gb')}</dd>
              </dl>
            </>
          ) : <p className="muted">unavailable</p>}
        </div>

        <div className="status-card">
          <h4>Process</h4>
          <dl>
            <dt>Started</dt><dd>{new Date(data.started_at).toLocaleString()}</dd>
            <dt>Python</dt><dd>{data.python}</dd>
            <dt>Platform</dt><dd>{data.platform}</dd>
          </dl>
        </div>
      </div>

      <div className="status-card status-logs">
        <div className="logs-header">
          <h4>Recent log records</h4>
          <select value={logFilter} onChange={(e) => setLogFilter(e.target.value)}>
            <option value="">All levels</option>
            <option value="DEBUG">Debug</option>
            <option value="INFO">Info</option>
            <option value="WARNING">Warning</option>
            <option value="ERROR">Error</option>
          </select>
        </div>
        <div className="logs-tail">
          {logs.length === 0 && <div className="muted">No log records yet.</div>}
          {logs.map((row, i) => (
            <div key={`${row.ts}-${i}`} className={`log-row log-${row.level.toLowerCase()}`}>
              <span className="log-ts">{row.ts.slice(11, 19)}</span>
              <span className="log-level">{row.level}</span>
              <span className="log-name">{row.name}</span>
              <span className="log-msg">{row.msg}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default StatusPage;
