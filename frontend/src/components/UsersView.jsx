import React, { useCallback, useEffect, useState } from 'react';
import { UserPlus, Trash2, KeyRound, RefreshCw, Plus, Copy, Check, X } from 'lucide-react';
import { api } from '../api.js';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

export default function UsersView({ currentUser, notify }) {
  const { t, locale } = useI18n();
  const [users, setUsers] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [loading, setLoading] = useState(true);

  // Create API key form
  const [newKeyName, setNewKeyName] = useState('');
  const [creatingKey, setCreatingKey] = useState(false);
  const [revealedKey, setRevealedKey] = useState(null);
  const [copiedId, setCopiedId] = useState('');

  // Create user form
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [creating, setCreating] = useState(false);

  // Change own password form
  const [currentPw, setCurrentPw] = useState('');
  const [nextPw, setNextPw] = useState('');
  const [confirmNextPw, setConfirmNextPw] = useState('');
  const [changingOwn, setChangingOwn] = useState(false);

  // Reset password modal for any user
  const [resetTarget, setResetTarget] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetting, setResetting] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [userList, keyList] = await Promise.all([api.listUsers(), api.listApiKeys()]);
      setUsers(userList);
      setApiKeys(keyList);
    } catch (err) {
      notify(t('users.toastLoadError', { error: err.message }), 'error');
    } finally {
      setLoading(false);
    }
  }, [notify, t]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const copyValue = (id, text) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(''), 1800);
  };

  const handleCreateApiKey = async (e) => {
    e.preventDefault();
    if (!newKeyName.trim()) return;
    setCreatingKey(true);
    try {
      const created = await api.createApiKey(newKeyName.trim());
      setNewKeyName('');
      setRevealedKey(created);
      notify(t('apikeys.toastCreated', { name: created.name }));
      await loadAll();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setCreatingKey(false);
    }
  };

  const handleRevokeApiKey = async (k) => {
    if (!window.confirm(t('apikeys.confirmRevoke', { name: k.name }))) return;
    try {
      await api.revokeApiKey(k.id);
      if (revealedKey && revealedKey.id === k.id) setRevealedKey(null);
      notify(t('apikeys.toastRevoked', { name: k.name }));
      await loadAll();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const handleCreateUser = async (e) => {
    e.preventDefault();
    if (!newUsername.trim() || !newPassword) return;
    setCreating(true);
    try {
      const created = await api.createUser(newUsername.trim(), newPassword);
      setNewUsername('');
      setNewPassword('');
      notify(t('users.toastCreated', { username: created.username }));
      await loadAll();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleChangeOwnPassword = async (e) => {
    e.preventDefault();
    if (nextPw.length < 6) {
      notify(t('users.errNewPwMin'), 'error');
      return;
    }
    if (nextPw !== confirmNextPw) {
      notify(t('users.errNewPwMismatch'), 'error');
      return;
    }
    setChangingOwn(true);
    try {
      await api.changePassword(currentPw, nextPw);
      setCurrentPw('');
      setNextPw('');
      setConfirmNextPw('');
      notify(t('users.toastOwnPwUpdated'));
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setChangingOwn(false);
    }
  };

  const handleResetPassword = async (e) => {
    e.preventDefault();
    if (!resetTarget) return;
    if (resetPassword.length < 6) {
      notify(t('auth.errPasswordMin'), 'error');
      return;
    }
    setResetting(true);
    try {
      await api.resetUserPassword(resetTarget.id, resetPassword);
      notify(t('users.toastResetUpdated', { username: resetTarget.username }));
      setResetTarget(null);
      setResetPassword('');
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setResetting(false);
    }
  };

  const handleDelete = async (user) => {
    if (!window.confirm(t('users.confirmDelete', { username: user.username }))) {
      return;
    }
    try {
      await api.deleteUser(user.id);
      notify(t('users.toastDeleted', { username: user.username }));
      await loadAll();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('users.breadcrumbRoot')} <span className="slash">//</span> {t('users.breadcrumbPage')}
          </div>
          <h2>{t('users.title')}</h2>
          <p>{t('users.subtitle')}</p>
        </div>
        <button type="button" className="btn" onClick={loadAll}>
          <RefreshCw size={14} strokeWidth={1.5} /> {t('common.refresh')}
        </button>
      </div>

      {/* API Keys Management for AI Agents */}
      <SchematicCard
        label={t('apikeys.sectionLabel', { count: apiKeys.length })}
        right={<span className="badge">X-API-KEY // BEARER</span>}
        noPadding
      >
        <div style={{ padding: 18, borderBottom: '1px dashed var(--dashed)' }} className="stack-sm">
          <p style={{ margin: 0, fontSize: 12, color: 'var(--muted)' }}>{t('apikeys.sectionSub')}</p>

          <form onSubmit={handleCreateApiKey} className="row" style={{ alignItems: 'flex-end' }}>
            <div className="field" style={{ flex: 1, minWidth: 220 }}>
              <label>
                {t('apikeys.nameLabel')}
                <span className="req">*</span>
              </label>
              <input
                className="input"
                type="text"
                autoComplete="off"
                placeholder={t('apikeys.namePlaceholder')}
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
              />
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={creatingKey || !newKeyName.trim()}
            >
              <Plus size={14} strokeWidth={1.5} />{' '}
              {creatingKey ? t('apikeys.creating') : t('apikeys.createBtn')}
            </button>
          </form>

          {revealedKey && (
            <div className="secret-reveal">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="subsection-title" style={{ color: 'var(--good-text)' }}>
                  {t('apikeys.revealTitle', { name: revealedKey.name })}
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setRevealedKey(null)}
                  title={t('apikeys.dismiss')}
                >
                  <X size={12} strokeWidth={1.5} /> {t('apikeys.dismiss')}
                </button>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--good-text)' }}>{t('apikeys.revealWarn')}</div>
              <div className="row" style={{ alignItems: 'stretch' }}>
                <code className="secret-reveal-key">{revealedKey.api_key}</code>
                <button
                  type="button"
                  className="btn btn-ink"
                  onClick={() => copyValue('raw', revealedKey.api_key)}
                >
                  {copiedId === 'raw' ? <Check size={13} strokeWidth={1.5} /> : <Copy size={13} strokeWidth={1.5} />}{' '}
                  {copiedId === 'raw' ? t('common.copied') : t('common.copy')}
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => copyValue('hdr', `X-API-Key: ${revealedKey.api_key}`)}
                >
                  {copiedId === 'hdr' ? <Check size={13} strokeWidth={1.5} /> : <Copy size={13} strokeWidth={1.5} />}{' '}
                  {copiedId === 'hdr' ? t('common.copied') : t('apikeys.copyHeader')}
                </button>
              </div>
            </div>
          )}
        </div>

        {apiKeys.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-title">{t('apikeys.emptyTitle')}</p>
            <p className="empty-state-sub">{t('apikeys.emptySub')}</p>
          </div>
        ) : (
          <div className="schematic-list">
            {apiKeys.map((k) => (
              <div key={k.id} className="user-row">
                <div>
                  <div className="row" style={{ gap: 8 }}>
                    <strong style={{ fontSize: 12.5 }}>{k.name}</strong>
                    <span className="badge">{k.key_prefix}••••••••</span>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>#{k.id}</span>
                  </div>
                  <div style={{ fontSize: 10.5, color: 'var(--muted)', marginTop: 4 }}>
                    {t('apikeys.metaRow', {
                      created: new Date(k.created_at).toLocaleDateString(locale),
                      by: k.created_by,
                      lastUsed: k.last_used_at
                        ? new Date(k.last_used_at).toLocaleString(locale)
                        : t('common.never').toUpperCase(),
                    })}
                  </div>
                </div>
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => handleRevokeApiKey(k)}
                >
                  <Trash2 size={12} strokeWidth={1.5} /> {t('apikeys.revoke')}
                </button>
              </div>
            ))}
          </div>
        )}
      </SchematicCard>

      <div className="two-col">
        <div className="stack">
          <SchematicCard
            label={t('users.directoryLabel', { count: users.length })}
            right={
              <span className="badge badge-good">
                <span className="dot" /> {t('users.authActive')}
              </span>
            }
            noPadding
          >
            {loading ? (
              <div style={{ padding: 18, color: 'var(--muted)', fontSize: 12 }}>{t('users.loading')}</div>
            ) : users.length === 0 ? (
              <div className="empty-state">
                <p className="empty-state-title">{t('users.emptyTitle')}</p>
                <p className="empty-state-sub">{t('users.emptySub')}</p>
              </div>
            ) : (
              <div className="schematic-list">
                {users.map((u) => {
                  const isSelf = currentUser && u.id === currentUser.id;
                  const canDelete = !isSelf && users.length > 1;
                  return (
                    <div key={u.id} className="user-row">
                      <div>
                        <div className="row" style={{ gap: 8 }}>
                          <strong style={{ fontSize: 12.5 }}>@{u.username}</strong>
                          {isSelf && <span className="badge badge-accent">{t('users.currentSession')}</span>}
                        </div>
                        <div style={{ fontSize: 10.5, color: 'var(--muted)', marginTop: 4 }}>
                          {t('users.metaRow', {
                            created: new Date(u.created_at).toLocaleDateString(locale),
                            lastLogin: u.last_login_at
                              ? new Date(u.last_login_at).toLocaleString(locale)
                              : t('common.never').toUpperCase(),
                          })}
                        </div>
                      </div>
                      <div className="row" style={{ gap: 6 }}>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            setResetTarget(u);
                            setResetPassword('');
                          }}
                          title={t('users.keyTitle')}
                        >
                          <KeyRound size={12} strokeWidth={1.5} /> {t('users.keyBtn')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          disabled={!canDelete}
                          onClick={() => handleDelete(u)}
                          title={
                            isSelf
                              ? t('users.deleteSelfTitle')
                              : users.length <= 1
                              ? t('users.deleteLastTitle')
                              : t('users.deleteTitle')
                          }
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

          <SchematicCard
            as="form"
            onSubmit={handleCreateUser}
            label={t('users.addLabel')}
            bodyClassName="stack"
          >
            <div className="row">
              <div className="field" style={{ flex: 1, minWidth: 160 }}>
                <label>
                  {t('users.usernameLabel')}
                  <span className="req">*</span>
                </label>
                <input
                  className="input"
                  type="text"
                  autoComplete="off"
                  placeholder={t('users.usernamePlaceholder')}
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                />
              </div>
              <div className="field" style={{ flex: 1, minWidth: 160 }}>
                <label>
                  {t('users.passwordMinLabel')}
                  <span className="req">*</span>
                </label>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  placeholder="••••••••"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
              </div>
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={creating || !newUsername.trim() || newPassword.length < 6}
              >
                <UserPlus size={14} strokeWidth={1.5} />{' '}
                {creating ? t('users.creating') : t('users.createBtn')}
              </button>
            </div>
          </SchematicCard>
        </div>

        <div className="stack">
          {currentUser && (
            <SchematicCard
              as="form"
              onSubmit={handleChangeOwnPassword}
              label={t('users.ownPwLabel', { username: currentUser.username })}
              bodyClassName="stack"
            >
              <h3 className="section-heading">{t('users.ownPwTitle')}</h3>
              <div className="field">
                <label>
                  {t('users.currentPw')}
                  <span className="req">*</span>
                </label>
                <input
                  className="input"
                  type="password"
                  autoComplete="current-password"
                  value={currentPw}
                  onChange={(e) => setCurrentPw(e.target.value)}
                />
              </div>
              <div className="field">
                <label>
                  {t('users.newPw')}
                  <span className="req">*</span>
                </label>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  placeholder={t('auth.passwordPlaceholderSetup')}
                  value={nextPw}
                  onChange={(e) => setNextPw(e.target.value)}
                />
              </div>
              <div className="field">
                <label>
                  {t('users.confirmNewPw')}
                  <span className="req">*</span>
                </label>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={confirmNextPw}
                  onChange={(e) => setConfirmNextPw(e.target.value)}
                />
              </div>
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={changingOwn || !currentPw || !nextPw || !confirmNextPw}
                >
                  <KeyRound size={14} strokeWidth={1.5} />{' '}
                  {changingOwn ? t('users.updating') : t('users.updatePwBtn')}
                </button>
              </div>
            </SchematicCard>
          )}

          <SchematicCard label={t('users.deployLabel')} bodyClassName="stack-sm">
            <div style={{ fontSize: 11.5, color: 'var(--ink-secondary)', lineHeight: 1.65 }}>
              <div>
                <strong>WEBAPP //</strong> {t('users.deployWeb')}
              </div>
              <div style={{ marginTop: 6 }}>
                <strong>BOOTSTRAP //</strong> {t('users.deployBootstrap')}
              </div>
              <div style={{ marginTop: 6 }}>
                <strong>AGENTS //</strong> {t('users.deployAgents')}
              </div>
            </div>
          </SchematicCard>
        </div>
      </div>

      {resetTarget && (
        <div className="modal-backdrop" onClick={() => setResetTarget(null)}>
          <div className="modal-shell" onClick={(e) => e.stopPropagation()}>
            <SchematicCard
              as="form"
              onSubmit={handleResetPassword}
              label={t('users.resetModalLabel', { username: resetTarget.username })}
              highlight
              bodyClassName="stack"
            >
              <h3 className="section-heading">
                {t('users.resetModalTitle', { username: resetTarget.username })}
              </h3>
              <p style={{ margin: 0, fontSize: 12, color: 'var(--muted)' }}>{t('users.resetModalSub')}</p>
              <div className="field">
                <label>
                  {t('users.resetPwLabel')}
                  <span className="req">*</span>
                </label>
                <input
                  className="input"
                  type="password"
                  autoFocus
                  autoComplete="new-password"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                />
              </div>
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button type="button" className="btn" onClick={() => setResetTarget(null)}>
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={resetting || resetPassword.length < 6}
                >
                  {resetting ? t('users.saving') : t('users.saveNewKey')}
                </button>
              </div>
            </SchematicCard>
          </div>
        </div>
      )}
    </div>
  );
}
