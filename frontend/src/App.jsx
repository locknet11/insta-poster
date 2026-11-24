import React, { useCallback, useEffect, useState } from 'react';
import { api, setUnauthorizedHandler } from './api.js';
import { useI18n } from './i18n.jsx';
import AuthView from './components/AuthView.jsx';
import Sidebar from './components/Sidebar.jsx';
import DashboardView from './components/DashboardView.jsx';
import PoolView from './components/PoolView.jsx';
import ComposerView from './components/ComposerView.jsx';
import QueueView from './components/QueueView.jsx';
import SettingsAgentView from './components/SettingsAgentView.jsx';
import UsersView from './components/UsersView.jsx';

export default function App() {
  const { t } = useI18n();
  const [auth, setAuth] = useState({
    loading: true,
    authenticated: false,
    needsSetup: false,
    user: null,
  });
  const [tab, setTab] = useState('dashboard');
  const [stats, setStats] = useState(null);
  const [pool, setPool] = useState([]);
  const [publications, setPublications] = useState([]);
  const [adapters, setAdapters] = useState([]);
  const [activity, setActivity] = useState([]);
  const [composerDraft, setComposerDraft] = useState({ pubType: 'post', imageIds: [] });
  const [editingPub, setEditingPub] = useState(null);
  const [toast, setToast] = useState(null);

  const notify = useCallback((message, type = 'ok') => {
    setToast({ message, type });
    setTimeout(() => {
      setToast((prev) => (prev && prev.message === message ? null : prev));
    }, 4200);
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const status = await api.authStatus();
      setAuth({
        loading: false,
        authenticated: Boolean(status.authenticated),
        needsSetup: Boolean(status.needs_setup),
        user: status.user || null,
      });
    } catch {
      setAuth({
        loading: false,
        authenticated: false,
        needsSetup: false,
        user: null,
      });
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAuth((prev) => {
        if (!prev.authenticated) return prev;
        return { ...prev, authenticated: false, user: null };
      });
    });
    checkAuth();
    return () => setUnauthorizedHandler(null);
  }, [checkAuth]);

  const refreshAll = useCallback(async () => {
    try {
      const [s, p, pubs, ad, act] = await Promise.all([
        api.stats(),
        api.listPool({ limit: 400 }),
        api.listPublications({ limit: 200 }),
        api.listAdapters(false),
        api.activity(40),
      ]);
      setStats(s);
      setPool(p);
      setPublications(pubs);
      setAdapters(ad);
      setActivity(act);
    } catch (err) {
      if (err.status === 401) return;
      notify(t('common.loadError', { error: err.message }), 'error');
    }
  }, [notify, t]);

  useEffect(() => {
    if (!auth.authenticated) return undefined;
    refreshAll();
    const timer = setInterval(refreshAll, 20000);
    return () => clearInterval(timer);
  }, [auth.authenticated, refreshAll]);

  const handleLogout = async () => {
    try {
      await api.authLogout();
    } catch {
      // ignore
    }
    setStats(null);
    setPool([]);
    setPublications([]);
    setAdapters([]);
    setActivity([]);
    setEditingPub(null);
    setTab('dashboard');
    await checkAuth();
  };

  if (auth.loading) {
    return (
      <div className="auth-screen">
        <div className="schematic-eyebrow">{t('common.loadingSession')}</div>
      </div>
    );
  }

  if (!auth.authenticated) {
    return (
      <AuthView
        needsSetup={auth.needsSetup}
        onAuthenticated={(user) => {
          setAuth({
            loading: false,
            authenticated: true,
            needsSetup: false,
            user,
          });
        }}
      />
    );
  }

  const openComposerWith = (pubType, imageIds) => {
    setEditingPub(null);
    setComposerDraft({ pubType, imageIds });
    setTab('composer');
  };

  const openEditorForPub = (pub) => {
    setEditingPub(pub);
    setTab('composer');
  };

  return (
    <div className="app-shell">
      <Sidebar
        activeTab={tab}
        onSelect={(next) => {
          if (next === 'composer' && tab !== 'composer') {
            setEditingPub(null);
          }
          setTab(next);
        }}
        stats={stats}
        currentUser={auth.user}
        onLogout={handleLogout}
      />
      <main className="main">
        {tab === 'dashboard' && (
          <DashboardView
            stats={stats}
            publications={publications}
            activity={activity}
            adapters={adapters}
            onNavigate={(next) => {
              if (next === 'composer') setEditingPub(null);
              setTab(next);
            }}
            onRefresh={refreshAll}
          />
        )}
        {tab === 'pool' && (
          <PoolView
            pool={pool}
            onRefresh={refreshAll}
            onCompose={openComposerWith}
            notify={notify}
          />
        )}
        {tab === 'composer' && (
          <ComposerView
            pool={pool}
            adapters={adapters}
            defaultAdapter={stats?.default_adapter || 'dry_run'}
            initialDraft={composerDraft}
            editingPublication={editingPub}
            currentUser={auth.user}
            onCancelEdit={() => {
              setEditingPub(null);
              setTab('queue');
            }}
            onCreated={async (msg) => {
              setEditingPub(null);
              notify(msg);
              await refreshAll();
              setTab('queue');
            }}
            notify={notify}
          />
        )}
        {tab === 'queue' && (
          <QueueView
            publications={publications}
            pool={pool}
            onEdit={openEditorForPub}
            onRefresh={refreshAll}
            notify={notify}
          />
        )}
        {tab === 'settings' && (
          <SettingsAgentView
            adapters={adapters}
            onRefresh={refreshAll}
            notify={notify}
          />
        )}
        {tab === 'users' && (
          <UsersView
            currentUser={auth.user}
            notify={notify}
          />
        )}
      </main>
      {toast && <div className={`toast ${toast.type === 'error' ? 'error' : ''}`}>{toast.message}</div>}
    </div>
  );
}
