import React from 'react';
import { Images, CalendarClock, CheckCircle2, Smartphone, RefreshCw, Terminal, Plus } from 'lucide-react';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

export default function DashboardView({ stats, publications, activity, adapters, onNavigate, onRefresh }) {
  const { t, locale } = useI18n();
  const scheduled = publications.filter((p) => p.status === 'scheduled').slice(0, 5);
  const activeAdapter = adapters.find((a) => a.is_default);
  const isDry = activeAdapter?.name === 'dry_run';

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('dashboard.breadcrumbRoot')} <span className="slash">//</span> {t('dashboard.breadcrumbPage')}
          </div>
          <h2>{t('dashboard.title')}</h2>
          <p>{t('dashboard.subtitle')}</p>
        </div>
        <div className="row">
          <button type="button" className="btn" onClick={onRefresh}>
            <RefreshCw size={14} strokeWidth={1.5} /> {t('common.refresh')}
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onNavigate('composer')}>
            <Plus size={14} strokeWidth={1.5} /> {t('dashboard.newPub')}
          </button>
        </div>
      </div>

      <SchematicCard
        label={t('dashboard.telemetryLabel')}
        right={
          <span className="badge badge-good">
            <span className="dot" />
            {stats?.scheduler_enabled ? t('dashboard.systemOk') : t('dashboard.schedulerPaused')}
          </span>
        }
        noPadding
      >
        <div className="metrics-strip">
          <div className="metric-cell">
            <div className="metric-label">
              <span>{t('dashboard.metricPool')}</span>
              <Images size={14} strokeWidth={1.5} />
            </div>
            <div className="metric-value">{stats?.pool_available ?? 0}</div>
            <div className="metric-sub">{t('dashboard.metricPoolSub', { total: stats?.pool_total ?? 0 })}</div>
          </div>

          <div className="metric-cell">
            <div className="metric-label">
              <span>{t('dashboard.metricQueue')}</span>
              <CalendarClock size={14} strokeWidth={1.5} />
            </div>
            <div className="metric-value">{stats?.publications_scheduled ?? 0}</div>
            <div className="metric-sub">
              {t('dashboard.metricQueueSub', { drafts: stats?.publications_draft ?? 0 })}
            </div>
          </div>

          <div className="metric-cell">
            <div className="metric-label">
              <span>{t('dashboard.metricPosts')}</span>
              <CheckCircle2 size={14} strokeWidth={1.5} />
            </div>
            <div className="metric-value">{stats?.posts_published ?? 0}</div>
            <div className="metric-sub">{t('dashboard.metricPostsSub')}</div>
          </div>

          <div className="metric-cell">
            <div className="metric-label">
              <span>{t('dashboard.metricStories')}</span>
              <Smartphone size={14} strokeWidth={1.5} />
            </div>
            <div className="metric-value">{stats?.stories_published ?? 0}</div>
            <div className="metric-sub">
              {t('dashboard.metricStoriesSub', { failed: stats?.publications_failed ?? 0 })}
            </div>
          </div>
        </div>
      </SchematicCard>

      {activeAdapter && (
        <SchematicCard
          label={t('dashboard.adapterLabel')}
          right={
            <span
              className={`badge ${
                isDry ? 'badge-warn' : activeAdapter.configured && activeAdapter.available ? 'badge-good' : 'badge-accent'
              }`}
            >
              <span
                className={`dot ${
                  isDry ? 'warn' : activeAdapter.configured && activeAdapter.available ? '' : 'error'
                }`}
              />
              {activeAdapter.name}
            </span>
          }
        >
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div className="section-heading">{activeAdapter.label}</div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>{activeAdapter.message}</div>
            </div>
            <button type="button" className="btn" onClick={() => onNavigate('settings')}>
              <Terminal size={14} strokeWidth={1.5} /> {t('dashboard.configureAdapters')}
            </button>
          </div>
        </SchematicCard>
      )}

      <div className="two-col">
        <SchematicCard
          label={t('dashboard.upcomingLabel')}
          right={
            <button type="button" className="btn btn-sm" onClick={() => onNavigate('queue')}>
              {t('dashboard.viewAll')}
            </button>
          }
          noPadding
        >
          {scheduled.length === 0 ? (
            <div className="empty-state">
              <p className="empty-state-title">{t('dashboard.noScheduledTitle')}</p>
              <p className="empty-state-sub">{t('dashboard.noScheduledSub')}</p>
            </div>
          ) : (
            <div className="schematic-list">
              {scheduled.map((pub) => (
                <div
                  key={pub.id}
                  className="pub-row"
                  style={{ gridTemplateColumns: '52px 1fr auto', padding: '12px 18px' }}
                >
                  <img
                    src={pub.prepared_urls?.[0] || ''}
                    alt=""
                    className="pub-thumb"
                    style={{ width: 52, height: 52 }}
                  />
                  <div>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="badge badge-accent">{pub.pub_type}</span>
                      <span className="badge">{t('dashboard.byActor', { actor: pub.created_by })}</span>
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 5 }}>
                      {pub.scheduled_at ? new Date(pub.scheduled_at).toLocaleString(locale) : '-'}
                    </div>
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>#{pub.id}</span>
                </div>
              ))}
            </div>
          )}
        </SchematicCard>

        <SchematicCard label={t('dashboard.activityLabel')} noPadding>
          {activity.length === 0 ? (
            <div className="empty-state">
              <p className="empty-state-title">{t('dashboard.noActivityTitle')}</p>
              <p className="empty-state-sub">{t('dashboard.noActivitySub')}</p>
            </div>
          ) : (
            <div className="schematic-list" style={{ maxHeight: 360, overflowY: 'auto' }}>
              {activity.slice(0, 15).map((entry) => (
                <div key={entry.id} className="schematic-row" style={{ padding: '10px 18px', fontSize: 11.5 }}>
                  <div className="row" style={{ gap: 8 }}>
                    <span className="badge">{entry.actor}</span>
                    <span style={{ fontWeight: 500, color: 'var(--ink)' }}>{entry.action}</span>
                    {entry.entity_id && <span style={{ color: 'var(--muted)' }}>#{entry.entity_id}</span>}
                  </div>
                  <span style={{ color: 'var(--muted)', fontSize: 10.5, whiteSpace: 'nowrap' }}>
                    {new Date(entry.created_at).toLocaleTimeString(locale)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SchematicCard>
      </div>
    </div>
  );
}
