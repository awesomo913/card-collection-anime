import React, { useCallback, useEffect, useRef, useState } from 'react';
import api from '../services/api';

/**
 * IdentifyDropZone — reusable drag-and-drop image identifier.
 *
 * The frontend NEVER calls DeepSeek directly. It calls our backend's
 * /identify/* endpoints, which proxy to DeepSeek using the server-side
 * DEEPSEEK_API_KEY env var. This means:
 *   - The key never appears in built JS or browser network traffic.
 *   - The drop zone works the same on the dev box and the Pi.
 *   - When the key isn't set, the backend returns 503 with a clear hint
 *     that this component surfaces verbatim.
 *
 * Props:
 *   mode          'single' | 'batch'
 *                   single: pass one file to /identify/image; auto-pick the
 *                           top candidate's URL (calls onAutoPick).
 *                   batch:  pass N files to /identify/batch; render a review
 *                           grid for the parent to consume via onResults.
 *   gameHint      Optional 'magic'/'pokemon'/'yugioh' — passed to backend so
 *                 the model biases its guesses. Used by AddCardPage where
 *                 the user already picked a game in the dropdown.
 *   onAutoPick    Fired in 'single' mode when identification succeeds and
 *                 the top candidate has a usable URL. Receives the URL string.
 *                 Existing CatalogSearch already listens for the corresponding
 *                 `catalog-search-prefill` event — the parent typically just
 *                 dispatches that event and lets the existing flow take over.
 *   onResults     Fired in 'batch' mode with the IdentifyBatchResponse from
 *                 the backend; parent renders its own review UI.
 *   accept        File input `accept` string. Defaults to 'image/*'.
 *
 * Per-file state machine:
 *   queued → running → done (success) | error (per-image failure)
 */

const ALLOWED_IMAGE_MIME_PREFIX = 'image/';

const IdentifyDropZone = ({
  mode = 'single',
  gameHint,
  onAutoPick,
  onResults,
  accept = 'image/*',
}) => {
  const [items, setItems] = useState([]);  // [{file, status, thumbnail, error, result}]
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef(null);
  // Pin callbacks through refs so the dispatch effect doesn't re-fire on every
  // parent re-render. Same trick used in CatalogSearch.js for the onPick prop.
  const onAutoPickRef = useRef(onAutoPick);
  const onResultsRef = useRef(onResults);
  useEffect(() => { onAutoPickRef.current = onAutoPick; }, [onAutoPick]);
  useEffect(() => { onResultsRef.current = onResults; }, [onResults]);

  const validFiles = useCallback((fileList) => {
    return Array.from(fileList).filter((f) => {
      // Soft filter: keep only image MIME types. The backend will 415 anything
      // else but we may as well skip the round trip when the browser knows.
      return (f.type || '').startsWith(ALLOWED_IMAGE_MIME_PREFIX);
    });
  }, []);

  const makeItems = useCallback((files) => {
    return files.map((file) => ({
      file,
      status: 'queued',
      thumbnail: URL.createObjectURL(file),
      error: null,
      result: null,
    }));
  }, []);

  // Free blob URLs on unmount — leaks add up after a 30-image session.
  useEffect(() => {
    return () => {
      items.forEach((it) => {
        if (it.thumbnail) URL.revokeObjectURL(it.thumbnail);
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runSingle = useCallback(async (file) => {
    setBusy(true);
    setItems([{ file, status: 'running', thumbnail: URL.createObjectURL(file), error: null, result: null }]);
    try {
      const res = await api.identifyImage(file, gameHint);
      const result = res.data;
      setItems((cur) => cur.map((it) =>
        it.file === file ? { ...it, status: result.error ? 'error' : 'done', error: result.error, result } : it
      ));
      // Auto-pick: prefer first candidate with a TCGplayer URL.
      const top = (result.candidates || [])[0];
      const url = top?.suggested_urls?.[0];
      if (url && onAutoPickRef.current) onAutoPickRef.current(url);
    } catch (err) {
      const msg = err?.response?.data?.detail || err.message || 'Identify failed';
      setItems((cur) => cur.map((it) =>
        it.file === file ? { ...it, status: 'error', error: msg } : it
      ));
    } finally {
      setBusy(false);
    }
  }, [gameHint]);

  const runBatch = useCallback(async (files) => {
    setBusy(true);
    const initialItems = makeItems(files).map((it) => ({ ...it, status: 'running' }));
    setItems(initialItems);
    try {
      const res = await api.identifyBatch(files);
      const batch = res.data;
      // Merge per-result outcomes back into UI items by index — the backend
      // returns results in upload order.
      setItems((cur) => cur.map((it, idx) => {
        const r = batch.results[idx];
        if (!r) return { ...it, status: 'error', error: 'No result returned' };
        return { ...it, status: r.error ? 'error' : 'done', error: r.error, result: r };
      }));
      if (onResultsRef.current) onResultsRef.current(batch);
    } catch (err) {
      const msg = err?.response?.data?.detail || err.message || 'Batch identify failed';
      setItems((cur) => cur.map((it) => ({ ...it, status: 'error', error: msg })));
    } finally {
      setBusy(false);
    }
  }, [makeItems]);

  const handleFiles = useCallback((fileList) => {
    const files = validFiles(fileList);
    if (!files.length) return;
    if (mode === 'single') {
      runSingle(files[0]);
    } else {
      runBatch(files);
    }
  }, [mode, runSingle, runBatch, validFiles]);

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (busy) return;
    handleFiles(e.dataTransfer.files);
  };

  const onPickFiles = (e) => {
    handleFiles(e.target.files);
    // Reset so the same file can be re-picked.
    e.target.value = '';
  };

  return (
    <div className="identify-drop-zone-host">
      <div
        className={`identify-drop-zone${dragOver ? ' drag-over' : ''}${busy ? ' busy' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => !busy && fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && !busy && fileInputRef.current?.click()}
        aria-label={mode === 'single' ? 'Drop a card image to identify' : 'Drop card images to identify in batch'}
      >
        <div className="dz-icon">📷</div>
        <div className="dz-text">
          {busy ? 'Identifying…' : (
            mode === 'single'
              ? <>Drop a card photo here, or <span className="dz-link">click to choose a file</span></>
              : <>Drop card photos here, or <span className="dz-link">click to choose files</span> (batch)</>
          )}
        </div>
        <div className="dz-hint">
          Server uses DeepSeek vision. Your DEEPSEEK_API_KEY is read from the Pi env — never sent to the browser.
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept={accept}
          multiple={mode === 'batch'}
          onChange={onPickFiles}
          style={{ display: 'none' }}
        />
      </div>

      {items.length > 0 && (
        <ul className="identify-thumb-grid">
          {items.map((it, idx) => {
            const top = it.result?.candidates?.[0];
            return (
              <li key={idx} className={`identify-thumb status-${it.status}`}>
                <img src={it.thumbnail} alt={it.file.name} />
                <div className="thumb-meta">
                  <div className="thumb-status">{it.status}</div>
                  {top && (
                    <>
                      <div className="thumb-name">{top.name}</div>
                      <div className="thumb-sub">
                        {top.game} · {(top.confidence * 100).toFixed(0)}% confidence
                      </div>
                    </>
                  )}
                  {it.error && <div className="thumb-error">{it.error}</div>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default IdentifyDropZone;
