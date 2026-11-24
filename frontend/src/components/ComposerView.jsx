import React, { useEffect, useState } from 'react';
import { Send, CalendarClock, FileText, Save, X } from 'lucide-react';
import { api } from '../api.js';
import { useI18n } from '../i18n.jsx';
import SchematicCard from './SchematicCard.jsx';

function isoToDatetimeLocal(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function ComposerView({
  pool,
  adapters,
  defaultAdapter,
  initialDraft,
  editingPublication,
  currentUser,
  onCancelEdit,
  onCreated,
  notify,
}) {
  const { t, locale } = useI18n();
  const isEditing = Boolean(editingPublication);

  const [pubType, setPubType] = useState(initialDraft?.pubType || 'post');
  const [selectedIds, setSelectedIds] = useState(initialDraft?.imageIds || []);
  const [fitMode, setFitMode] = useState('auto');
  const [caption, setCaption] = useState('');
  const [hashtags, setHashtags] = useState('');
  const [adapter, setAdapter] = useState(defaultAdapter || 'dry_run');
  const [scheduleMode, setScheduleMode] = useState('now'); // now | schedule | draft
  const [scheduledAt, setScheduledAt] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (editingPublication) {
      setPubType(editingPublication.pub_type || 'post');
      setSelectedIds(editingPublication.image_ids || []);
      setFitMode(editingPublication.fit_mode || 'auto');
      setCaption(editingPublication.caption || '');
      setHashtags((editingPublication.hashtags || []).join(' '));
      setAdapter(editingPublication.adapter || defaultAdapter || 'dry_run');
      if (editingPublication.scheduled_at) {
        setScheduleMode('schedule');
        setScheduledAt(isoToDatetimeLocal(editingPublication.scheduled_at));
      } else {
        setScheduleMode('draft');
        setScheduledAt('');
      }
      return;
    }

    setPubType(initialDraft?.pubType || 'post');
    setSelectedIds(initialDraft?.imageIds || []);
    setFitMode('auto');
    setCaption('');
    setHashtags('');
    setAdapter(defaultAdapter || 'dry_run');
    setScheduleMode('now');
    setScheduledAt('');
  }, [editingPublication, initialDraft, defaultAdapter]);

  const toggleImage = (id) => {
    if (pubType === 'carousel') {
      setSelectedIds((prev) =>
        prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 10 ? [...prev, id] : prev
      );
    } else {
      setSelectedIds([id]);
    }
  };

  const switchType = (nextType) => {
    setPubType(nextType);
    if (nextType !== 'carousel' && selectedIds.length > 1) {
      setSelectedIds([selectedIds[0]]);
    }
  };

  const previewId = selectedIds[0];
  const previewSrc = previewId
    ? `/api/pool/${previewId}/preview?pub_type=${pubType}&fit_mode=${fitMode}`
    : null;

  const fullCaptionPreview = [caption.trim(), hashtags.trim()].filter(Boolean).join('\n\n');
  const previewHandle = currentUser?.username || 'account';

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (selectedIds.length === 0) {
      notify(t('composer.errSelectImage'), 'error');
      return;
    }
    if (pubType === 'carousel' && selectedIds.length < 2) {
      notify(t('composer.errCarouselMin'), 'error');
      return;
    }
    if (scheduleMode === 'schedule' && !scheduledAt) {
      notify(t('composer.errScheduleDate'), 'error');
      return;
    }

    setSubmitting(true);
    try {
      let res;
      if (isEditing) {
        const updatePayload = {
          pub_type: pubType,
          image_ids: selectedIds,
          caption: pubType === 'story' ? '' : caption,
          hashtags: pubType === 'story' ? [] : hashtags ? [hashtags] : [],
          fit_mode: fitMode,
          adapter,
          scheduled_at: scheduleMode === 'schedule' ? new Date(scheduledAt).toISOString() : null,
          clear_schedule: scheduleMode !== 'schedule',
          status: scheduleMode === 'schedule' ? 'scheduled' : 'draft',
          publish_now: scheduleMode === 'now',
        };
        res = await api.updatePublication(editingPublication.id, updatePayload);
      } else {
        const createPayload = {
          pub_type: pubType,
          image_ids: selectedIds,
          caption: pubType === 'story' ? '' : caption,
          hashtags: pubType === 'story' ? [] : hashtags ? [hashtags] : [],
          fit_mode: fitMode,
          adapter,
          scheduled_at: scheduleMode === 'schedule' ? new Date(scheduledAt).toISOString() : null,
          publish_now: scheduleMode === 'now',
          created_by: 'web-ui',
        };
        res = await api.createPublication(createPayload);
      }

      if (res.status === 'failed') {
        notify(t('composer.errPublishFailed', { error: res.error_message }), 'error');
      } else {
        let msg;
        if (scheduleMode === 'now') {
          msg = t('composer.toastPublished', { adapter: res.adapter, id: res.external_id || res.id });
        } else if (scheduleMode === 'schedule') {
          const formattedDate = new Date(res.scheduled_at).toLocaleString(locale);
          msg = isEditing
            ? t('composer.toastUpdatedScheduled', { date: formattedDate })
            : t('composer.toastCreatedScheduled', { date: formattedDate });
        } else {
          msg = isEditing
            ? t('composer.toastDraftUpdated', { id: res.id })
            : t('composer.toastDraftCreated', { id: res.id });
        }
        await onCreated(msg);
      }
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const selectablePool = pool.filter((img) => img.status !== 'archived' || selectedIds.includes(img.id));

  const submitLabel = submitting
    ? t('composer.processing')
    : scheduleMode === 'now'
    ? isEditing
      ? pubType === 'story'
        ? t('composer.submit.editNowStory')
        : t('composer.submit.editNowPost')
      : pubType === 'story'
      ? t('composer.submit.nowStory')
      : t('composer.submit.nowPost')
    : scheduleMode === 'schedule'
    ? isEditing
      ? t('composer.submit.editSchedule')
      : t('composer.submit.schedule')
    : isEditing
    ? t('composer.submit.editDraft')
    : t('composer.submit.draft');

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="page-breadcrumb">
            {t('composer.breadcrumbRoot')} <span className="slash">//</span>{' '}
            {isEditing
              ? t('composer.breadcrumbEdit', { id: editingPublication.id })
              : t('composer.breadcrumbNew')}
          </div>
          <h2>
            {isEditing
              ? editingPublication.status === 'scheduled'
                ? t('composer.titleEditScheduled')
                : t('composer.titleEditDraft')
              : t('composer.titleNew')}
          </h2>
          <p>{isEditing ? t('composer.subEdit') : t('composer.subNew')}</p>
        </div>
        {isEditing && (
          <button type="button" className="btn" onClick={onCancelEdit}>
            <X size={14} strokeWidth={1.5} /> {t('composer.cancelEdit')}
          </button>
        )}
      </div>

      <div className="composer-layout">
        <SchematicCard
          as="form"
          onSubmit={handleSubmit}
          label={t('composer.specLabel')}
          right={<span className="badge">{pubType.toUpperCase()}</span>}
          bodyClassName="stack"
        >
          <div className="field">
            <label>
              {t('composer.format')}
              <span className="req">*</span>
            </label>
            <div className="row">
              {[
                { id: 'post', label: t('composer.format.post') },
                { id: 'carousel', label: t('composer.format.carousel') },
                { id: 'story', label: t('composer.format.story') },
              ].map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`btn btn-sm ${pubType === opt.id ? 'btn-ink' : ''}`}
                  onClick={() => switchType(opt.id)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <hr className="dashed-divider" />

          <div className="field">
            <label>
              {t('composer.selectPool', {
                count: selectedIds.length,
                max: pubType === 'carousel' ? '10' : '1',
              })}
              <span className="req">*</span>
            </label>
            {selectablePool.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--muted)', padding: '12px 0' }}>
                {t('composer.emptyPool')}
              </div>
            ) : (
              <div className="mini-pool">
                {selectablePool.map((img) => {
                  const idx = selectedIds.indexOf(img.id);
                  const isSel = idx >= 0;
                  return (
                    <div
                      key={img.id}
                      className={`mini-tile ${isSel ? 'selected' : ''}`}
                      onClick={() => toggleImage(img.id)}
                      title={`${img.original_filename} (${img.width}x${img.height})`}
                    >
                      <img src={img.thumbnail_url} alt={img.original_filename} />
                      {isSel && <span className="mini-tile-order">{idx + 1}</span>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="row">
            <div className="field" style={{ flex: 1, minWidth: 180 }}>
              <label>{t('composer.fitMode')}</label>
              <select className="select" value={fitMode} onChange={(e) => setFitMode(e.target.value)}>
                <option value="auto">{t('composer.fit.auto')}</option>
                <option value="pad_blur">{t('composer.fit.pad_blur')}</option>
                <option value="crop">{t('composer.fit.crop')}</option>
                <option value="pad_solid">{t('composer.fit.pad_solid')}</option>
              </select>
            </div>
            <div className="field" style={{ flex: 1, minWidth: 180 }}>
              <label>{t('composer.adapter')}</label>
              <select className="select" value={adapter} onChange={(e) => setAdapter(e.target.value)}>
                {adapters.map((a) => (
                  <option key={a.name} value={a.name}>
                    {a.label} ({a.name})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {pubType !== 'story' && (
            <>
              <hr className="dashed-divider" />
              <div className="field">
                <label>{t('composer.caption')}</label>
                <textarea
                  className="textarea"
                  placeholder={t('composer.captionPlaceholder')}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                />
              </div>
              <div className="field">
                <label>{t('composer.hashtags')}</label>
                <input
                  className="input"
                  placeholder={t('composer.hashtagsPlaceholder')}
                  value={hashtags}
                  onChange={(e) => setHashtags(e.target.value)}
                />
              </div>
            </>
          )}

          <hr className="dashed-divider" />

          <div className="field">
            <label>{t('composer.action')}</label>
            <div className="row">
              <button
                type="button"
                className={`btn btn-sm ${scheduleMode === 'draft' ? 'btn-ink' : ''}`}
                onClick={() => setScheduleMode('draft')}
              >
                <FileText size={13} strokeWidth={1.5} /> {t('composer.mode.draft')}
              </button>
              <button
                type="button"
                className={`btn btn-sm ${scheduleMode === 'schedule' ? 'btn-ink' : ''}`}
                onClick={() => setScheduleMode('schedule')}
              >
                <CalendarClock size={13} strokeWidth={1.5} /> {t('composer.mode.schedule')}
              </button>
              <button
                type="button"
                className={`btn btn-sm ${scheduleMode === 'now' ? 'btn-ink' : ''}`}
                onClick={() => setScheduleMode('now')}
              >
                <Send size={13} strokeWidth={1.5} /> {t('composer.mode.now')}
              </button>
            </div>
            {scheduleMode === 'schedule' && (
              <input
                type="datetime-local"
                className="input"
                style={{ marginTop: 8, maxWidth: 260 }}
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
              />
            )}
          </div>

          <div className="row" style={{ justifyContent: 'flex-end', paddingTop: 6 }}>
            {isEditing && (
              <button type="button" className="btn" onClick={onCancelEdit}>
                {t('common.cancel')}
              </button>
            )}
            <button className="btn btn-primary" type="submit" disabled={submitting || selectedIds.length === 0}>
              <Save size={14} strokeWidth={1.5} /> {submitLabel}
            </button>
          </div>
        </SchematicCard>

        <div className="preview-sticky">
          <SchematicCard
            label={t('composer.previewLabel', { type: pubType.toUpperCase(), fit: fitMode.toUpperCase() })}
            noPadding
          >
            <div className="preview-stage">
              {pubType === 'story' ? (
                <div className="ig-phone-story">
                  <div className="ig-story-bars" />
                  <div className="ig-story-header">
                    <span className="ig-avatar" style={{ width: 20, height: 20 }}>
                      {previewHandle.slice(0, 1).toUpperCase()}
                    </span>
                    <span>@{previewHandle}</span>
                  </div>
                  {previewSrc ? (
                    <img src={previewSrc} alt="Preview story" />
                  ) : (
                    <div
                      style={{
                        height: '100%',
                        display: 'grid',
                        placeItems: 'center',
                        color: 'var(--muted)',
                        fontSize: 11.5,
                        textTransform: 'uppercase',
                        letterSpacing: '0.1em',
                      }}
                    >
                      {t('composer.previewPick')}
                    </div>
                  )}
                </div>
              ) : (
                <div className="ig-phone-post">
                  <div className="ig-post-header">
                    <span className="ig-avatar">{previewHandle.slice(0, 1).toUpperCase()}</span>
                    <span>@{previewHandle}</span>
                    {pubType === 'carousel' && selectedIds.length > 1 && (
                      <span className="badge" style={{ marginLeft: 'auto' }}>
                        1 // {selectedIds.length}
                      </span>
                    )}
                  </div>
                  <div className="ig-post-media">
                    {previewSrc ? (
                      <img src={previewSrc} alt="Preview post" />
                    ) : (
                      <span
                        style={{
                          color: 'var(--muted)',
                          fontSize: 11,
                          textTransform: 'uppercase',
                          letterSpacing: '0.1em',
                        }}
                      >
                        {t('composer.previewPickPool')}
                      </span>
                    )}
                  </div>
                  <div className="ig-post-caption">
                    <strong>@{previewHandle}</strong>{' '}
                    {fullCaptionPreview || (
                      <span style={{ color: 'var(--muted)' }}>{t('composer.previewCaptionEmpty')}</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          </SchematicCard>
        </div>
      </div>
    </div>
  );
}
