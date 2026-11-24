import React from 'react';
import {
  Camera,
  LayoutDashboard,
  Images,
  Sparkles,
  CalendarClock,
  Terminal,
  Users,
  LogOut,
} from 'lucide-react';
import { LanguageSwitcher, useI18n } from '../i18n.jsx';

export default function Sidebar({ activeTab, onSelect, stats, currentUser, onLogout }) {
  const { t } = useI18n();

  const nav = [
    { id: 'dashboard', label: t('sidebar.nav.dashboard'), icon: LayoutDashboard },
    { id: 'pool', label: t('sidebar.nav.pool'), icon: Images, badge: stats?.pool_available ?? null },
    { id: 'composer', label: t('sidebar.nav.composer'), icon: Sparkles },
    { id: 'queue', label: t('sidebar.nav.queue'), icon: CalendarClock, badge: stats?.publications_scheduled ?? null },
    { id: 'settings', label: t('sidebar.nav.settings'), icon: Terminal },
    { id: 'users', label: t('sidebar.nav.users'), icon: Users },
  ];

  const adapterName = stats?.default_adapter || 'dry_run';
  const isDry = adapterName === 'dry_run';

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <Camera size={18} strokeWidth={1.5} />
        </div>
        <div>
          <h1>Insta-Poster</h1>
          <span>{t('sidebar.subtitle')}</span>
        </div>
      </div>

      <div className="sidebar-section-label">{t('sidebar.navSection')}</div>

      <nav className="nav-list">
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={`nav-btn ${activeTab === item.id ? 'active' : ''}`}
              onClick={() => onSelect(item.id)}
            >
              <Icon size={15} strokeWidth={1.5} />
              <span>{item.label}</span>
              {item.badge !== null && item.badge !== undefined && (
                <span className="nav-badge">{String(item.badge).padStart(2, '0')}</span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-kv">
          <span className="sidebar-label">{t('sidebar.adapter')}</span>
          <span className="adapter-pill">
            <span className={`dot ${isDry ? 'warn' : ''}`} />
            <span>{adapterName}</span>
          </span>
        </div>
        <div className="sidebar-kv">
          <span className="sidebar-label">{t('sidebar.scheduler')}</span>
          <span style={{ color: stats?.scheduler_enabled ? 'var(--good-text)' : 'var(--muted)', fontWeight: 500 }}>
            {stats?.scheduler_enabled ? t('common.active') : t('common.paused')}
          </span>
        </div>
        <div className="sidebar-kv">
          <span className="sidebar-label">{t('sidebar.language')}</span>
          <LanguageSwitcher />
        </div>

        {currentUser && (
          <div className="sidebar-user">
            <span
              className="mono"
              style={{
                fontSize: 11.5,
                fontWeight: 600,
                color: 'var(--ink)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              @{currentUser.username}
            </span>
            <button
              type="button"
              className="btn btn-sm"
              onClick={onLogout}
              title={t('sidebar.logoutTitle')}
            >
              <LogOut size={12} strokeWidth={1.5} /> {t('sidebar.logout')}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
