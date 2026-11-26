const BASE = '';

let onUnauthorizedCallback = null;

export function setUnauthorizedHandler(fn) {
  onUnauthorizedCallback = fn;
}

async function request(path, options = {}) {
  const { skipUnauthorizedHandler = false, ...fetchOptions } = options;
  const headers = {
    'X-Actor': 'web-ui',
    ...(fetchOptions.headers || {}),
  };
  if (fetchOptions.body && !(fetchOptions.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const resp = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    ...fetchOptions,
    headers,
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      // ignore
    }
    if (resp.status === 401 && !skipUnauthorizedHandler && onUnauthorizedCallback) {
      onUnauthorizedCallback();
    }
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

export const api = {
  // Auth & Users
  authStatus: () => request('/api/auth/status', { skipUnauthorizedHandler: true }),
  authSetup: (username, password) =>
    request('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      skipUnauthorizedHandler: true,
    }),
  authLogin: (username, password) =>
    request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      skipUnauthorizedHandler: true,
    }),
  authLogout: () =>
    request('/api/auth/logout', {
      method: 'POST',
      skipUnauthorizedHandler: true,
    }),
  changePassword: (current_password, new_password) =>
    request('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),
  listUsers: () => request('/api/users'),
  createUser: (username, password) =>
    request('/api/users', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  resetUserPassword: (userId, password) =>
    request(`/api/users/${userId}/password`, {
      method: 'PATCH',
      body: JSON.stringify({ password }),
    }),
  deleteUser: (userId) => request(`/api/users/${userId}`, { method: 'DELETE' }),
  listApiKeys: () => request('/api/api-keys'),
  createApiKey: (name) =>
    request('/api/api-keys', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  revokeApiKey: (keyId) => request(`/api/api-keys/${keyId}`, { method: 'DELETE' }),

  // Core app endpoints
  health: () => request('/api/health', { skipUnauthorizedHandler: true }),
  stats: () => request('/api/stats'),
  activity: (limit = 40) => request(`/api/activity?limit=${limit}`),
  agentSchema: () => request('/api/agent/schema'),

  listPool: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request(`/api/pool${q ? `?${q}` : ''}`);
  },
  uploadPool: (files, tags = '', notes = '') => {
    const form = new FormData();
    Array.from(files).forEach((f) => form.append('files', f));
    form.append('tags', tags);
    form.append('notes', notes);
    return request('/api/pool/upload', { method: 'POST', body: form });
  },
  syncFolder: (folder_path, tags = [], recursive = true) =>
    request('/api/pool/sync', {
      method: 'POST',
      body: JSON.stringify({ folder_path, tags, recursive }),
    }),
  updatePoolImage: (id, payload) =>
    request(`/api/pool/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deletePoolImage: (id) => request(`/api/pool/${id}`, { method: 'DELETE' }),

  listPublications: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request(`/api/publications${q ? `?${q}` : ''}`);
  },
  createPublication: (payload) =>
    request('/api/publications', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updatePublication: (id, payload) =>
    request(`/api/publications/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  publishNow: (id, adapter = null) => {
    const q = adapter ? `?adapter=${encodeURIComponent(adapter)}` : '';
    return request(`/api/publications/${id}/publish${q}`, { method: 'POST' });
  },
  deletePublication: (id) => request(`/api/publications/${id}`, { method: 'DELETE' }),
  runDueScheduled: () => request('/api/scheduler/run-due', { method: 'POST' }),

  listAdapters: (verifyRemote = false) =>
    request(`/api/adapters?verify_remote=${verifyRemote ? 'true' : 'false'}`),
  checkAdapter: (name, verifyRemote = true) =>
    request(`/api/adapters/${name}/check?verify_remote=${verifyRemote ? 'true' : 'false'}`, {
      method: 'POST',
    }),
  getSettings: () => request('/api/settings'),
  updateSettings: (payload) =>
    request('/api/settings', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  // Insights & analytics
  getInsights: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request(`/api/insights${q ? `?${q}` : ''}`);
  },
  syncInsights: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request(`/api/insights/sync${q ? `?${q}` : ''}`, { method: 'POST' });
  },
  getMediaInsight: (identifier) => request(`/api/insights/media/${encodeURIComponent(identifier)}`),
  getMediaInsightHistory: (identifier, limit = 50) =>
    request(`/api/insights/media/${encodeURIComponent(identifier)}/history?limit=${limit}`),
  syncMediaInsight: (identifier) =>
    request(`/api/insights/media/${encodeURIComponent(identifier)}/sync`, { method: 'POST' }),
  syncPublicationInsights: (pubId) =>
    request(`/api/publications/${encodeURIComponent(pubId)}/insights/sync`, { method: 'POST' }),
};
