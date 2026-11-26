import React, { useMemo, useState } from 'react';
import { Send, Trash2, Play, RefreshCw, Edit3, BarChart3, ExternalLink } from 'lucide-react';
import { api } from '../api.js';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

export default function QueueView({ publications, onEdit, onRefresh, notify }) {
  const { t, locale } = useI18n();
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [syncingInsightId, setSyncingInsightId] = useState(null);

  const handleSyncPubInsight = async (pub) => {
    setSyncingInsightId(pub.id);
    try {
      const res = await api.syncPublicationInsights(pub.id);
      if (res.error_message) {
        notify(res.error_message, 'error');
      } else {
        notify(t('queue.toastInsightSynced'));
      }
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setSyncingInsightId(null);
    }
  };

  const filtered = useMemo(() => {
    return publications.filter((p) => {
      if (statusFilter && p.status !== statusFilter) return false;
      if (typeFilter && p.pub_type !== typeFilter) return false;
      return true;
    });
  }, [publications, statusFilter, typeFilter]);

  const handlePublish = async (pub) => {
    setBusyId(pub.id);
    try {
      const res = await api.publishNow(pub.id);
      if (res.status === 'failed') {
        notify(t('queue.toastFailed', { error: res.error_message }), 'error');
      } else {
        notify(t('queue.toastPublished', { adapter: res.adapter, id: res.external_id }));
      }
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (pub) => {
    if (!window.confirm(t('queue.confirmDelete', { id: pub.id }))) return;
    try {
      await api.deletePublication(pub.id);
      notify(t('queue.toastDeleted'));
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const handleRunDue = async () => {
    try {
      const done = await api.runDueScheduled();
      notify(t('queue.toastRunDue', { count: done.length }));
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const statusMeta = (s) => {
    if (s === 'published') return { badge: 'badge-good', dot: '' };
    if (s === 'scheduled') return { badge: 'badge-warn', dot: 'warn' };
    if (s === 'failed') return { badge: 'badge-accent', dot: 'error' };
    return { badge: '', dot: 'muted' };
  };

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('queue.breadcrumbRoot')} <span className="slash">//</span> {t('queue.breadcrumbPage')}
          </div>
          <h2>{t('queue.title')}</h2>
          <p>{t('queue.subtitle')}</p>
        </div>
        <div className="row">
          <button type="button" className="btn" onClick={handleRunDue}>
            <Play size={14} strokeWidth={1.5} /> {t('queue.runDue')}
          </button>
          <button type="button" className="btn" onClick={onRefresh}>
            <RefreshCw size={14} strokeWidth={1.5} /> {t('common.refresh')}
          </button>
        </div>
      </div>

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="filter-tabs">
          {[
            { id: '', label: t('queue.filter.all') },
            { id: 'scheduled', label: t('queue.filter.scheduled') },
            { id: 'draft', label: t('queue.filter.draft') },
            { id: 'published', label: t('queue.filter.published') },
            { id: 'failed', label: t('queue.filter.failed') },
          ].map((f) => (
            <button
              key={f.id}
              type="button"
              className={`filter-tab ${statusFilter === f.id ? 'active' : ''}`}
              onClick={() => setStatusFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>

        <select
          className="select"
          style={{ width: 175 }}
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">{t('queue.allTypes')}</option>
          <option value="post">{t('queue.type.post')}</option>
          <option value="carousel">{t('queue.type.carousel')}</option>
          <option value="story">{t('queue.type.story')}</option>
        </select>
      </div>

      <SchematicCard
        label={t('queue.listLabel')}
        right={<span className="badge">{t('queue.recordsBadge', { count: filtered.length })}</span>}
        noPadding
      >
        {filtered.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-title">{t('queue.emptyTitle')}</p>
            <p className="empty-state-sub">{t('queue.emptySub')}</p>
          </div>
        ) : (
          <div className="schematic-list">
            {filtered.map((pub) => {
              const sm = statusMeta(pub.status);
              return (
                <div key={pub.id} className="pub-row">
                  <img src={pub.prepared_urls?.[0] || ''} alt="" className="pub-thumb" />
                  <div className="stack-sm" style={{ gap: 6 }}>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="badge badge-ink">{pub.pub_type}</span>
                      <span className={`badge ${sm.badge}`}>
                        <span className={`dot ${sm.dot}`} />
                        {pub.status}
                      </span>
                      <span className="badge">{pub.adapter}</span>
                      <span className="badge">{t('dashboard.byActor', { actor: pub.created_by })}</span>
                      <span style={{ fontSize: 11, color: 'var(--muted)' }}>#{pub.id}</span>
                    </div>
                    {pub.full_caption && (
                      <div style={{ fontSize: 12.5, lineHeight: 1.45, color: 'var(--ink)' }}>
                        {pub.full_caption.slice(0, 150)}
                        {pub.full_caption.length > 150 ? '…' : ''}
                      </div>
                    )}
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                      {pub.published_at
                        ? t('queue.metaPublished', { date: new Date(pub.published_at).toLocaleString(locale) })
                        : pub.scheduled_at
                        ? t('queue.metaScheduled', { date: new Date(pub.scheduled_at).toLocaleString(locale) })
                        : t('queue.metaCreated', { date: new Date(pub.created_at).toLocaleString(locale) })}
                      {pub.external_id && ` // EXT: ${pub.external_id}`}
                    </div>
                    {pub.error_message && (
                      <div style={{ fontSize: 11.5, color: 'var(--accent)' }}>
                        {t('queue.errorPrefix', { error: pub.error_message })}
                      </div>
                    )}
                    {pub.insights && (
                      <div className="queue-insight-bar">
                        <span>
                          <strong>{pub.insights.views ?? '—'}</strong> {t('insights.kpi.views')}
                        </span>
                        <span>
                          <strong>{pub.insights.reach ?? '—'}</strong> {t('insights.kpi.reach')}
                        </span>
                        {pub.pub_type === 'story' ? (
                          <>
                            <span>
                              <strong>{pub.insights.replies ?? '—'}</strong> {t('insights.kpi.replies')}
                            </span>
                            <span>
                              <strong>{pub.insights.navigation ?? '—'}</strong> {t('insights.kpi.navigation')}
                            </span>
                          </>
                        ) : (
                          <>
                            <span>
                              <strong>{pub.insights.likes ?? '—'}</strong> {t('insights.kpi.likes')}
                            </span>
                            <span>
                              <strong>{pub.insights.comments ?? '—'}</strong> {t('insights.kpi.comments')}
                            </span>
                            <span>
                              <strong>{pub.insights.saved ?? '—'}</strong> {t('insights.kpi.saved')}
                            </span>
                          </>
                        )}
                        <span>
                          <strong>{pub.insights.shares ?? '—'}</strong> {t('insights.kpi.shares')}
                        </span>
                        {pub.insights.engagement_rate !== null && pub.insights.engagement_rate !== undefined && (
                          <span className="queue-insight-er">ER {pub.insights.engagement_rate}%</span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="row" style={{ gap: 6 }}>
                    {pub.status === 'published' && pub.external_url && pub.external_url.startsWith('http') && (
                      <a
                        href={pub.external_url}
                        target="_blank"
                        rel="noreferrer"
                        className="btn btn-sm"
                        title={t('insights.openInstagram')}
                      >
                        <ExternalLink size={12} strokeWidth={1.5} />
                      </a>
                    )}
                    {pub.status === 'published' && pub.adapter === 'graph_api' && pub.external_id && (
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={syncingInsightId === pub.id}
                        onClick={() => handleSyncPubInsight(pub)}
                        title={t('queue.syncInsightsTitle')}
                      >
                        <BarChart3 size={12} strokeWidth={1.5} />{' '}
                        {syncingInsightId === pub.id ? '...' : t('queue.syncInsights')}
                      </button>
                    )}
                    {pub.status !== 'published' && (
                      <>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => onEdit && onEdit(pub)}
                          title={t('queue.editTitle')}
                        >
                          <Edit3 size={12} strokeWidth={1.5} /> {t('common.edit')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={busyId === pub.id}
                          onClick={() => handlePublish(pub)}
                        >
                          <Send size={12} strokeWidth={1.5} />{' '}
                          {busyId === pub.id
                            ? '...'
                            : pub.status === 'failed'
                            ? t('queue.retry')
                            : t('queue.publish')}
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      onClick={() => handleDelete(pub)}
                      title={t('common.delete')}
                    >
                      <Trash2 size={12} strokeWidth={1.5} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </SchematicCard>
    </div>
  );
}
