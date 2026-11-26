import React, { useCallback, useEffect, useState } from 'react';
import {
  BarChart3,
  RefreshCw,
  Users,
  Eye,
  Heart,
  TrendingUp,
  ExternalLink,
  History,
  ChevronDown,
  ChevronUp,
  Smartphone,
  Images,
  Film,
  Clock,
  AlertTriangle,
} from 'lucide-react';
import { api } from '../api.js';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

function fmtNum(val) {
  if (val === null || val === undefined) return '—';
  return Number(val).toLocaleString();
}

function MediaThumb({ item }) {
  const [broken, setBroken] = useState(false);
  if (!item.thumbnail_url || broken) {
    const Icon =
      item.media_product_type === 'STORY'
        ? Smartphone
        : item.media_product_type === 'REELS'
        ? Film
        : Images;
    return (
      <div className="insight-thumb-fallback" aria-hidden="true">
        <Icon size={20} strokeWidth={1.4} />
      </div>
    );
  }
  return (
    <img
      src={item.thumbnail_url}
      alt=""
      className="pub-thumb"
      onError={() => setBroken(true)}
    />
  );
}

function DailyReachChart({ series, t }) {
  if (!series || series.length === 0) return null;
  const maxReach = Math.max(...series.map((d) => d.reach || 0), 1);
  const totalReach = series.reduce((acc, d) => acc + (d.reach || 0), 0);
  const peak = series.reduce((best, d) => ((d.reach || 0) > (best.reach || 0) ? d : best), series[0]);

  return (
    <SchematicCard
      label={t('insights.chartLabel')}
      right={
        <div className="row" style={{ gap: 8 }}>
          <span className="badge">
            {t('insights.chartTotal', { value: fmtNum(totalReach) })}
          </span>
          {peak && (
            <span className="badge badge-accent">
              {t('insights.chartPeak', { date: peak.date.slice(5), value: fmtNum(peak.reach) })}
            </span>
          )}
        </div>
      }
    >
      <div className="chart-wrap">
        <div className="chart-bars" style={{ gridTemplateColumns: `repeat(${series.length}, minmax(18px, 1fr))` }}>
          {series.map((pt) => {
            const pct = Math.max(4, Math.round(((pt.reach || 0) / maxReach) * 100));
            return (
              <div
                key={pt.date}
                className="chart-col"
                title={`${pt.date} · ${t('insights.kpi.reach')}: ${pt.reach} · ${t('insights.kpi.followersDelta')}: ${pt.follower_count >= 0 ? `+${pt.follower_count}` : pt.follower_count}`}
              >
                <div className="chart-val">{pt.reach > 0 ? pt.reach : ''}</div>
                <div className="chart-track">
                  <div
                    className={`chart-bar ${pt.reach === peak?.reach && pt.reach > 0 ? 'is-peak' : ''}`}
                    style={{ height: `${pt.reach > 0 ? pct : 2}%` }}
                  />
                </div>
                <div className="chart-date">{pt.date.slice(5)}</div>
              </div>
            );
          })}
        </div>
      </div>
    </SchematicCard>
  );
}

export default function InsightsView({ onNavigate, onRefresh, notify }) {
  const { t, locale } = useI18n();
  const [days, setDays] = useState(7);
  const [typeFilter, setTypeFilter] = useState('');
  const [sortBy, setSortBy] = useState('published_at');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncingItemId, setSyncingItemId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [historyMap, setHistoryMap] = useState({});
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadInsights = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getInsights({
        days,
        media_product_type: typeFilter || undefined,
        sort_by: sortBy,
        limit: 100,
      });
      setData(res);
    } catch (err) {
      if (err.status !== 401) {
        notify(t('insights.toastLoadError', { error: err.message }), 'error');
      }
    } finally {
      setLoading(false);
    }
  }, [days, typeFilter, sortBy, notify, t]);

  useEffect(() => {
    loadInsights();
  }, [loadInsights]);

  const handleSyncAll = async () => {
    setSyncing(true);
    try {
      const res = await api.syncInsights({ days });
      const msg = t('insights.toastSynced', {
        media: res.synced_media_count,
        stories: res.synced_stories_count,
      });
      if (res.errors && res.errors.length > 0) {
        notify(`${msg} (${res.errors[0]})`, 'error');
      } else {
        notify(msg);
      }
      await loadInsights();
      if (onRefresh) await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setSyncing(false);
    }
  };

  const handleSyncItem = async (item) => {
    setSyncingItemId(item.external_id);
    try {
      const updated = await api.syncMediaInsight(item.external_id);
      if (updated.error_message) {
        notify(updated.error_message, 'error');
      } else {
        notify(t('insights.toastItemSynced'));
      }
      await loadInsights();
      if (expandedId === item.external_id) {
        const hist = await api.getMediaInsightHistory(item.external_id, 30);
        setHistoryMap((prev) => ({ ...prev, [item.external_id]: hist }));
      }
      if (onRefresh) await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setSyncingItemId(null);
    }
  };

  const toggleHistory = async (externalId) => {
    if (expandedId === externalId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(externalId);
    setHistoryLoading(true);
    try {
      const hist = await api.getMediaInsightHistory(externalId, 30);
      setHistoryMap((prev) => ({ ...prev, [externalId]: hist }));
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setHistoryLoading(false);
    }
  };

  const account = data?.account;
  const summary = data?.summary;
  const items = data?.items || [];

  const navLabel = (key) => {
    const map = {
      tap_forward: t('insights.nav.tapForward'),
      tap_back: t('insights.nav.tapBack'),
      tap_exit: t('insights.nav.tapExit'),
      swipe_forward: t('insights.nav.swipeForward'),
    };
    return map[key] || key;
  };

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('insights.breadcrumbRoot')} <span className="slash">//</span> {t('insights.breadcrumbPage')}
          </div>
          <h2>{t('insights.title')}</h2>
          <p>{t('insights.subtitle')}</p>
        </div>
        <div className="row">
          <div className="filter-tabs" role="group" aria-label={t('insights.periodLabel')}>
            {[7, 14, 30].map((d) => (
              <button
                key={d}
                type="button"
                className={`filter-tab ${days === d ? 'active' : ''}`}
                onClick={() => setDays(d)}
              >
                {d}D
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={syncing || !data?.adapter_configured}
            onClick={handleSyncAll}
          >
            <RefreshCw size={14} strokeWidth={1.5} className={syncing ? 'spin' : ''} />{' '}
            {syncing ? t('insights.syncing') : t('insights.syncNow')}
          </button>
        </div>
      </div>

      {data && !data.adapter_configured && (
        <SchematicCard label={t('insights.setupLabel')} highlight>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="stack-sm" style={{ gap: 4 }}>
              <div className="section-heading">{t('insights.notConfiguredTitle')}</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>{t('insights.notConfiguredSub')}</div>
            </div>
            <button type="button" className="btn btn-primary" onClick={() => onNavigate('settings')}>
              {t('dashboard.configureAdapters')}
            </button>
          </div>
        </SchematicCard>
      )}

      {account ? (
        <SchematicCard
          label={t('insights.accountLabel', {
            username: account.username || account.ig_user_id,
            days: account.period_days,
          })}
          right={
            <span className="badge badge-good">
              <span className="dot" />
              {t('insights.syncedBadge', {
                date: new Date(account.synced_at).toLocaleString(locale),
              })}
            </span>
          }
          noPadding
        >
          <div className="metrics-strip">
            <div className="metric-cell">
              <div className="metric-label">
                <span>{t('insights.kpi.followers')}</span>
                <Users size={14} strokeWidth={1.5} />
              </div>
              <div className="metric-value">{fmtNum(account.followers_count)}</div>
              <div className="metric-sub">
                {t('insights.accountFollowersSub', {
                  following: fmtNum(account.follows_count),
                  posts: fmtNum(account.media_count),
                })}
              </div>
            </div>

            <div className="metric-cell">
              <div className="metric-label">
                <span>{t('insights.kpi.viewsWindow', { days: account.period_days })}</span>
                <Eye size={14} strokeWidth={1.5} />
              </div>
              <div className="metric-value">{fmtNum(account.totals?.views)}</div>
              <div className="metric-sub">
                {t('insights.accountReachSub', { reach: fmtNum(account.totals?.reach) })}
              </div>
            </div>

            <div className="metric-cell">
              <div className="metric-label">
                <span>{t('insights.kpi.interactions')}</span>
                <Heart size={14} strokeWidth={1.5} />
              </div>
              <div className="metric-value">{fmtNum(account.totals?.total_interactions)}</div>
              <div className="metric-sub">
                {t('insights.accountEngagedSub', {
                  accounts: fmtNum(account.totals?.accounts_engaged),
                })}
              </div>
            </div>

            <div className="metric-cell">
              <div className="metric-label">
                <span>{t('insights.kpi.profileVisits')}</span>
                <TrendingUp size={14} strokeWidth={1.5} />
              </div>
              <div className="metric-value">{fmtNum(account.totals?.profile_views)}</div>
              <div className="metric-sub">
                {t('insights.accountActionsSub', {
                  likes: fmtNum(account.totals?.likes ?? 0),
                  shares: fmtNum(account.totals?.shares ?? 0),
                  saves: fmtNum(account.totals?.saves ?? 0),
                })}
              </div>
            </div>
          </div>

          {account.error_message && (
            <div className="insight-warning-bar">
              <AlertTriangle size={14} strokeWidth={1.6} />
              <span>{account.error_message}</span>
            </div>
          )}
        </SchematicCard>
      ) : (
        data?.adapter_configured &&
        !loading && (
          <SchematicCard label={t('insights.emptyAccountLabel')}>
            <div className="empty-state" style={{ padding: '26px 20px' }}>
              <p className="empty-state-title">{t('insights.neverSyncedTitle')}</p>
              <p className="empty-state-sub">{t('insights.neverSyncedSub')}</p>
              <button
                type="button"
                className="btn btn-primary"
                style={{ marginTop: 8 }}
                disabled={syncing}
                onClick={handleSyncAll}
              >
                <RefreshCw size={14} strokeWidth={1.5} className={syncing ? 'spin' : ''} />{' '}
                {t('insights.syncNow')}
              </button>
            </div>
          </SchematicCard>
        )
      )}

      {account?.daily_series?.length > 0 && <DailyReachChart series={account.daily_series} t={t} />}

      {summary && summary.total_items > 0 && (
        <div className="insight-story-banner">
          <Clock size={14} strokeWidth={1.5} />
          <span>
            {t('insights.storyNotice', {
              minutes: data?.auto_sync_interval_minutes ?? 30,
              active: summary.active_stories_count,
              totalStories: summary.story_count,
            })}
          </span>
        </div>
      )}

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="filter-tabs">
          {[
            { id: '', label: t('insights.filter.all') },
            { id: 'FEED', label: t('insights.filter.feed') },
            { id: 'STORY', label: t('insights.filter.story') },
            { id: 'REELS', label: t('insights.filter.reels') },
          ].map((f) => (
            <button
              key={f.id}
              type="button"
              className={`filter-tab ${typeFilter === f.id ? 'active' : ''}`}
              onClick={() => setTypeFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="row" style={{ gap: 8 }}>
          <span className="sidebar-label">{t('insights.sortLabel')}</span>
          <select
            className="select"
            style={{ width: 200 }}
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            <option value="published_at">{t('insights.sort.recent')}</option>
            <option value="views">{t('insights.sort.views')}</option>
            <option value="reach">{t('insights.sort.reach')}</option>
            <option value="total_interactions">{t('insights.sort.interactions')}</option>
            <option value="likes">{t('insights.sort.likes')}</option>
            <option value="saved">{t('insights.sort.saved')}</option>
            <option value="shares">{t('insights.sort.shares')}</option>
            <option value="profile_visits">{t('insights.sort.profileVisits')}</option>
          </select>
        </div>
      </div>

      <SchematicCard
        label={t('insights.contentLabel')}
        right={
          <div className="row" style={{ gap: 6 }}>
            {summary?.avg_engagement_rate !== null && summary?.avg_engagement_rate !== undefined && (
              <span className="badge badge-good">
                {t('insights.avgErBadge', { er: summary.avg_engagement_rate })}
              </span>
            )}
            <span className="badge">{t('queue.recordsBadge', { count: items.length })}</span>
          </div>
        }
        noPadding
      >
        {loading && !data ? (
          <div className="empty-state">
            <p className="empty-state-sub">{t('insights.loading')}</p>
          </div>
        ) : items.length === 0 ? (
          <div className="empty-state">
            <BarChart3 size={28} strokeWidth={1.3} style={{ color: 'var(--muted)' }} />
            <p className="empty-state-title">{t('insights.emptyTitle')}</p>
            <p className="empty-state-sub">{t('insights.emptySub')}</p>
          </div>
        ) : (
          <div className="schematic-list">
            {items.map((item) => {
              const isStory = item.media_product_type === 'STORY';
              const isExpanded = expandedId === item.external_id;
              const navEntries = Object.entries(item.navigation_breakdown || {}).filter(([, v]) => v > 0);
              const totalNav = navEntries.reduce((s, [, v]) => s + v, 0) || item.navigation || 0;
              const snapshots = historyMap[item.external_id] || [];

              return (
                <div key={item.external_id} className="insight-item-block">
                  <div className="insight-row">
                    <MediaThumb item={item} />

                    <div className="stack-sm" style={{ gap: 8, minWidth: 0 }}>
                      <div className="row" style={{ gap: 6 }}>
                        <span className="badge badge-ink">{item.pub_type || item.media_product_type}</span>
                        {isStory && (
                          <span className={`badge ${item.is_active ? 'badge-good' : ''}`}>
                            <span className={`dot ${item.is_active ? '' : 'muted'}`} />
                            {item.is_active ? t('insights.storyActive') : t('insights.storyArchived')}
                          </span>
                        )}
                        <span className={`badge ${item.publication_id ? 'badge-accent' : ''}`}>
                          {item.publication_id
                            ? `INSTA-POSTER #${item.publication_id}`
                            : t('insights.originNative')}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                          {item.published_at ? new Date(item.published_at).toLocaleString(locale) : '—'}
                        </span>
                      </div>

                      {item.caption && (
                        <div style={{ fontSize: 12.5, lineHeight: 1.45, color: 'var(--ink)' }}>
                          {item.caption.slice(0, 140)}
                          {item.caption.length > 140 ? '…' : ''}
                        </div>
                      )}

                      <div className="insight-kpi-grid">
                        <div className="insight-kpi">
                          <span className="insight-kpi-lbl">{t('insights.kpi.views')}</span>
                          <span className="insight-kpi-val">{fmtNum(item.views)}</span>
                        </div>
                        <div className="insight-kpi">
                          <span className="insight-kpi-lbl">{t('insights.kpi.reach')}</span>
                          <span className="insight-kpi-val">{fmtNum(item.reach)}</span>
                        </div>
                        {isStory ? (
                          <>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.replies')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.replies)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.shares')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.shares)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.navigation')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.navigation)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.profileVisits')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.profile_visits)}</span>
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.likes')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.likes)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.comments')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.comments)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.saved')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.saved)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.shares')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.shares)}</span>
                            </div>
                            <div className="insight-kpi">
                              <span className="insight-kpi-lbl">{t('insights.kpi.profileVisits')}</span>
                              <span className="insight-kpi-val">{fmtNum(item.profile_visits)}</span>
                            </div>
                          </>
                        )}
                        <div className="insight-kpi">
                          <span className="insight-kpi-lbl">{t('insights.kpi.er')}</span>
                          <span className="insight-kpi-val">
                            {item.engagement_rate !== null && item.engagement_rate !== undefined
                              ? `${item.engagement_rate}%`
                              : '—'}
                          </span>
                        </div>
                      </div>

                      {isStory && navEntries.length > 0 && (
                        <div className="nav-breakdown-strip">
                          <span className="sidebar-label">{t('insights.navBreakdownLabel')}</span>
                          {navEntries.map(([k, v]) => {
                            const pct = totalNav > 0 ? Math.round((v / totalNav) * 100) : 0;
                            return (
                              <span key={k} className="tag">
                                {navLabel(k)}: <strong>{v}</strong> ({pct}%)
                              </span>
                            );
                          })}
                        </div>
                      )}

                      {item.error_message && (
                        <div style={{ fontSize: 11, color: 'var(--warn)' }}>{item.error_message}</div>
                      )}
                    </div>

                    <div className="row" style={{ gap: 6, alignSelf: 'flex-start' }}>
                      {item.permalink && (
                        <a
                          href={item.permalink}
                          target="_blank"
                          rel="noreferrer"
                          className="btn btn-sm"
                          title={t('insights.openInstagram')}
                        >
                          <ExternalLink size={12} strokeWidth={1.5} />
                        </a>
                      )}
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={syncingItemId === item.external_id || !data?.adapter_configured}
                        onClick={() => handleSyncItem(item)}
                        title={t('insights.refreshItem')}
                      >
                        <RefreshCw
                          size={12}
                          strokeWidth={1.5}
                          className={syncingItemId === item.external_id ? 'spin' : ''}
                        />
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => toggleHistory(item.external_id)}
                        title={t('insights.historyTitle')}
                      >
                        <History size={12} strokeWidth={1.5} />
                        <span>{item.snapshots_count}</span>
                        {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      </button>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="snapshot-drawer">
                      <div className="schematic-eyebrow" style={{ marginBottom: 8 }}>
                        {t('insights.historyDrawerTitle', { id: item.external_id })}
                      </div>
                      {historyLoading && snapshots.length === 0 ? (
                        <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{t('insights.loading')}</div>
                      ) : snapshots.length === 0 ? (
                        <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{t('insights.noSnapshots')}</div>
                      ) : (
                        <div className="snapshot-table-wrap">
                          <table className="snapshot-table">
                            <thead>
                              <tr>
                                <th>{t('insights.snapTime')}</th>
                                <th>{t('insights.kpi.views')}</th>
                                <th>{t('insights.kpi.reach')}</th>
                                <th>{isStory ? t('insights.kpi.replies') : t('insights.kpi.likes')}</th>
                                <th>{isStory ? t('insights.kpi.navigation') : t('insights.kpi.saved')}</th>
                                <th>{t('insights.kpi.shares')}</th>
                                <th>{t('insights.kpi.profileVisits')}</th>
                                <th>{t('insights.kpi.interactions')}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {snapshots.map((sn) => (
                                <tr key={sn.id}>
                                  <td>{new Date(sn.captured_at).toLocaleString(locale)}</td>
                                  <td>{fmtNum(sn.views)}</td>
                                  <td>{fmtNum(sn.reach)}</td>
                                  <td>{fmtNum(isStory ? sn.replies : sn.likes)}</td>
                                  <td>{fmtNum(isStory ? sn.navigation : sn.saved)}</td>
                                  <td>{fmtNum(sn.shares)}</td>
                                  <td>{fmtNum(sn.profile_visits)}</td>
                                  <td>{fmtNum(sn.total_interactions)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </SchematicCard>
    </div>
  );
}
