import React, { useMemo, useRef, useState } from 'react';
import { Upload, FolderSync, Search, Trash2, Edit3, Send, Smartphone } from 'lucide-react';
import { api } from '../api.js';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

export default function PoolView({ pool, onRefresh, onCompose, notify }) {
  const { t } = useI18n();
  const fileRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadTags, setUploadTags] = useState('');
  const [folderPath, setFolderPath] = useState('');
  const [syncing, setSyncing] = useState(false);

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [formatFilter, setFormatFilter] = useState('');
  const [unusedOnly, setUnusedOnly] = useState(false);

  const [editing, setEditing] = useState(null);
  const [editTags, setEditTags] = useState('');
  const [editNotes, setEditNotes] = useState('');
  const [editStatus, setEditStatus] = useState('available');

  const filtered = useMemo(() => {
    return pool.filter((img) => {
      if (statusFilter && img.status !== statusFilter) return false;
      if (formatFilter && !img.suitable_for.includes(formatFilter)) return false;
      if (unusedOnly && (img.times_used_post > 0 || img.times_used_story > 0)) return false;
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        const hay = `${img.original_filename} ${img.notes} ${img.tags.join(' ')} ${img.id}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [pool, statusFilter, formatFilter, unusedOnly, search]);

  const handleFiles = async (files) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const res = await api.uploadPool(files, uploadTags);
      const dups = res.duplicates.length
        ? t('pool.toastUploadedDups', { count: res.duplicates.length })
        : '';
      notify(t('pool.toastUploaded', { added: res.added.length, dups }));
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleSyncFolder = async (e) => {
    e.preventDefault();
    if (!folderPath.trim()) return;
    setSyncing(true);
    try {
      const tags = uploadTags.split(',').map((item) => item.trim()).filter(Boolean);
      const res = await api.syncFolder(folderPath.trim(), tags, true);
      notify(t('pool.toastSynced', { added: res.added.length, skipped: res.skipped_duplicates }));
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setSyncing(false);
    }
  };

  const openEdit = (img) => {
    setEditing(img);
    setEditTags(img.tags.join(', '));
    setEditNotes(img.notes || '');
    setEditStatus(img.status);
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      await api.updatePoolImage(editing.id, {
        tags: editTags.split(',').map((item) => item.trim()).filter(Boolean),
        notes: editNotes,
        status: editStatus,
      });
      setEditing(null);
      notify(t('pool.toastUpdated'));
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const removeImage = async (img) => {
    if (!window.confirm(t('pool.confirmDelete', { filename: img.original_filename }))) return;
    try {
      await api.deletePoolImage(img.id);
      notify(t('pool.toastDeleted'));
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const statusBadge = (s) => {
    if (s === 'available') return 'badge-good';
    if (s === 'queued') return 'badge-warn';
    if (s === 'published') return 'badge-ink';
    return '';
  };

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('pool.breadcrumbRoot')} <span className="slash">//</span> {t('pool.breadcrumbPage')}
          </div>
          <h2>{t('pool.title')}</h2>
          <p>{t('pool.subtitle')}</p>
        </div>
        <span className="badge">
          {t('pool.assetsBadge', { shown: filtered.length, total: pool.length })}
        </span>
      </div>

      <SchematicCard label={t('pool.ingestLabel')} bodyClassName="stack-sm">
        <div
          className={`dropzone ${dragging ? 'dragging' : ''}`}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleFiles(e.dataTransfer.files);
          }}
        >
          <input
            ref={fileRef}
            type="file"
            multiple
            accept="image/*"
            style={{ display: 'none' }}
            onChange={(e) => handleFiles(e.target.files)}
          />
          <Upload size={22} strokeWidth={1.5} style={{ marginBottom: 6, color: 'var(--accent)' }} />
          <div style={{ fontWeight: 600, fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            {uploading ? t('pool.uploading') : t('pool.dropPrompt')}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{t('pool.dropSub')}</div>
        </div>

        <hr className="dashed-divider" />

        <form onSubmit={handleSyncFolder} className="row">
          <input
            className="input"
            style={{ flex: 2, minWidth: 240 }}
            placeholder={t('pool.folderPlaceholder')}
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
          />
          <input
            className="input"
            style={{ flex: 1, minWidth: 160 }}
            placeholder={t('pool.tagsPlaceholder')}
            value={uploadTags}
            onChange={(e) => setUploadTags(e.target.value)}
          />
          <button className="btn btn-primary" type="submit" disabled={syncing || !folderPath.trim()}>
            <FolderSync size={14} strokeWidth={1.5} /> {syncing ? t('pool.syncing') : t('pool.syncFolder')}
          </button>
        </form>
      </SchematicCard>

      <div className="row">
        <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
          <Search
            size={14}
            strokeWidth={1.5}
            style={{ position: 'absolute', left: 11, top: 11, color: 'var(--muted)' }}
          />
          <input
            className="input"
            style={{ paddingLeft: 32 }}
            placeholder={t('pool.searchPlaceholder')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="select"
          style={{ width: 175 }}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">{t('pool.allStatuses')}</option>
          <option value="available">{t('pool.status.available')}</option>
          <option value="queued">{t('pool.status.queued')}</option>
          <option value="published">{t('pool.status.published')}</option>
          <option value="archived">{t('pool.status.archived')}</option>
        </select>
        <select
          className="select"
          style={{ width: 170 }}
          value={formatFilter}
          onChange={(e) => setFormatFilter(e.target.value)}
        >
          <option value="">{t('pool.allFormats')}</option>
          <option value="post">{t('pool.format.post')}</option>
          <option value="story">{t('pool.format.story')}</option>
        </select>
        <label className="checkbox-label">
          <input type="checkbox" checked={unusedOnly} onChange={(e) => setUnusedOnly(e.target.checked)} />
          {t('pool.unusedOnly')}
        </label>
      </div>

      {filtered.length === 0 ? (
        <SchematicCard label={t('pool.resultsLabel')}>
          <div className="empty-state">
            <p className="empty-state-title">{t('pool.emptyTitle')}</p>
            <p className="empty-state-sub">{t('pool.emptySub')}</p>
          </div>
        </SchematicCard>
      ) : (
        <div className="pool-grid" style={{ marginTop: 0 }}>
          {filtered.map((img) => (
            <div key={img.id} className="pool-card">
              <div className="pool-thumb-wrap">
                <img src={img.thumbnail_url} alt={img.original_filename} loading="lazy" />
                <div className="pool-thumb-top">
                  <span className={`badge ${statusBadge(img.status)}`}>{img.status}</span>
                  <span className="badge">{img.width}×{img.height}</span>
                </div>
              </div>
              <div className="pool-body">
                <div className="pool-filename" title={img.original_filename}>
                  {img.original_filename}
                </div>
                <div className="pool-meta">
                  <span>#{img.id}</span>
                  <span>
                    {img.times_used_post}P // {img.times_used_story}S
                  </span>
                </div>
                {img.notes && (
                  <div style={{ fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.4 }}>
                    {img.notes.slice(0, 84)}
                    {img.notes.length > 84 ? '…' : ''}
                  </div>
                )}
                {img.tags.length > 0 && (
                  <div className="tag-list">
                    {img.tags.map((tag) => (
                      <span key={tag} className="tag">
                        #{tag}
                      </span>
                    ))}
                  </div>
                )}
                <div className="row" style={{ marginTop: 'auto', paddingTop: 8, gap: 6, borderTop: '1px dashed var(--dashed)' }}>
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ flex: 1 }}
                    onClick={() => onCompose('post', [img.id])}
                  >
                    <Send size={12} strokeWidth={1.5} /> Post
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ flex: 1 }}
                    onClick={() => onCompose('story', [img.id])}
                  >
                    <Smartphone size={12} strokeWidth={1.5} /> Story
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => openEdit(img)}
                    title={t('pool.editMetadata')}
                  >
                    <Edit3 size={12} strokeWidth={1.5} />
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => removeImage(img)}
                    title={t('common.delete')}
                  >
                    <Trash2 size={12} strokeWidth={1.5} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <div className="modal-backdrop" onClick={() => setEditing(null)}>
          <div className="modal-shell" onClick={(e) => e.stopPropagation()}>
            <SchematicCard
              label={t('pool.modalLabel', { id: editing.id })}
              highlight
              bodyClassName="stack"
            >
              <h3 className="section-heading">{t('pool.modalTitle')}</h3>
              <div className="field">
                <label>{t('pool.modalTags')}</label>
                <input className="input" value={editTags} onChange={(e) => setEditTags(e.target.value)} />
              </div>
              <div className="field">
                <label>{t('pool.modalNotes')}</label>
                <textarea
                  className="textarea"
                  placeholder={t('pool.modalNotesPlaceholder')}
                  value={editNotes}
                  onChange={(e) => setEditNotes(e.target.value)}
                />
              </div>
              <div className="field">
                <label>{t('pool.modalStatus')}</label>
                <select className="select" value={editStatus} onChange={(e) => setEditStatus(e.target.value)}>
                  <option value="available">available</option>
                  <option value="queued">queued</option>
                  <option value="published">published</option>
                  <option value="archived">archived</option>
                </select>
              </div>
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button type="button" className="btn" onClick={() => setEditing(null)}>
                  {t('common.cancel')}
                </button>
                <button type="button" className="btn btn-primary" onClick={saveEdit}>
                  {t('common.saveChanges')}
                </button>
              </div>
            </SchematicCard>
          </div>
        </div>
      )}
    </div>
  );
}
