import React, { useRef, useState } from 'react';
import api from '../services/api';

const downloadBlob = (text, filename) => {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 0);
};

const SettingsPage = () => {
  const [exportPwd, setExportPwd] = useState('');
  const [exportConfirm, setExportConfirm] = useState('');
  const [exportBusy, setExportBusy] = useState(false);
  const [exportMsg, setExportMsg] = useState(null);

  const [importPwd, setImportPwd] = useState('');
  const [importBusy, setImportBusy] = useState(false);
  const [importMsg, setImportMsg] = useState(null);
  const [importErr, setImportErr] = useState(null);
  const [replaceMode, setReplaceMode] = useState(true);
  const fileRef = useRef(null);

  const onExport = async (e) => {
    e.preventDefault();
    setExportMsg(null);
    if (!exportPwd) return setExportMsg({ kind: 'error', text: 'Enter a password.' });
    if (exportPwd !== exportConfirm) return setExportMsg({ kind: 'error', text: 'Passwords do not match.' });
    setExportBusy(true);
    try {
      const res = await api.exportProfile(exportPwd);
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      downloadBlob(res.data, `card-collection-backup-${ts}.txt`);
      setExportMsg({ kind: 'ok', text: 'Backup downloaded. Keep the password — without it the file cannot be restored.' });
      setExportPwd('');
      setExportConfirm('');
    } catch (err) {
      console.error(err);
      setExportMsg({ kind: 'error', text: err?.response?.data?.detail || 'Export failed.' });
    } finally {
      setExportBusy(false);
    }
  };

  const onImport = async (e) => {
    e.preventDefault();
    setImportMsg(null);
    setImportErr(null);
    const file = fileRef.current?.files?.[0];
    if (!file) return setImportErr('Pick a backup file first.');
    if (!importPwd) return setImportErr('Enter the password used when exporting.');
    setImportBusy(true);
    try {
      const text = await file.text();
      const res = await api.importProfile(text, importPwd, replaceMode);
      const c = res.data?.restored || {};
      setImportMsg(
        `Restored ${c.cards || 0} card(s), ${c.sealed_products || 0} sealed product(s), ` +
        `${c.price_history || 0} price-history row(s).`
      );
      setImportPwd('');
      if (fileRef.current) fileRef.current.value = '';
    } catch (err) {
      console.error(err);
      setImportErr(err?.response?.data?.detail || 'Import failed.');
    } finally {
      setImportBusy(false);
    }
  };

  return (
    <section className="settings-page">
      <h2>Settings — Backup &amp; Restore</h2>

      <div className="settings-card">
        <h3>Export collection</h3>
        <p className="muted">
          Downloads a password-encrypted text file containing every card, sealed product,
          and price history row. The file is plain text so you can read it, but the
          collection data inside is encrypted with your password.
        </p>
        <form onSubmit={onExport}>
          <div>
            <label>Password</label>
            <input
              type="password"
              value={exportPwd}
              onChange={(e) => setExportPwd(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <div>
            <label>Confirm password</label>
            <input
              type="password"
              value={exportConfirm}
              onChange={(e) => setExportConfirm(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <button type="submit" disabled={exportBusy}>
            {exportBusy ? 'Encrypting…' : 'Download Backup'}
          </button>
          {exportMsg && (
            <div className={exportMsg.kind === 'error' ? 'error' : 'success-msg'}>
              {exportMsg.text}
            </div>
          )}
        </form>
      </div>

      <div className="settings-card">
        <h3>Import collection</h3>
        <p className="muted">
          Restore a previously-exported backup. Use this on a new machine or after
          a future update — the file format is forward-compatible.
        </p>
        <form onSubmit={onImport}>
          <div>
            <label>Backup file</label>
            <input type="file" ref={fileRef} accept=".txt,text/plain" />
          </div>
          <div>
            <label>Password</label>
            <input
              type="password"
              value={importPwd}
              onChange={(e) => setImportPwd(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="checkbox-row">
            <label>
              <input
                type="checkbox"
                checked={replaceMode}
                onChange={(e) => setReplaceMode(e.target.checked)}
              />
              Replace existing collection (uncheck to merge on top)
            </label>
          </div>
          <button type="submit" disabled={importBusy}>
            {importBusy ? 'Decrypting…' : 'Restore Backup'}
          </button>
          {importErr && <div className="error">{importErr}</div>}
          {importMsg && <div className="success-msg">{importMsg}</div>}
        </form>
      </div>
    </section>
  );
};

export default SettingsPage;
