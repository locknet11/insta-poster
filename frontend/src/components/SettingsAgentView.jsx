import React, { useEffect, useState } from 'react';
import { Copy, Check, ExternalLink, PlugZap, Save, KeyRound } from 'lucide-react';
import { api } from '../api.js';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

export default function SettingsAgentView({ adapters, onNavigate, onRefresh, notify }) {
  const { t } = useI18n();
  const [settings, setSettings] = useState(null);
  const [schema, setSchema] = useState(null);
  const [copiedKey, setCopiedKey] = useState('');

  const [defaultAdapter, setDefaultAdapter] = useState('dry_run');
  const [schedulerEnabled, setSchedulerEnabled] = useState(true);

  // Graph API fields
  const [graphToken, setGraphToken] = useState('');
  const [graphUserId, setGraphUserId] = useState('');
  const [graphPublicUrl, setGraphPublicUrl] = useState('');
  const [graphImgbbKey, setGraphImgbbKey] = useState('');
  const [graphHost, setGraphHost] = useState('graph.instagram.com');
  const [graphVersion, setGraphVersion] = useState('v21.0');

  useEffect(() => {
    Promise.all([api.getSettings(), api.agentSchema()]).then(([s, sc]) => {
      setSettings(s);
      setSchema(sc);
      setDefaultAdapter(s.default_adapter || 'dry_run');
      setSchedulerEnabled(Boolean(s.scheduler_enabled));
      setGraphUserId(s.graph_api?.ig_user_id || '');
      setGraphPublicUrl(s.graph_api?.public_base_url || '');
      setGraphHost(s.graph_api?.api_host || 'graph.instagram.com');
      setGraphVersion(s.graph_api?.api_version || 'v21.0');
    });
  }, []);

  const copyText = (key, text) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(''), 1800);
  };

  const persistSettings = async (silent = false) => {
    const updated = await api.updateSettings({
      default_adapter: defaultAdapter,
      scheduler_enabled: schedulerEnabled,
      graph_api: {
        ig_user_id: graphUserId,
        public_base_url: graphPublicUrl,
        api_host: graphHost,
        api_version: graphVersion,
        ...(graphToken.trim() ? { access_token: graphToken.trim() } : {}),
        ...(graphImgbbKey.trim() ? { imgbb_api_key: graphImgbbKey.trim() } : {}),
      },
    });
    setSettings(updated);
    setGraphToken('');
    setGraphImgbbKey('');
    if (!silent) {
      notify(t('settings.toastSaved'));
      await onRefresh();
    }
    return updated;
  };

  const saveSettings = async (e) => {
    e.preventDefault();
    try {
      await persistSettings(false);
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const testAdapter = async (name) => {
    try {
      await persistSettings(true);
      const res = await api.checkAdapter(name, true);
      notify(`${res.name}: ${res.message}`, res.available && res.configured ? 'ok' : 'error');
      await onRefresh();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('settings.breadcrumbRoot')} <span className="slash">//</span> {t('settings.breadcrumbPage')}
          </div>
          <h2>{t('settings.title')}</h2>
          <p>{t('settings.subtitle')}</p>
        </div>
        <a href="/docs" target="_blank" rel="noreferrer" className="btn">
          <ExternalLink size={14} strokeWidth={1.5} /> {t('settings.openDocs')}
        </a>
      </div>

      <div className="two-col">
        <SchematicCard
          as="form"
          onSubmit={saveSettings}
          label={t('settings.configLabel')}
          bodyClassName="stack"
        >
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <div className="field" style={{ flex: 1, minWidth: 220 }}>
              <label>{t('settings.defaultAdapter')}</label>
              <select className="select" value={defaultAdapter} onChange={(e) => setDefaultAdapter(e.target.value)}>
                <option value="dry_run">{t('settings.optDryRun')}</option>
                <option value="graph_api">{t('settings.optGraphApi')}</option>
              </select>
            </div>
            <label className="checkbox-label" style={{ paddingBottom: 8 }}>
              <input
                type="checkbox"
                checked={schedulerEnabled}
                onChange={(e) => setSchedulerEnabled(e.target.checked)}
              />
              {t('settings.schedulerActive')}
            </label>
          </div>

          <hr className="dashed-divider" />

          <div className="stack-sm">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="subsection-title">{t('settings.graphSection')}</span>
              <button type="button" className="btn btn-sm" onClick={() => testAdapter('graph_api')}>
                <PlugZap size={13} strokeWidth={1.5} /> {t('settings.testConnection')}
              </button>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{t('settings.graphHelp')}</div>
            <div className="row">
              <div className="field" style={{ flex: 2, minWidth: 200 }}>
                <label>{t('settings.graphHost')}</label>
                <select className="select" value={graphHost} onChange={(e) => setGraphHost(e.target.value)}>
                  <option value="graph.instagram.com">{t('settings.graphHostIg')}</option>
                  <option value="graph.facebook.com">{t('settings.graphHostFb')}</option>
                </select>
              </div>
              <div className="field" style={{ width: 110 }}>
                <label>{t('settings.apiVersion')}</label>
                <input className="input" value={graphVersion} onChange={(e) => setGraphVersion(e.target.value)} />
              </div>
            </div>
            <div className="field">
              <label>
                {t('settings.accessToken')}{' '}
                {settings?.graph_api?.has_access_token ? `(${settings.graph_api.access_token_masked})` : ''}
              </label>
              <input
                className="input"
                type="password"
                autoComplete="off"
                placeholder={t('settings.keepCurrentPlaceholder')}
                value={graphToken}
                onChange={(e) => setGraphToken(e.target.value)}
              />
            </div>
            <div className="field">
              <label>
                {t('settings.igUserId')}{' '}
                {graphHost === 'graph.instagram.com' ? t('settings.igUserIdOpt') : t('settings.igUserIdReq')}
              </label>
              <input
                className="input"
                autoComplete="off"
                value={graphUserId}
                onChange={(e) => setGraphUserId(e.target.value)}
                placeholder={
                  graphHost === 'graph.instagram.com' ? t('settings.igUserIdPlaceholderOpt') : '178414...'
                }
              />
            </div>
            <div className="field">
              <label>
                {t('settings.imgbbKey')}{' '}
                {settings?.graph_api?.has_imgbb_api_key ? `(${settings.graph_api.imgbb_api_key_masked})` : ''}
              </label>
              <input
                className="input"
                type="password"
                autoComplete="off"
                value={graphImgbbKey}
                onChange={(e) => setGraphImgbbKey(e.target.value)}
                placeholder={t('settings.imgbbPlaceholder')}
              />
            </div>
            <div className="field">
              <label>{t('settings.publicBaseUrl')}</label>
              <input
                className="input"
                autoComplete="off"
                value={graphPublicUrl}
                onChange={(e) => setGraphPublicUrl(e.target.value)}
                placeholder="https://insta.yourdomain.com"
              />
            </div>
          </div>

          <div className="row" style={{ justifyContent: 'flex-end', paddingTop: 4 }}>
            <button type="submit" className="btn btn-primary">
              <Save size={14} strokeWidth={1.5} /> {t('settings.saveConfig')}
            </button>
          </div>
        </SchematicCard>

        <div className="stack">
          <SchematicCard
            label={t('settings.agentLabel')}
            right={
              onNavigate && (
                <button type="button" className="btn btn-sm btn-primary" onClick={() => onNavigate('users')}>
                  <KeyRound size={12} strokeWidth={1.5} /> {t('settings.manageApiKeys')}
                </button>
              )
            }
            bodyClassName="stack"
          >
            <p style={{ margin: 0, fontSize: 12, color: 'var(--muted)' }}>{t('settings.agentDesc')}</p>

            <div className="stack-sm">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="subsection-title">{t('settings.cliTitle')}</span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => copyText('cli', (schema?.cli_examples || []).join('\n'))}
                >
                  {copiedKey === 'cli' ? <Check size={12} strokeWidth={1.5} /> : <Copy size={12} strokeWidth={1.5} />}{' '}
                  {copiedKey === 'cli' ? t('common.copied') : t('common.copy')}
                </button>
              </div>
              <pre className="code-block">{(schema?.cli_examples || []).join('\n')}</pre>
            </div>

            <hr className="dashed-divider" />

            <div className="stack-sm">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="subsection-title">{t('settings.sdkTitle')}</span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => copyText('sdk', schema?.python_code_mode || '')}
                >
                  {copiedKey === 'sdk' ? <Check size={12} strokeWidth={1.5} /> : <Copy size={12} strokeWidth={1.5} />}{' '}
                  {copiedKey === 'sdk' ? t('common.copied') : t('common.copy')}
                </button>
              </div>
              <pre className="code-block">{schema?.python_code_mode || ''}</pre>
            </div>
          </SchematicCard>

          <SchematicCard label={t('settings.healthLabel')} noPadding>
            <div className="schematic-list">
              {adapters.map((a) => {
                const ok = a.configured && a.available;
                return (
                  <div
                    key={a.name}
                    className="schematic-row"
                    style={{ flexDirection: 'column', alignItems: 'stretch', gap: 6 }}
                  >
                    <div className="row" style={{ justifyContent: 'space-between' }}>
                      <span style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: 12 }}>
                        {a.name}
                      </span>
                      <span className={`badge ${ok ? 'badge-good' : 'badge-warn'}`}>
                        <span className={`dot ${ok ? '' : 'warn'}`} />
                        {ok ? t('settings.statusReady') : t('settings.statusPending')}
                      </span>
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{a.message}</div>
                  </div>
                );
              })}
            </div>
          </SchematicCard>
        </div>
      </div>
    </div>
  );
}
