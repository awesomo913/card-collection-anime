import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import IdentifyDropZone from '../components/IdentifyDropZone';

/**
 * IdentifyPage — dedicated batch identification + review.
 *
 * Flow:
 *   1. User drops N photos (or picks via dialog).
 *   2. Backend runs each through DeepSeek in parallel (Phase 1 fan-out).
 *   3. For each result, a review card shows: thumbnail + top candidate's
 *      name + confidence + game tag + alternative candidates + suggested
 *      URLs / search queries.
 *   4. Clicking "Use URL" pre-fills the existing Add Card page's search box
 *      via the existing `catalog-search-prefill` window event. The resolver
 *      + save pipeline takes over from there. No duplicate logic in this
 *      page — single source of truth for "URL → saved card".
 */

const fmtPct = (n) => `${(n * 100).toFixed(0)}%`;

const IdentifyPage = () => {
  const navigate = useNavigate();
  const [batch, setBatch] = useState(null);
  const [dismissed, setDismissed] = useState(new Set());

  const handleUseUrl = (url) => {
    // Dispatch the same event AddCardPage's "📋 Paste URL" button dispatches.
    // CatalogSearch listens for it (CatalogSearch.js useEffect at top).
    navigate('/cards/add');
    // Defer event dispatch a tick so CatalogSearch has mounted on the new page
    // before we fire (otherwise the event evaporates with no listener).
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('catalog-search-prefill', { detail: url }));
    }, 150);
  };

  const handleUseQuery = (query) => {
    // For now, route the user to Add Card with the search box prefilled. The
    // typed-search path in CatalogSearch will autocomplete from there.
    navigate('/cards/add');
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('catalog-search-prefill', { detail: query }));
    }, 150);
  };

  return (
    <section className="identify-page">
      <h2>Identify Cards from Photos</h2>
      <p className="muted">
        Drop a stack of photos. The Pi sends each through DeepSeek (server-side,
        with the key never reaching this browser) and returns ranked guesses.
        Click <strong>Use URL</strong> on any guess to send it through the
        normal add-card flow.
      </p>

      <IdentifyDropZone mode="batch" onResults={setBatch} />

      {batch && (
        <div className="identify-summary muted">
          {batch.results.length} images · {batch.duration_seconds.toFixed(1)}s ·{' '}
          {batch.results.filter((r) => !r.error).length} ok ·{' '}
          {batch.results.filter((r) => r.error).length} errors
        </div>
      )}

      {batch && (
        <ul className="identify-results">
          {batch.results.map((r, idx) => {
            if (dismissed.has(idx)) return null;
            const top = r.candidates?.[0];
            const others = (r.candidates || []).slice(1);
            return (
              <li key={`${r.source_filename}-${idx}`} className="identify-result-card"
                  data-game={(top?.game || 'unknown').toLowerCase()}>
                <div className="result-header">
                  <span className="result-filename">{r.source_filename}</span>
                  <button type="button" className="ghost dismiss"
                          onClick={() => setDismissed((s) => new Set(s).add(idx))}>
                    Dismiss
                  </button>
                </div>
                {r.error && (
                  <div className="error">{r.error}</div>
                )}
                {!r.error && !top && (
                  <div className="muted">No candidates returned.</div>
                )}
                {top && (
                  <CandidateBlock c={top} onUseUrl={handleUseUrl} onUseQuery={handleUseQuery} primary />
                )}
                {others.length > 0 && (
                  <details className="more-candidates">
                    <summary>{others.length} alternative{others.length > 1 ? 's' : ''}</summary>
                    {others.map((c, i) => (
                      <CandidateBlock key={i} c={c} onUseUrl={handleUseUrl} onUseQuery={handleUseQuery} />
                    ))}
                  </details>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

const CandidateBlock = ({ c, onUseUrl, onUseQuery, primary = false }) => {
  return (
    <div className={`candidate${primary ? ' primary' : ''}`}>
      <div className="candidate-meta">
        <span className="game-chip" data-game={c.game}>{c.game}</span>
        <span className="candidate-name">{c.name}</span>
        <span className="candidate-conf">{fmtPct(c.confidence)}</span>
      </div>
      {c.set_name && <div className="candidate-set">{c.set_name}</div>}
      {c.printing_notes && <div className="candidate-notes">Notes: {c.printing_notes}</div>}
      {c.justification && <div className="candidate-just muted">"{c.justification}"</div>}
      <div className="candidate-actions">
        {(c.suggested_urls || []).map((url, i) => (
          <button key={`u-${i}`} type="button" className="primary"
                  onClick={() => onUseUrl(url)} title={url}>
            Use URL
          </button>
        ))}
        {(c.search_queries || []).map((q, i) => (
          <button key={`q-${i}`} type="button" className="ghost"
                  onClick={() => onUseQuery(q)} title={q}>
            Search "{q.length > 30 ? q.slice(0, 30) + '…' : q}"
          </button>
        ))}
      </div>
    </div>
  );
};

export default IdentifyPage;
