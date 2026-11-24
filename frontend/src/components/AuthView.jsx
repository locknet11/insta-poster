import React, { useState } from 'react';
import { Camera, Lock, ShieldCheck, LogIn } from 'lucide-react';
import { api } from '../api.js';
import { LanguageSwitcher, useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

export default function AuthView({ needsSetup, onAuthenticated }) {
  const { t } = useI18n();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    const cleanUser = username.trim();
    if (!cleanUser) {
      setError(t('auth.errUsernameRequired'));
      return;
    }
    if (!password) {
      setError(t('auth.errPasswordRequired'));
      return;
    }

    if (needsSetup) {
      if (password.length < 6) {
        setError(t('auth.errPasswordMin'));
        return;
      }
      if (password !== confirmPassword) {
        setError(t('auth.errPasswordMismatch'));
        return;
      }
    }

    setSubmitting(true);
    try {
      const res = needsSetup
        ? await api.authSetup(cleanUser, password)
        : await api.authLogin(cleanUser, password);
      onAuthenticated(res.user);
    } catch (err) {
      setError(err.message || t('auth.errGeneric'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-wrap">
        <SchematicCard
          as="form"
          onSubmit={handleSubmit}
          label={needsSetup ? t('auth.eyebrowSetup') : t('auth.eyebrowLogin')}
          right={<LanguageSwitcher />}
          bodyClassName="stack"
        >
          <div>
            <div className="auth-brand-row">
              <div className="brand-mark">
                <Camera size={18} strokeWidth={1.5} />
              </div>
              <span className="schematic-eyebrow">{t('auth.brandSub')}</span>
            </div>
            <h1 className="auth-title">
              {needsSetup ? t('auth.titleSetup') : t('auth.titleLogin')}
            </h1>
            <p className="auth-sub">
              {needsSetup ? t('auth.subSetup') : t('auth.subLogin')}
            </p>
          </div>

          {needsSetup && (
            <div className="auth-notice">
              <ShieldCheck size={15} strokeWidth={1.5} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{t('auth.setupNotice')}</span>
            </div>
          )}

          <div className="field">
            <label htmlFor="auth-username">
              {t('auth.username')}
              <span className="req">*</span>
            </label>
            <input
              id="auth-username"
              className="input"
              type="text"
              autoFocus
              autoComplete="username"
              placeholder={needsSetup ? t('auth.usernamePlaceholderSetup') : t('auth.usernamePlaceholderLogin')}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="auth-password">
              {t('auth.password')}
              <span className="req">*</span>
            </label>
            <input
              id="auth-password"
              className="input"
              type="password"
              autoComplete={needsSetup ? 'new-password' : 'current-password'}
              placeholder={needsSetup ? t('auth.passwordPlaceholderSetup') : '••••••••'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {needsSetup && (
            <div className="field">
              <label htmlFor="auth-confirm">
                {t('auth.confirmPassword')}
                <span className="req">*</span>
              </label>
              <input
                id="auth-confirm"
                className="input"
                type="password"
                autoComplete="new-password"
                placeholder={t('auth.confirmPlaceholder')}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>
          )}

          {error && <div className="auth-error">{error}</div>}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting}
            style={{ width: '100%', padding: '11px 16px' }}
          >
            {needsSetup ? <Lock size={14} strokeWidth={1.5} /> : <LogIn size={14} strokeWidth={1.5} />}
            {submitting
              ? t('auth.verifying')
              : needsSetup
              ? t('auth.submitSetup')
              : t('auth.submitLogin')}
          </button>
        </SchematicCard>
      </div>
    </div>
  );
}
