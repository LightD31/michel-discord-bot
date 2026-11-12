// Michel dashboard SPA — served as /static/app.js (no inline script: see the CSP).
    // ── State ─────────────────────────────────────────
    const state = {
        user: null,
        currentPage: 'home',
        currentServerId: null,
        currentModule: null,
        config: null,
        servers: null,
        botInfo: null,
        moduleSchemas: {},
        globalSchemas: {},
        extensions: [],
        sidebarOpen: false,
        // Log viewer state
        logs: [],
        logLevel: '',
        logSearch: '',
        logAutoScroll: true,
        logStreaming: false,
        logVerbose: false,
        // XP module stats: selected window (days)
        xpStatsDays: 30,
        // Set on /public/{token}/{kind}: { token, kind, meta, error }.
        // The page then renders the read-only public dashboard, no login.
        publicView: null,
        // Short-link manager (Shlink) state
        shlink: {
            status: null,
            items: [],
            pagination: {},
            page: 1,
            search: '',
            editing: null,
            loading: false,
        },
    };

    let modalSaveCallback = null;
    let logEventSource = null;

    // ── API helpers ───────────────────────────────────
    async function api(url, options = {}) {
        try {
            const resp = await fetch(url, {
                headers: { 'Content-Type': 'application/json', ...options.headers },
                ...options,
            });
            if (resp.status === 401) {
                state.user = null;
                render();
                return null;
            }
            if (resp.status === 403) {
                // Silently return null for admin-only endpoints when user is not admin
                return null;
            }
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ detail: 'Erreur inconnue' }));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            return await resp.json();
        } catch (e) {
            toast(e.message, 'error');
            throw e;
        }
    }

    // ── Toast system ──────────────────────────────────
    function toast(message, type = 'info') {
        const container = document.getElementById('toasts');
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }

    // ── Modal ─────────────────────────────────────────
    function openModal(title, json, onSave) {
        document.getElementById('modal-title').textContent = title;
        document.getElementById('json-editor').value = JSON.stringify(json, null, 4);
        document.getElementById('json-error').textContent = '';
        modalSaveCallback = onSave;
        document.getElementById('modal-overlay').classList.add('active');
    }

    function closeModal() {
        document.getElementById('modal-overlay').classList.remove('active');
        document.getElementById('modal-save-btn').style.display = '';
        modalSaveCallback = null;
        const body = document.getElementById('modal-overlay').querySelector('.modal-body');
        body.innerHTML = `
            <textarea class="json-editor" id="json-editor" spellcheck="false"></textarea>
            <div class="json-error" id="json-error"></div>
        `;
    }

    async function saveModal() {
        const editor = document.getElementById('json-editor');
        const errorEl = document.getElementById('json-error');

        if (!editor) {
            try {
                if (modalSaveCallback) {
                    await modalSaveCallback();
                }
                closeModal();
                toast('Configuration sauvegardée !', 'success');
            } catch (e) {
                if (errorEl) errorEl.textContent = e.message;
                toast('Erreur lors de la sauvegarde', 'error');
            }
            return;
        }

        try {
            const parsed = JSON.parse(editor.value);
            if (errorEl) errorEl.textContent = '';
            if (modalSaveCallback) {
                await modalSaveCallback(parsed);
            }
            closeModal();
            toast('Configuration sauvegardée !', 'success');
        } catch (e) {
            if (e instanceof SyntaxError) {
                if (errorEl) errorEl.textContent = `JSON invalide : ${e.message}`;
            } else {
                if (errorEl) errorEl.textContent = e.message;
                toast('Erreur lors de la sauvegarde', 'error');
            }
        }
    }

    // ── Sidebar toggle (mobile) ───────────────────────
    function toggleSidebar() {
        state.sidebarOpen = !state.sidebarOpen;
        const sidebar = document.querySelector('.sidebar');
        const overlay = document.querySelector('.sidebar-overlay');
        if (sidebar) sidebar.classList.toggle('open', state.sidebarOpen);
        if (overlay) overlay.classList.toggle('active', state.sidebarOpen);
    }

    function closeSidebar() {
        state.sidebarOpen = false;
        const sidebar = document.querySelector('.sidebar');
        const overlay = document.querySelector('.sidebar-overlay');
        if (sidebar) sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('active');
    }

    // ── Navigation ────────────────────────────────────
    function navigate(page, serverId = null, moduleName = null) {
        // Restrict admin-only pages for non-admin users
        const adminPages = ['server', 'module'];
        const developerPages = ['global', 'extensions', 'logs', 'links'];
        if (adminPages.includes(page) && !isAdmin()) {
            page = 'home';
            serverId = null;
        }
        if (developerPages.includes(page) && !isDeveloper()) {
            page = 'home';
            serverId = null;
            moduleName = null;
        }
        state.currentPage = page;
        state.currentServerId = serverId;
        state.currentModule = moduleName;
        closeSidebar();

        if (page === 'logs') {
            startLogStream();
        } else {
            stopLogStream();
        }

        if (page === 'extensions') {
            loadExtensions().then(() => render());
        }

        if (page === 'global') {
            loadSpotifyAuth().then(() => render());
        }

        if (page === 'links') {
            state.shlink.editing = null;
            loadShortLinks().then(() => render());
        }

        render();
    }

    // ── Log Streaming ─────────────────────────────────
    async function loadLogs() {
        try {
            const params = new URLSearchParams();
            params.set('count', '500');
            if (state.logLevel) params.set('level', state.logLevel);
            if (state.logSearch) params.set('search', state.logSearch);
            const data = await api(`/api/logs?${params}`);
            state.logs = data?.logs || [];
        } catch (e) {
            state.logs = [];
        }
    }

    function startLogStream() {
        if (logEventSource) return;
        state.logStreaming = true;

        logEventSource = new EventSource('/api/logs/stream');

        logEventSource.addEventListener('log', (event) => {
            try {
                const entry = JSON.parse(event.data);
                state.logs.push(entry);
                if (state.logs.length > 2000) {
                    state.logs = state.logs.slice(-1500);
                }
                appendLogLine(entry);
            } catch (e) { /* ignore */ }
        });

        logEventSource.addEventListener('ping', () => { /* keepalive */ });

        logEventSource.onerror = () => {
            state.logStreaming = false;
            logEventSource?.close();
            logEventSource = null;
            setTimeout(() => {
                if (state.currentPage === 'logs') {
                    startLogStream();
                }
            }, 5000);
        };
    }

    function stopLogStream() {
        if (logEventSource) {
            logEventSource.close();
            logEventSource = null;
        }
        state.logStreaming = false;
    }

    function appendLogLine(entry) {
        const container = document.getElementById('log-lines');
        if (!container) return;

        if (state.logLevel) {
            const levels = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50 };
            if ((levels[entry.level] || 0) < (levels[state.logLevel] || 0)) return;
        }
        if (state.logSearch) {
            const s = state.logSearch.toLowerCase();
            if (!entry.message.toLowerCase().includes(s) && !entry.logger.toLowerCase().includes(s)) return;
        }

        const line = document.createElement('div');
        line.className = 'log-line';
        line.innerHTML = formatLogLine(entry);
        container.appendChild(line);

        const countEl = document.getElementById('log-count');
        if (countEl) countEl.textContent = container.children.length;

        if (state.logAutoScroll) {
            const logContainer = document.getElementById('log-container');
            if (logContainer) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        }
    }

    function formatLogLine(entry) {
        const time = new Date(entry.timestamp * 1000).toLocaleTimeString('fr-FR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        let msg = escapeHtml(entry.message);
        if (state.logSearch) {
            const regex = new RegExp(`(${escapeRegExp(state.logSearch)})`, 'gi');
            msg = msg.replace(regex, '<span class="log-search-highlight">$1</span>');
        }
        const parts = [
            `<span class="log-time">${time}</span>`,
            `<span class="log-level ${entry.level}">${entry.level.padEnd(8)}</span>`,
        ];
        if (state.logVerbose) {
            parts.push(`<span class="log-logger" title="${escapeHtml(entry.logger)}">${escapeHtml(entry.logger)}</span>`);
            const loc = entry.filename ? `${entry.filename}:${entry.lineno}` : `:${entry.lineno}`;
            parts.push(`<span class="log-lineno" title="${escapeHtml(loc)}">${escapeHtml(loc)}</span>`);
        }
        parts.push(`<span class="log-msg">${msg}</span>`);
        return parts.join('');
    }

    function onLogVerboseChange(checked) {
        state.logVerbose = checked;
        renderLogLines();
    }

    function escapeRegExp(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function handleLogScroll() {
        const el = document.getElementById('log-container');
        if (!el) return;
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
        state.logAutoScroll = atBottom;
        const btn = document.getElementById('autoscroll-btn');
        if (btn) btn.classList.toggle('visible', !atBottom);
    }

    function scrollToBottom() {
        const el = document.getElementById('log-container');
        if (el) {
            el.scrollTop = el.scrollHeight;
            state.logAutoScroll = true;
        }
        const btn = document.getElementById('autoscroll-btn');
        if (btn) btn.classList.remove('visible');
    }

    function refreshLogs() {
        loadLogs().then(() => renderLogLines());
    }

    function renderLogLines() {
        const container = document.getElementById('log-lines');
        if (!container) return;

        let filtered = state.logs;
        if (state.logLevel) {
            const levels = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50 };
            const minLevel = levels[state.logLevel] || 0;
            filtered = filtered.filter(e => (levels[e.level] || 0) >= minLevel);
        }
        if (state.logSearch) {
            const s = state.logSearch.toLowerCase();
            filtered = filtered.filter(e =>
                e.message.toLowerCase().includes(s) || e.logger.toLowerCase().includes(s)
            );
        }

        container.innerHTML = filtered.map(e =>
            `<div class="log-line">${formatLogLine(e)}</div>`
        ).join('');

        const countEl = document.getElementById('log-count');
        if (countEl) countEl.textContent = filtered.length;

        if (state.logAutoScroll) {
            const logC = document.getElementById('log-container');
            if (logC) logC.scrollTop = logC.scrollHeight;
        }
    }

    function onLogLevelChange(val) {
        state.logLevel = val;
        renderLogLines();
    }

    let logSearchTimeout = null;
    function onLogSearchInput(val) {
        clearTimeout(logSearchTimeout);
        logSearchTimeout = setTimeout(() => {
            state.logSearch = val;
            renderLogLines();
        }, 250);
    }

    function clearLogs() {
        state.logs = [];
        renderLogLines();
    }

    // ── Data loading ──────────────────────────────────
    async function loadUserInfo() {
        const data = await api('/api/me');
        if (data && data.authenticated) {
            state.user = data;
        } else {
            state.user = null;
        }
    }

    function isAdmin() {
        return state.user && state.user.is_admin;
    }

    function isDeveloper() {
        return state.user && state.user.is_developer;
    }

    async function loadBotInfo() {
        try {
            state.botInfo = await api('/api/bot-info');
        } catch (e) {
            state.botInfo = { status: 'error' };
        }
    }

    async function loadExtensions() {
        try {
            const data = await api('/api/extensions');
            state.extensions = data.extensions || [];
        } catch (e) {
            state.extensions = [];
        }
    }

    async function loadServers() {
        try {
            state.servers = await api('/api/servers');
        } catch (e) {
            state.servers = {};
        }
    }

    async function loadSchemas() {
        try {
            const [modSchemas, globalSchemas] = await Promise.all([
                api('/api/schemas/modules'),
                api('/api/schemas/global'),
            ]);
            state.moduleSchemas = modSchemas || {};
            state.globalSchemas = globalSchemas || {};
        } catch (e) {
            state.moduleSchemas = {};
            state.globalSchemas = {};
        }
    }

    async function loadConfig() {
        try {
            state.config = await api('/api/config');
        } catch (e) {
            state.config = {};
        }
    }

    async function loadSpotifyAuth() {
        try {
            state.spotifyAuth = await api('/api/spotify/status');
        } catch (e) {
            state.spotifyAuth = { error: String(e?.message || e) };
        }
    }

    function renderSpotifyAuthPanel() {
        const s = state.spotifyAuth || {};
        let badge, body;
        if (s.error) {
            badge = `<span style="color:var(--danger)">⚠ Erreur</span>`;
            body = `<div style="color:var(--text-muted);font-size:0.85rem">Impossible de lire l'état : ${escapeHtml(s.error)}</div>`;
        } else if (!s.configured) {
            badge = `<span style="color:var(--warning)">○ Non configuré</span>`;
            body = `<div style="color:var(--text-muted);font-size:0.85rem">Renseigne Client ID et Client Secret ci-dessus, puis recharge la page.</div>`;
        } else if (!s.authorized) {
            badge = `<span style="color:var(--warning)">○ Non autorisé</span>`;
            body = `<div style="color:var(--text-muted);font-size:0.85rem">Aucun token en cache. Clique sur « Se connecter » pour démarrer l'autorisation OAuth.</div>`;
        } else {
            const acct = s.account || {};
            const exp = s.expires_at ? new Date(s.expires_at * 1000) : null;
            const stale = s.is_expired;
            badge = stale
                ? `<span style="color:var(--warning)">⏳ Expiré (refresh à la prochaine requête)</span>`
                : `<span style="color:var(--success)">● Connecté</span>`;
            body = `
                <div style="font-size:0.9rem;line-height:1.6">
                    <div><strong>Compte :</strong> ${escapeHtml(acct.display_name || '?')} <code>${escapeHtml(acct.id || '?')}</code></div>
                    ${exp ? `<div><strong>Token valide jusqu'à :</strong> ${exp.toLocaleString()}</div>` : ''}
                    ${s.scope ? `<div style="color:var(--text-muted);font-size:0.8rem"><strong>Scopes :</strong> ${escapeHtml(s.scope)}</div>` : ''}
                </div>
            `;
        }

        const btnLabel = s.authorized ? '🔄 Re-authoriser' : '🔑 Se connecter';
        const btnClass = s.authorized ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm';
        const cbUrl = s.callback_url || '';
        const cbHint = cbUrl ? `
            <div style="margin-top:8px;padding:8px;background:var(--bg-primary);border-radius:6px;font-size:0.8rem;color:var(--text-muted)">
                Redirect URI à enregistrer dans le dashboard Spotify :
                <code style="user-select:all">${escapeHtml(cbUrl)}</code>
            </div>` : '';

        return `
            <div style="margin-top:12px;padding:12px;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <div><strong>OAuth Spotify</strong> &nbsp; ${badge}</div>
                    <button class="${btnClass}" data-click="startSpotifyAuth">${btnLabel}</button>
                </div>
                ${body}
                ${cbHint}
            </div>
        `;
    }

    function startSpotifyAuth() {
        window.location.href = '/spotify/auth/start';
    }

    // ── Schema-based form rendering ───────────────────

    function renderSchemaForm(formId, schema, values, options = {}) {
        const fields = schema.fields || {};
        const entries = Object.entries(fields);
        if (entries.length === 0) return '<p style="color:var(--text-muted)">Aucun champ défini.</p>';

        let html = `<div class="schema-form" data-form-id="${formId}">`;
        for (const [key, field] of entries) {
            if (options.excludeEnabled && key === 'enabled') continue;

            const value = values[key];
            const hasValue = value !== undefined && value !== null;
            const fieldType = field.type || 'string';
            const isSecret = field.secret || false;
            const isRequired = field.required || false;

            if (field.hidden) {
                if (hasValue) {
                    html += `<input type="hidden" data-field="${key}" data-type="${fieldType}" value="${escapeHtml(String(value))}">`;
                }
                continue;
            }

            html += `<div class="form-field" data-key="${key}">`;
            html += `<div class="form-field-header">`;
            html += `<label class="form-label">${field.label || key}`;
            if (isRequired) html += ' <span class="required-badge">requis</span>';
            html += `</label>`;
            if (RESETTABLE_TYPES.has(fieldType) && field.default !== undefined && field.default !== null) {
                html += `<button type="button" class="btn-reset-default" data-click="resetFieldToDefault" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]"
                    data-default="${escapeHtml(JSON.stringify(field.default))}"
                    title="Rétablir la valeur par défaut (à sauvegarder ensuite)">↺ Par défaut</button>`;
            }
            html += `</div>`;
            if (field.description) {
                html += `<div class="form-description">${field.description}</div>`;
            }

            switch (fieldType) {
                case 'boolean':
                    html += `
                        <label class="toggle" style="margin-top:4px;">
                            <input type="checkbox" data-field="${key}" data-type="boolean"
                                ${value === true ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>`;
                    break;

                case 'number':
                    html += `<input type="number" class="form-input" data-field="${key}" data-type="number"
                        value="${hasValue ? value : (field.default ?? '')}"
                        ${field.step != null ? `step="${field.step}"` : ''}
                        placeholder="${field.default != null ? 'Défaut: ' + field.default : ''}">`;
                    break;

                case 'secret':
                    html += `<div style="position:relative;">
                        <input type="password" class="form-input" data-field="${key}" data-type="secret"
                            value="${hasValue ? value : ''}"
                            placeholder="${hasValue ? '••••••••' : 'Non défini'}">
                        <button class="btn-reveal" data-click="togglePasswordVisibility" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" type="button" title="Afficher/masquer">👁</button>
                    </div>`;
                    break;

                case 'channel': {
                    const currentVal = hasValue ? String(value) : '';
                    html += `<div class="channel-picker" data-field="${key}" data-type="channel" data-current="${escapeHtml(currentVal)}">
                        <select class="form-input channel-picker-select" data-channel-select="${key}">
                            <option value="">— Chargement des salons… —</option>
                            ${currentVal ? `<option value="${escapeHtml(currentVal)}" selected>${escapeHtml(currentVal)}</option>` : ''}
                        </select>
                        <input type="text" class="form-input channel-picker-manual" data-channel-manual="${key}"
                            value="${escapeHtml(currentVal)}" placeholder="ID du salon" style="display:none;">
                        <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle">✎</button>
                    </div>`;
                    break;
                }
                case 'role':
                case 'message':
                case 'url': {
                    const typeHint = {role: 'ID du rôle', message: 'ID du message', url: 'URL'}[fieldType];
                    html += `<input type="text" class="form-input" data-field="${key}" data-type="${fieldType}"
                        value="${hasValue ? escapeHtml(String(value)) : ''}"
                        placeholder="${typeHint}">`;
                    break;
                }

                case 'list':
                    const listVal = Array.isArray(value) ? value.join('\n') : (hasValue ? String(value) : '');
                    const defaultHint = Array.isArray(field.default) ? field.default.join(', ') : '';
                    html += `<textarea class="form-textarea" data-field="${key}" data-type="list"
                        placeholder="${defaultHint ? 'Défaut: ' + defaultHint : 'Une valeur par ligne'}"
                        rows="3">${escapeHtml(listVal)}</textarea>`;
                    break;

                case 'list:number':
                    const numListVal = Array.isArray(value) ? value.join(', ') : (hasValue ? String(value) : '');
                    html += `<input type="text" class="form-input" data-field="${key}" data-type="list:number"
                        value="${escapeHtml(numListVal)}"
                        placeholder="Nombres séparés par des virgules (ex: 1, 2, 3)">`;
                    break;

                case 'dict':
                    const jsonStr = hasValue ? JSON.stringify(value, null, 2) : '{}';
                    html += `<textarea class="form-textarea json-field" data-field="${key}" data-type="dict"
                        rows="5" spellcheck="false">${escapeHtml(jsonStr)}</textarea>`;
                    break;

                case 'models':
                    const models = Array.isArray(value) ? value : [];
                    html += `<div class="models-list" data-field="${key}" data-type="models">`;
                    html += `<div class="models-header"><span>Clé</span><span>API</span><span>Model ID</span><span>Nom d'affichage</span><span></span></div>`;
                    models.forEach((m, idx) => {
                        html += modelEntryHtml(m, idx);
                    });
                    html += `</div>`;
                    html += `<div class="models-actions">
                        <button type="button" class="btn-add-model" data-click="addModelEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un modèle</button>
                        <button type="button" class="btn-add-model" data-click="openModelCatalog" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">📚 Parcourir les modèles</button>
                    </div>`;
                    break;

                case 'text':
                    html += `<textarea class="form-textarea" data-field="${key}" data-type="text"
                        rows="8">${escapeHtml(hasValue ? String(value) : (field.default ?? ''))}</textarea>`;
                    break;

                case 'aimodel': {
                    const current = hasValue ? String(value) : '';
                    html += `<select class="form-input" data-field="${key}" data-type="aimodel" data-current="${escapeHtml(current)}">
                        ${current ? `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>` : '<option value="">— Chargement… —</option>'}
                    </select>`;
                    break;
                }

                case 'teams': {
                    const teams = Array.isArray(value) ? value : [];
                    html += `<div class="teams-list" data-field="${key}" data-type="teams">`;
                    teams.forEach((t, idx) => {
                        html += renderTeamEntry(t, idx);
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addTeamEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une équipe</button>`;
                    break;
                }

                case 'discord2name':
                    const nameMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                    html += `<div class="namemap-list" data-field="${key}" data-type="discord2name">`;
                    html += `<div class="namemap-header"><span>Membre Discord</span><span>Prénom</span><span></span></div>`;
                    Object.entries(nameMap).forEach(([discordId, name]) => {
                        html += `
                            <div class="namemap-entry">
                                ${memberPickerHtml(discordId, 'data-namemap-field="discord_id"')}
                                <input type="text" data-namemap-field="name" value="${escapeHtml(String(name))}" placeholder="ex: Jean">
                                <button class="btn-remove-entry" data-click="removeNameMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                            </div>`;
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addNameMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un membre</button>`;
                    break;

                case 'keyvaluemap':
                    const kvMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                    const keyLabel = field.keyLabel || 'Clé';
                    const valueLabel = field.valueLabel || 'Valeur';
                    html += `<div class="keyvaluemap-list" data-field="${key}" data-type="keyvaluemap" data-key-label="${escapeHtml(keyLabel)}" data-value-label="${escapeHtml(valueLabel)}">`;
                    html += `<div class="keyvaluemap-header"><span>${escapeHtml(keyLabel)}</span><span>${escapeHtml(valueLabel)}</span><span></span></div>`;
                    Object.entries(kvMap).forEach(([mapKey, mapValue]) => {
                        html += `
                            <div class="keyvaluemap-entry">
                                <input type="text" data-kvmap-field="key" value="${escapeHtml(mapKey)}" placeholder="ex: ${escapeHtml(keyLabel).toLowerCase()}">
                                <input type="text" data-kvmap-field="value" value="${escapeHtml(String(mapValue))}" placeholder="ex: ${escapeHtml(valueLabel).toLowerCase()}">
                                <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.keyvaluemap-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                            </div>`;
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addKeyValueMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une entrée</button>`;
                    break;

                case 'streamermap': {
                    const streamerMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                    html += `<div class="streamers-list" data-field="${key}" data-type="streamermap">`;
                    Object.entries(streamerMap).forEach(([login, cfg]) => {
                        html += renderStreamerEntry(login, cfg || {});
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addStreamerEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un streamer</button>`;
                    break;
                }

                case 'youtubechannelmap': {
                    const channelMap = (value && typeof value === 'object' && !Array.isArray(value)) ? value : {};
                    html += `<div class="youtubechannel-list" data-field="${key}" data-type="youtubechannelmap">`;
                    Object.entries(channelMap).forEach(([handle, cfg]) => {
                        html += renderYoutubeChannelEntry(handle, cfg || {});
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addYoutubeChannelEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une chaîne</button>`;
                    break;
                }

                case 'rssfeedmap': {
                    const feedMap = (typeof value === 'object' && value !== null && !Array.isArray(value)) ? value : {};
                    html += `<div class="rssfeed-list" data-field="${key}" data-type="rssfeedmap">`;
                    Object.entries(feedMap).forEach(([feedId, cfg]) => {
                        const normalized = (typeof cfg === 'string') ? { url: cfg } : (cfg || {});
                        html += renderRssFeedEntry(feedId, normalized);
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addRssFeedEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un flux</button>`;
                    html += `<select class="rssfeed-preset-select" data-change="applyRssPreset" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Préréglages prêts à l'emploi">
                        <option value="">+ Préréglage…</option>
                        <optgroup label="LootScraper — Bundles">
                            <option value="lootscraper-all">Bundle classique (Epic + Amazon + itch.io + Steam)</option>
                            <option value="lootscraper-all-pc">Bundle PC (Epic + GOG + Humble + itch.io + Amazon + Steam)</option>
                            <option value="lootscraper-everything">Tout LootScraper (12 flux)</option>
                        </optgroup>
                        <optgroup label="LootScraper — Jeux PC gratuits">
                            <option value="lootscraper-epic">Epic Games</option>
                            <option value="lootscraper-gog">GOG</option>
                            <option value="lootscraper-humble">Humble</option>
                            <option value="lootscraper-itch">itch.io</option>
                        </optgroup>
                        <optgroup label="LootScraper — Amazon &amp; Steam">
                            <option value="lootscraper-amazon">Amazon Prime Gaming (jeux)</option>
                            <option value="lootscraper-amazon-loot">Amazon Prime Gaming (loot)</option>
                            <option value="lootscraper-steam">Steam (jeux gratuits)</option>
                            <option value="lootscraper-steam-loot">Steam (loot)</option>
                        </optgroup>
                        <optgroup label="LootScraper — Mobile">
                            <option value="lootscraper-epic-android">Epic Games — Android</option>
                            <option value="lootscraper-epic-ios">Epic Games — iOS</option>
                            <option value="lootscraper-apple">App Store (iOS)</option>
                            <option value="lootscraper-google">Google Play (Android)</option>
                        </optgroup>
                    </select>`;
                    break;
                }

                case 'spotifymap':
                    const spotifyUsers = Array.isArray(value) ? value : [];
                    html += `<div class="spotifymap-list" data-field="${key}" data-type="spotifymap">`;
                    html += `<div class="spotifymap-header"><span>Spotify ID</span><span>Membre Discord</span><span></span></div>`;
                    spotifyUsers.forEach((user, idx) => {
                        html += `
                            <div class="spotifymap-entry" data-user-idx="${idx}">
                                <input type="text" data-spotifymap-field="spotifyId" value="${escapeHtml(user.spotifyId || '')}" placeholder="ex: exemple123">
                                ${memberPickerHtml(user.discordId, 'data-spotifymap-field="discordId"')}
                                <button class="btn-remove-entry" data-click="removeSpotifyMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                            </div>`;
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-entry" data-click="addSpotifyUserEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un utilisateur</button>`;
                    break;

                case 'messagelist':
                    const messages = Array.isArray(value) ? value : [];
                    html += `<div class="messagelist-list" data-field="${key}" data-type="messagelist">`;
                    html += `<div class="messagelist-header"><span>Message</span><span>Poids</span><span></span></div>`;
                    messages.forEach((msg) => {
                        const text = typeof msg === 'string' ? msg : String(msg?.text ?? '');
                        const w = (msg && typeof msg === 'object' && msg.weight !== undefined) ? msg.weight : 1;
                        html += `
                            <div class="messagelist-entry">
                                <input type="text" data-msg-field="message" value="${escapeHtml(text)}" placeholder="Message…">
                                <input type="number" data-msg-field="weight" value="${escapeHtml(String(w))}" min="0" placeholder="1">
                                <button class="btn-remove-msg" data-click="remove-closest" data-click-args="[&quot;.messagelist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                            </div>`;
                    });
                    html += `</div>`;
                    if (field.variables) {
                        const varChips = field.variables.split(',').map(v => {
                            const vt = v.trim();
                            return `<button type="button" class="var-chip" data-var="${escapeHtml(vt)}" data-mousedown="insertVariable" data-mousedown-args="[{&quot;$&quot;:&quot;event&quot;},{&quot;$&quot;:&quot;el&quot;}]">${escapeHtml(vt)}</button>`;
                        }).join('');
                        html += `<div class="messagelist-variables"><span>Variables : </span>${varChips}</div>`;
                    }
                    html += `<button type="button" class="btn-add-entry" data-click="addMessageEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un message</button>`;
                    break;

                case 'commandlist': {
                    const commands = Array.isArray(value) ? value : [];
                    html += `<div class="commandlist-list" data-field="${key}" data-type="commandlist">`;
                    commands.forEach((cmd) => {
                        const cmdName = typeof cmd?.name === 'string' ? cmd.name : '';
                        const cmdDesc = typeof cmd?.description === 'string' ? cmd.description : '';
                        const responses = Array.isArray(cmd?.responses) ? cmd.responses : [];
                        html += `<div class="commandlist-entry">`;
                        html += `<div class="commandlist-entry-header">`;
                        html += `<div class="commandlist-entry-title">/${escapeHtml(cmdName) || 'nouvelle-commande'}</div>`;
                        html += `<button type="button" class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.commandlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cette commande">✕</button>`;
                        html += `</div>`;
                        html += `<div class="commandlist-entry-fields">`;
                        html += `<div><label class="commandlist-field-label">Nom</label>`;
                        html += `<input type="text" class="form-input" data-cmd-field="name" value="${escapeHtml(cmdName)}" placeholder="blague" maxlength="32" data-input="updateCommandTitle" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]"></div>`;
                        html += `<div><label class="commandlist-field-label">Description</label>`;
                        html += `<input type="text" class="form-input" data-cmd-field="description" value="${escapeHtml(cmdDesc)}" placeholder="Raconte une blague au hasard" maxlength="100"></div>`;
                        html += `</div>`;
                        html += `<div class="commandlist-responses">`;
                        html += `<div class="commandlist-responses-label">Réponses (tirées au hasard)</div>`;
                        responses.forEach((response) => {
                            const text = typeof response === 'string' ? response : String(response?.text ?? '');
                            html += `
                                <div class="commandlist-response-entry">
                                    <input type="text" data-response-field="text" value="${escapeHtml(text)}" placeholder="https://exemple.com/gif.gif ou un texte" maxlength="2000">
                                    <button type="button" class="btn-remove-response" data-click="remove-closest" data-click-args="[&quot;.commandlist-response-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                                </div>`;
                        });
                        html += `</div>`;
                        html += `<button type="button" class="btn-add-link" data-click="addCommandResponse" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une réponse</button>`;
                        html += `</div>`;
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-embed" data-click="addCommandEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une commande</button>`;
                    break;
                }

                case 'embedlist':
                    const embeds = Array.isArray(value) ? value : [];
                    html += `<div class="embedlist-list" data-field="${key}" data-type="embedlist">`;
                    embeds.forEach((embed, embedIdx) => {
                        const embedTitle = embed.title || 'Sans titre';
                        // Free text in the stored config: anything but six hex digits falls
                        // back to the default rather than landing in the markup.
                        const rawColor = String(embed.color || '').replace(/^#/, '');
                        const embedColor = /^[0-9a-fA-F]{6}$/.test(rawColor) ? rawColor : '3498db';
                        const embedLinks = Array.isArray(embed.links) ? embed.links : [];

                        html += `<div class="embedlist-entry" data-embed-idx="${embedIdx}">`;

                        // Embed header
                        html += `<div class="embedlist-entry-header">`;
                        html += `<div class="embedlist-entry-title" title="${escapeHtml(embedTitle)}">${escapeHtml(embedTitle)}</div>`;
                        html += `<div class="embedlist-entry-controls">`;
                        html += `<button class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.embedlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cet embed">✕</button>`;
                        html += `</div></div>`;

                        // Embed fields (title & color)
                        html += `<div class="embedlist-entry-fields">`;
                        html += `<div><label style="font-size:11px;color:var(--text-muted)">Titre</label>`;
                        html += `<input type="text" class="form-input" data-embed-field="title" value="${escapeHtml(embedTitle)}" placeholder="Titre de l'embed" data-bind="">`;
                        html += `</div>`;
                        html += `<div><label style="font-size:11px;color:var(--text-muted)">Couleur</label>`;
                        html += `<div class="embedlist-color-picker">`;
                        html += `<input type="color" class="embedlist-color-input" data-embed-field="color" value="#${embedColor}" data-change="updateEmbedColorText" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">`;
                        html += `<div class="embedlist-color-text">${embedColor}</div>`;
                        html += `</div></div></div>`;

                        // Links section
                        html += `<div class="embedlist-links">`;
                        html += `<div class="embedlist-links-label">Liens</div>`;
                        embedLinks.forEach((link, linkIdx) => {
                            const linkTitle = link.title || '';
                            const linkUrl = link.url || '';
                            html += `
                                <div class="embedlist-link-entry" data-link-idx="${linkIdx}">
                                    <input type="text" data-link-field="title" value="${escapeHtml(linkTitle)}" placeholder="Titre du lien" maxlength="256">
                                    <input type="text" data-link-field="url" value="${escapeHtml(linkUrl)}" placeholder="https://exemple.com" maxlength="2048">
                                    <button type="button" class="btn-remove-link" data-click="remove-closest" data-click-args="[&quot;.embedlist-link-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                                </div>`;
                        });
                        html += `</div>`;

                        html += `<button type="button" class="btn-add-link" data-click="addEmbedLink" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un lien</button>`;
                        html += `</div>`;
                    });
                    html += `</div>`;
                    html += `<button type="button" class="btn-add-embed" data-click="addEmbedEntry" data-click-args="${actionArgs(EL, key)}">+ Ajouter un embed</button>`;
                    break;

                default: // string
                    html += `<input type="text" class="form-input" data-field="${key}" data-type="string"
                        value="${hasValue ? escapeHtml(String(value)) : ''}"
                        placeholder="${field.default != null ? 'Défaut: ' + field.default : ''}">`;
            }

            html += `</div>`;
        }
        html += `</div>`;
        return html;
    }

    // Field types whose default can be restored with one click.
    const RESETTABLE_TYPES = new Set(['text', 'string', 'number', 'boolean', 'url']);

    function resetFieldToDefault(btn) {
        const field = btn.closest('.form-field');
        const input = field?.querySelector('[data-field]');
        if (!input) return;
        let value;
        try { value = JSON.parse(btn.dataset.default); } catch { return; }
        if (input.type === 'checkbox') {
            input.checked = value === true;
        } else {
            const current = input.value;
            const next = value == null ? '' : String(value);
            if (current === next) {
                toast('Déjà à la valeur par défaut', 'info');
                return;
            }
            // Long texts (the AI persona) are easy to lose: confirm before overwriting.
            if (input.tagName === 'TEXTAREA' && current.trim()
                && !confirm('Remplacer le texte actuel par la valeur par défaut ?')) {
                return;
            }
            input.value = next;
        }
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        toast('Valeur par défaut rétablie — pensez à sauvegarder', 'success');
    }

    function collectFormValues(formId) {
        const form = document.querySelector(`[data-form-id="${formId}"]`);
        if (!form) return {};
        const result = {};
        const inputs = form.querySelectorAll('[data-field]');
        for (const input of inputs) {
            const key = input.dataset.field;
            const type = input.dataset.type;
            let val;
            switch (type) {
                case 'boolean':
                    val = input.checked;
                    break;
                case 'channel': {
                    if (input.tagName === 'INPUT') {
                        val = (input.value || '').trim() || undefined;
                    } else {
                        const select = input.querySelector('.channel-picker-select');
                        const manual = input.querySelector('.channel-picker-manual');
                        const manualVisible = manual && manual.style.display !== 'none';
                        const raw = manualVisible ? (manual?.value ?? '') : (select?.value ?? '');
                        val = raw.trim() || undefined;
                    }
                    break;
                }
                case 'number':
                    val = input.value === '' ? undefined : Number(input.value);
                    break;
                case 'list':
                    val = input.value.trim() ? input.value.trim().split('\n').map(s => s.trim()).filter(Boolean) : undefined;
                    break;
                case 'list:number':
                    val = input.value.trim()
                        ? input.value.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n))
                        : undefined;
                    break;
                case 'dict':
                    try { val = JSON.parse(input.value); } catch { val = undefined; }
                    break;
                case 'models':
                    // Handled separately below
                    continue;
                case 'aimodel':
                    // Empty is meaningful (fall back to the default), so keep it.
                    val = input.value;
                    break;
                case 'teams':
                    // Handled separately below
                    continue;
                case 'discord2name':
                    // Handled separately below
                    continue;
                case 'keyvaluemap':
                    // Handled separately below
                    continue;
                case 'spotifymap':
                    // Handled separately below
                    continue;
                case 'streamermap':
                    // Handled separately below
                    continue;
                case 'youtubechannelmap':
                    // Handled separately below
                    continue;
                case 'rssfeedmap':
                    // Handled separately below
                    continue;
                case 'messagelist':
                    // Handled separately below
                    continue;
                case 'commandlist':
                    // Handled separately below
                    continue;
                case 'embedlist':
                    // Handled separately below
                    continue;
                case 'secret':
                    val = input.value || undefined;
                    break;
                default:
                    val = input.value || undefined;
            }
            if (val !== undefined) {
                result[key] = val;
            }
        }
        // Collect models lists
        const modelsLists = form.querySelectorAll('[data-type="models"]');
        for (const list of modelsLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.model-entry');
            const models = [];
            for (const entry of entries) {
                const provider = entry.querySelector('[data-model-field="provider"]')?.value?.trim();
                const apiName = entry.querySelector('[data-model-field="api"]')?.value || 'openrouter';
                const model_id = entry.querySelector('[data-model-field="model_id"]')?.value?.trim();
                const display_name = entry.querySelector('[data-model-field="display_name"]')?.value?.trim();
                if (provider && model_id) {
                    models.push({ provider, api: apiName, model_id, display_name: display_name || provider });
                }
            }
            if (models.length > 0) {
                result[key] = models;
            }
        }

        // Collect teams lists
        const teamsLists = form.querySelectorAll('[data-type="teams"]');
        for (const list of teamsLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.team-entry');
            const teams = [];
            for (const entry of entries) {
                const name = entry.querySelector('[data-team-field="name"]')?.value?.trim();
                const vlrTeamId = entry.querySelector('[data-team-field="vlrTeamId"]')?.value?.trim();
                const chanPicker = entry.querySelector('.channel-picker[data-team-field="channelId"]');
                let chan = '';
                if (chanPicker) {
                    const manual = chanPicker.querySelector('.channel-picker-manual');
                    const select = chanPicker.querySelector('.channel-picker-select');
                    const manualVisible = manual && manual.style.display !== 'none';
                    chan = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
                }
                const pinCb = entry.querySelector('input[type="checkbox"][data-team-field="pin"]');
                const pin = !!(pinCb && pinCb.checked);
                if (name) {
                    const team = { name };
                    if (vlrTeamId) team.vlrTeamId = vlrTeamId;
                    if (chan) team.channelId = chan;
                    if (pin) team.pin = true;
                    teams.push(team);
                }
            }
            result[key] = teams;
        }

        // Collect discord2name lists
        const namemapLists = form.querySelectorAll('[data-type="discord2name"]');
        for (const list of namemapLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.namemap-entry');
            const nameMap = {};
            for (const entry of entries) {
                const picker = entry.querySelector('.member-picker');
                let discordId = '';
                if (picker) {
                    const manual = picker.querySelector('.member-picker-manual');
                    const select = picker.querySelector('.member-picker-select');
                    const manualVisible = manual && manual.style.display !== 'none';
                    discordId = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
                } else {
                    discordId = entry.querySelector('[data-namemap-field="discord_id"]')?.value?.trim() || '';
                }
                const name = entry.querySelector('[data-namemap-field="name"]')?.value?.trim();
                if (discordId && name) {
                    nameMap[discordId] = name;
                }
            }
            result[key] = nameMap;
        }

        // Collect keyvaluemap lists
        const kvmapLists = form.querySelectorAll('[data-type="keyvaluemap"]');
        for (const list of kvmapLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.keyvaluemap-entry');
            const kvMap = {};
            for (const entry of entries) {
                const mapKey = entry.querySelector('[data-kvmap-field="key"]')?.value?.trim();
                const mapValue = entry.querySelector('[data-kvmap-field="value"]')?.value?.trim();
                if (mapKey && mapValue) {
                    kvMap[mapKey] = mapValue;
                }
            }
            result[key] = kvMap;
        }

        // Collect spotifymap lists
        const spotifymapLists = form.querySelectorAll('[data-type="spotifymap"]');
        for (const list of spotifymapLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.spotifymap-entry');
            const spotifyUsers = [];
            for (const entry of entries) {
                const spotifyId = entry.querySelector('[data-spotifymap-field="spotifyId"]')?.value?.trim();
                const picker = entry.querySelector('.member-picker');
                let discordId = '';
                if (picker) {
                    const manual = picker.querySelector('.member-picker-manual');
                    const select = picker.querySelector('.member-picker-select');
                    const manualVisible = manual && manual.style.display !== 'none';
                    discordId = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
                }
                if (spotifyId && discordId) {
                    spotifyUsers.push({ spotifyId, discordId });
                }
            }
            if (spotifyUsers.length > 0) {
                result[key] = spotifyUsers;
            }
        }

        // Collect youtubechannelmap lists
        const ytChannelLists = form.querySelectorAll('[data-type="youtubechannelmap"]');
        for (const list of ytChannelLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.youtubechannel-entry');
            const channelMap = {};
            for (const entry of entries) {
                const handle = entry.querySelector('[data-yt-field="handle"]')?.value?.trim();
                if (!handle) continue;
                const label = entry.querySelector('[data-yt-field="label"]')?.value?.trim() || '';
                const shorts = !!entry.querySelector('[data-yt-field="shorts"]')?.checked;
                const live = !!entry.querySelector('[data-yt-field="live"]')?.checked;
                const vod = !!entry.querySelector('[data-yt-field="vod"]')?.checked;
                const cfg = { shorts, live, vod };
                if (label) cfg.label = label;
                channelMap[handle] = cfg;
            }
            result[key] = channelMap;
        }

        // Collect rssfeedmap lists
        const rssFeedLists = form.querySelectorAll('[data-type="rssfeedmap"]');
        for (const list of rssFeedLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.rssfeed-entry');
            const feedMap = {};
            for (const entry of entries) {
                const feedId = entry.querySelector('[data-rss-field="feed_id"]')?.value?.trim();
                const url = entry.querySelector('[data-rss-field="url"]')?.value?.trim();
                if (!feedId || !url) continue;
                const label = entry.querySelector('[data-rss-field="label"]')?.value?.trim() || '';
                const channelPicker = entry.querySelector('[data-rss-field="channel_id"]');
                let channelId = '';
                if (channelPicker) {
                    const manual = channelPicker.querySelector('.channel-picker-manual');
                    const select = channelPicker.querySelector('.channel-picker-select');
                    const manualVisible = manual && manual.style.display !== 'none';
                    channelId = (manualVisible ? (manual?.value ?? '') : (select?.value ?? '')).trim();
                }
                const template = entry.querySelector('[data-rss-field="template"]')?.value?.trim() || '';
                const mode = entry.querySelector('[data-rss-field="mode"]')?.value || 'text';
                const embedColor = entry.querySelector('[data-rss-field="embed_color"]')?.value?.trim() || '';
                const embedTitle = entry.querySelector('[data-rss-field="embed_title"]')?.value?.trim() || '';
                const embedDescription = entry.querySelector('[data-rss-field="embed_description"]')?.value?.trim() || '';
                const embedShowImage = entry.querySelector('[data-rss-field="embed_show_image"]')?.checked;
                const embedShowLinkButton = entry.querySelector('[data-rss-field="embed_show_link_button"]')?.checked;
                const embedLinkButtonLabel = entry.querySelector('[data-rss-field="embed_link_button_label"]')?.value?.trim() || '';
                const cfg = { url };
                if (label) cfg.label = label;
                if (channelId) cfg.channel_id = channelId;
                if (template) cfg.template = template;
                if (mode === 'embed') {
                    cfg.mode = 'embed';
                    if (embedColor) cfg.embed_color = embedColor;
                    if (embedTitle) cfg.embed_title = embedTitle;
                    if (embedDescription) cfg.embed_description = embedDescription;
                    cfg.embed_show_image = embedShowImage !== false;
                    if (embedShowLinkButton) {
                        cfg.embed_show_link_button = true;
                        if (embedLinkButtonLabel) cfg.embed_link_button_label = embedLinkButtonLabel;
                    }
                }
                feedMap[feedId] = cfg;
            }
            result[key] = feedMap;
        }

        // Collect streamermap (Twitch) lists
        const streamerLists = form.querySelectorAll('[data-type="streamermap"]');
        for (const list of streamerLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.streamer-entry');
            const streamerMap = {};
            for (const entry of entries) {
                const login = entry.querySelector('[data-streamer-field="login"]')?.value?.trim();
                if (!login) continue;
                const planningPicker = entry.querySelector('[data-streamer-field="twitchPlanningChannelId"]');
                const notifPicker = entry.querySelector('[data-streamer-field="twitchNotificationChannelId"]');
                const pickValue = (picker) => {
                    if (!picker) return '';
                    const manual = picker.querySelector('.channel-picker-manual');
                    const select = picker.querySelector('.channel-picker-select');
                    const manualVisible = manual && manual.style.display !== 'none';
                    const raw = manualVisible ? (manual?.value ?? '') : (select?.value ?? '');
                    return (raw || '').trim();
                };
                const planningChan = pickValue(planningPicker);
                const notifChan = pickValue(notifPicker);
                const planningEnabled = !!entry.querySelector('[data-streamer-planning-enabled]')?.checked;
                const cfg = {};
                if (planningEnabled && planningChan) cfg.twitchPlanningChannelId = planningChan;
                if (notifChan) cfg.twitchNotificationChannelId = notifChan;
                for (const t of STREAMER_TOGGLE_FIELDS) {
                    if (t.planningOnly && !planningEnabled) continue;
                    const cb = entry.querySelector(`[data-streamer-field="${t.key}"]`);
                    if (cb && cb.checked) cfg[t.key] = true;
                }
                streamerMap[login] = cfg;
            }
            result[key] = streamerMap;
        }

        // Collect messagelist fields ({text, weight} objects)
        const msgLists = form.querySelectorAll('[data-type="messagelist"]');
        for (const list of msgLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.messagelist-entry');
            const messages = [];
            for (const entry of entries) {
                const msg = entry.querySelector('[data-msg-field="message"]')?.value?.trim();
                const w = entry.querySelector('[data-msg-field="weight"]')?.value?.trim();
                if (msg) {
                    messages.push({ text: msg, weight: w ? Number(w) : 1 });
                }
            }
            if (messages.length > 0) {
                result[key] = messages;
            }
        }

        // Collect commandlist fields
        const commandLists = form.querySelectorAll('[data-type="commandlist"]');
        for (const list of commandLists) {
            const key = list.dataset.field;
            const commands = [];
            for (const entry of list.querySelectorAll('.commandlist-entry')) {
                const name = entry.querySelector('[data-cmd-field="name"]')?.value?.trim();
                const description = entry.querySelector('[data-cmd-field="description"]')?.value?.trim();
                const responses = [];
                for (const responseEntry of entry.querySelectorAll('.commandlist-response-entry')) {
                    const text = responseEntry.querySelector('[data-response-field="text"]')?.value?.trim();
                    if (text) responses.push(text);
                }
                if (name) {
                    commands.push({ name, description: description || '', responses });
                }
            }
            // Always assigned: removing the last command must persist as [].
            result[key] = commands;
        }

        // Collect embedlist fields
        const embedLists = form.querySelectorAll('[data-type="embedlist"]');
        for (const list of embedLists) {
            const key = list.dataset.field;
            const entries = list.querySelectorAll('.embedlist-entry');
            const embeds = [];
            for (const entry of entries) {
                const title = entry.querySelector('[data-embed-field="title"]')?.value?.trim();
                const colorInput = entry.querySelector('[data-embed-field="color"]');
                let color = colorInput?.value || '#3498db';
                if (color.startsWith('#')) {
                    color = color.substring(1);
                }
                const linkEntries = entry.querySelectorAll('.embedlist-link-entry');
                const links = [];
                for (const linkEntry of linkEntries) {
                    const linkTitle = linkEntry.querySelector('[data-link-field="title"]')?.value?.trim();
                    const linkUrl = linkEntry.querySelector('[data-link-field="url"]')?.value?.trim();
                    if (linkTitle && linkUrl) {
                        links.push({ title: linkTitle, url: linkUrl });
                    }
                }
                const embed = {
                    title: title || 'Embed',
                    color: color,
                };
                if (links.length > 0) {
                    embed.links = links;
                }
                embeds.push(embed);
            }
            if (embeds.length > 0) {
                result[key] = embeds;
            }
        }

        return result;
    }

    const AI_APIS = [
        { id: 'openrouter', label: 'OpenRouter' },
        { id: 'nanogpt', label: 'NanoGPT' },
    ];

    function modelEntryHtml(m, idx) {
        const apiName = m.api || 'openrouter';
        const apiOptions = AI_APIS.map(a =>
            `<option value="${a.id}" ${a.id === apiName ? 'selected' : ''}>${a.label}</option>`).join('');
        return `
            <div class="model-entry" data-model-idx="${idx}">
                <input type="text" data-model-field="provider" value="${escapeHtml(m.provider || '')}" placeholder="ex: gpt-4.1" data-input="refresh-ai-models" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]">
                <select data-model-field="api">${apiOptions}</select>
                <input type="text" data-model-field="model_id" value="${escapeHtml(m.model_id || '')}" placeholder="ex: openai/gpt-4.1">
                <input type="text" data-model-field="display_name" value="${escapeHtml(m.display_name || '')}" placeholder="ex: OpenAI GPT-4.1" data-input="refresh-ai-models" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]">
                <button class="btn-remove-model" data-click="remove-model-entry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
            </div>`;
    }

    function modelsListFor(btn) {
        return btn.closest('.form-field').querySelector('.models-list');
    }

    function addModelEntry(btn, model = {}) {
        const list = btn.classList && btn.classList.contains('models-list') ? btn : modelsListFor(btn);
        const idx = list.querySelectorAll('.model-entry').length;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = modelEntryHtml(model, idx).trim();
        const entry = wrapper.firstElementChild;
        list.appendChild(entry);
        refreshAiModelSelects(list.closest('.schema-form'));
        return entry;
    }

    // ── AI model picker ──────────────────────────────
    const _aiCatalogCache = {};
    const _aiGuildModelsCache = {};

    function aiModelKeyFrom(id, taken) {
        const base = String(id).toLowerCase().split('/').pop()
            .replace(/[^a-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'modele';
        let key = base;
        let n = 2;
        while (taken.has(key)) key = `${base}-${n++}`;
        return key;
    }

    function formatPerMillion(value) {
        if (value === null || value === undefined) return '?';
        if (value === 0) return '0';
        return value < 0.01 ? value.toFixed(4) : value < 1 ? value.toFixed(2) : value.toFixed(value < 10 ? 2 : 1);
    }

    function formatContext(tokens) {
        if (!tokens) return '';
        return tokens >= 1000000 ? `${(tokens / 1000000).toFixed(tokens % 1000000 ? 1 : 0)}M ctx`
            : `${Math.round(tokens / 1000)}k ctx`;
    }

    function readModelEntries(list) {
        return [...list.querySelectorAll('.model-entry')].map(e => ({
            el: e,
            key: e.querySelector('[data-model-field="provider"]')?.value?.trim() || '',
            api: e.querySelector('[data-model-field="api"]')?.value || 'openrouter',
            model_id: e.querySelector('[data-model-field="model_id"]')?.value?.trim() || '',
            display_name: e.querySelector('[data-model-field="display_name"]')?.value?.trim() || '',
        }));
    }

    function flagMissingModels(list, apiName, catalog) {
        const ids = new Set(catalog.map(m => m.id));
        for (const entry of readModelEntries(list)) {
            if (entry.api !== apiName || !entry.model_id) continue;
            const missing = !ids.has(entry.model_id) && !ids.has(entry.model_id.split(':')[0]);
            entry.el.classList.toggle('model-missing', missing);
            entry.el.title = missing ? `⚠️ Modèle introuvable dans le catalogue ${apiName}` : '';
        }
    }

    async function openModelCatalog(btn) {
        const list = modelsListFor(btn);
        document.getElementById('ai-catalog-overlay')?.remove();
        const overlay = document.createElement('div');
        overlay.id = 'ai-catalog-overlay';
        overlay.className = 'ai-catalog-overlay';
        overlay.innerHTML = `
            <div class="ai-catalog" role="dialog" aria-label="Catalogue des modèles">
                <div class="ai-catalog-head">
                    <div class="ai-catalog-title">
                        <span>📚 Catalogue des modèles</span>
                        <button class="btn btn-ghost btn-sm" type="button" data-ai-close>✕</button>
                    </div>
                    <div class="ai-catalog-apis" data-ai-apis></div>
                    <input type="search" class="form-input" data-ai-search placeholder="Rechercher par nom ou identifiant…">
                </div>
                <div class="ai-catalog-list" data-ai-list><div class="loading"><div class="spinner"></div></div></div>
            </div>`;
        document.body.appendChild(overlay);
        overlay.addEventListener('click', e => {
            if (e.target === overlay || e.target.closest('[data-ai-close]')) overlay.remove();
        });

        const picker = { list, apiName: null, catalog: [], overlay };
        overlay.querySelector('[data-ai-search]').addEventListener('input', () => renderModelCatalog(picker));

        let apis = AI_APIS.map(a => ({ ...a, configured: true }));
        try {
            const res = await api('/api/ai/apis');
            if (res?.apis) apis = res.apis;
        } catch { /* toast already shown */ }
        const apisEl = overlay.querySelector('[data-ai-apis]');
        apisEl.innerHTML = apis.map(a => `
            <button type="button" class="btn btn-ghost btn-sm" data-ai-api="${a.id}" ${a.configured ? '' : 'disabled title="Clé API et URL à renseigner dans la section correspondante"'}>
                ${escapeHtml(a.label)}${a.configured ? '' : ' (non configurée)'}
            </button>`).join('');
        apisEl.addEventListener('click', e => {
            const b = e.target.closest('[data-ai-api]');
            if (b && !b.disabled) loadModelCatalog(picker, b.dataset.aiApi);
        });
        const first = apis.find(a => a.configured);
        if (first) {
            loadModelCatalog(picker, first.id);
        } else {
            overlay.querySelector('[data-ai-list]').innerHTML =
                '<p style="color:var(--text-muted);padding:12px">Aucune API configurée : renseignez la clé et l\'URL d\'OpenRouter ou de NanoGPT.</p>';
        }
    }

    async function loadModelCatalog(picker, apiName) {
        picker.apiName = apiName;
        picker.overlay.querySelectorAll('[data-ai-api]').forEach(b =>
            b.classList.toggle('active', b.dataset.aiApi === apiName));
        const listEl = picker.overlay.querySelector('[data-ai-list]');
        if (!_aiCatalogCache[apiName]) {
            listEl.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
            try {
                const res = await api(`/api/ai/catalog?api=${encodeURIComponent(apiName)}`);
                _aiCatalogCache[apiName] = res?.models || [];
            } catch {
                listEl.innerHTML = '<p style="color:var(--text-muted);padding:12px">Catalogue indisponible.</p>';
                return;
            }
        }
        if (picker.apiName !== apiName) return;
        picker.catalog = _aiCatalogCache[apiName];
        flagMissingModels(picker.list, apiName, picker.catalog);
        renderModelCatalog(picker);
    }

    function renderModelCatalog(picker) {
        const listEl = picker.overlay.querySelector('[data-ai-list]');
        const query = picker.overlay.querySelector('[data-ai-search]').value.trim().toLowerCase();
        const added = new Set(readModelEntries(picker.list).map(e => `${e.api}|${e.model_id}`));
        const matches = picker.catalog.filter(m =>
            !query || m.name.toLowerCase().includes(query) || m.id.toLowerCase().includes(query));
        const shown = matches.slice(0, 200);
        if (!shown.length) {
            listEl.innerHTML = '<p style="color:var(--text-muted);padding:12px">Aucun modèle ne correspond.</p>';
            return;
        }
        listEl.innerHTML = shown.map(m => {
            const isAdded = added.has(`${picker.apiName}|${m.id}`);
            const price = m.free ? '<span class="ai-catalog-badge">gratuit</span>'
                : `$${formatPerMillion(m.prompt_per_m)} / $${formatPerMillion(m.completion_per_m)} par M`;
            return `
                <button type="button" class="ai-catalog-row ${isAdded ? 'added' : ''}" data-ai-model="${escapeHtml(m.id)}" ${isAdded ? 'disabled' : ''}>
                    <div>
                        <div>${escapeHtml(m.name)}${isAdded ? ' <span class="ai-catalog-badge">ajouté</span>' : ''}</div>
                        <div class="ai-catalog-id">${escapeHtml(m.id)}</div>
                    </div>
                    <div class="ai-catalog-meta">${price}<br>${formatContext(m.context_length)}</div>
                </button>`;
        }).join('') + (matches.length > shown.length
            ? `<p style="color:var(--text-muted);padding:8px 12px;font-size:12px">${matches.length - shown.length} autres résultats — affinez la recherche.</p>`
            : '');
        listEl.querySelectorAll('[data-ai-model]').forEach(row => row.addEventListener('click', () => {
            const model = picker.catalog.find(m => m.id === row.dataset.aiModel);
            if (!model) return;
            const taken = new Set(readModelEntries(picker.list).map(e => e.key));
            addModelEntry(picker.list, {
                provider: aiModelKeyFrom(model.id, taken),
                api: picker.apiName,
                model_id: model.id,
                display_name: model.name,
            });
            toast(`${model.name} ajouté — pensez à sauvegarder`, 'success');
            renderModelCatalog(picker);
        }));
    }

    async function refreshAiModelSelects(root) {
        const scope = root || document;
        const selects = scope.querySelectorAll('select[data-type="aimodel"]');
        for (const select of selects) {
            const form = select.closest('.schema-form');
            const localList = form?.querySelector('.models-list');
            let models;
            let emptyLabel;
            if (localList) {
                models = readModelEntries(localList).filter(e => e.key)
                    .map(e => ({ key: e.key, display_name: e.display_name || e.key }));
                emptyLabel = '— Premier modèle utilisable —';
            } else if (state.currentServerId) {
                const sid = state.currentServerId;
                if (!_aiGuildModelsCache[sid]) {
                    try {
                        _aiGuildModelsCache[sid] = await api(`/api/servers/${sid}/ai/models`) || { models: [] };
                    } catch {
                        _aiGuildModelsCache[sid] = { models: [] };
                    }
                }
                models = _aiGuildModelsCache[sid].models || [];
                emptyLabel = '— Défaut global —';
            } else {
                continue;
            }
            const current = select.value || select.dataset.current || '';
            const known = models.some(m => m.key === current);
            select.innerHTML = `<option value="">${emptyLabel}</option>`
                + models.map(m => `<option value="${escapeHtml(m.key)}">${escapeHtml(m.display_name)} (${escapeHtml(m.key)})</option>`).join('')
                + (current && !known ? `<option value="${escapeHtml(current)}">⚠️ ${escapeHtml(current)} (introuvable)</option>` : '');
            select.value = current;
        }
    }

    function addNameMapEntry(btn) {
        const list = btn.previousElementSibling;
        const entry = document.createElement('div');
        entry.className = 'namemap-entry';
        entry.innerHTML = `
            ${memberPickerHtml('', 'data-namemap-field="discord_id"')}
            <input type="text" data-namemap-field="name" value="" placeholder="ex: Jean">
            <button class="btn-remove-entry" data-click="removeNameMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
        `;
        list.appendChild(entry);
        // Populate (fetches if not cached) then refresh the whole list so the new
        // picker excludes already-used IDs and so do siblings
        populateMemberPickers(entry).then(() => refreshNameMapListOptions(list));
        const search = entry.querySelector('.member-picker-search');
        if (search) search.focus();
    }

    function addKeyValueMapEntry(btn) {
        const list = btn.previousElementSibling;
        const keyLabel = list.dataset.keyLabel || 'Clé';
        const valueLabel = list.dataset.valueLabel || 'Valeur';
        const entry = document.createElement('div');
        entry.className = 'keyvaluemap-entry';
        entry.innerHTML = `
            <input type="text" data-kvmap-field="key" value="" placeholder="ex: ${escapeHtml(keyLabel).toLowerCase()}">
            <input type="text" data-kvmap-field="value" value="" placeholder="ex: ${escapeHtml(valueLabel).toLowerCase()}">
            <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.keyvaluemap-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
        `;
        list.appendChild(entry);
        entry.querySelector('input').focus();
    }

    const STREAMER_TOGGLE_FIELDS = [
        {key: 'twitchPlanningPinMessage', label: 'Épingler le planning', planningOnly: true},
        {key: 'notifyStreamStart', label: 'Début de live'},
        {key: 'notifyStreamUpdate', label: 'Changements live'},
        {key: 'notifyStreamEnd', label: 'Fin de live'},
        {key: 'notifyEmoteChanges', label: 'Emotes'},
        {key: 'manageDiscordEvents', label: 'Événement Discord'},
    ];

    function renderStreamerEntry(login, cfg) {
        const planningChan = cfg.twitchPlanningChannelId ? String(cfg.twitchPlanningChannelId) : '';
        const notifChan = cfg.twitchNotificationChannelId ? String(cfg.twitchNotificationChannelId) : '';
        const planningEnabled = !!planningChan;
        const toggles = STREAMER_TOGGLE_FIELDS.map(t => `
            <label class="streamer-toggle-inline${t.planningOnly ? ' planning-only' : ''}">
                <input type="checkbox" data-streamer-field="${t.key}" ${cfg[t.key] ? 'checked' : ''}>
                <span>${escapeHtml(t.label)}</span>
            </label>`).join('');
        return `
            <div class="streamer-entry${planningEnabled ? '' : ' planning-disabled'}">
                <div class="streamer-entry-header">
                    <input type="text" data-streamer-field="login" value="${escapeHtml(login || '')}" placeholder="Login Twitch (ex: zerator)">
                    <button class="btn-remove-streamer" data-click="remove-closest" data-click-args="[&quot;.streamer-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                </div>
                <label class="streamer-planning-toggle">
                    <input type="checkbox" data-streamer-planning-enabled ${planningEnabled ? 'checked' : ''} data-change="toggleStreamerPlanning" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <span>Activer le message de planning</span>
                </label>
                <div class="streamer-entry-grid">
                    <div class="streamer-field planning-only">
                        <label>Salon planning</label>
                        <div class="channel-picker" data-streamer-field="twitchPlanningChannelId" data-current="${escapeHtml(planningChan)}">
                            <select class="form-input channel-picker-select">
                                <option value="">— Chargement des salons… —</option>
                                ${planningChan ? `<option value="${escapeHtml(planningChan)}" selected>${escapeHtml(planningChan)}</option>` : ''}
                            </select>
                            <input type="text" class="form-input channel-picker-manual" value="${escapeHtml(planningChan)}" placeholder="ID du salon" style="display:none;">
                            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle">✎</button>
                        </div>
                    </div>
                    <div class="streamer-field">
                        <label>Salon notifications</label>
                        <div class="channel-picker" data-streamer-field="twitchNotificationChannelId" data-current="${escapeHtml(notifChan)}">
                            <select class="form-input channel-picker-select">
                                <option value="">— Chargement des salons… —</option>
                                ${notifChan ? `<option value="${escapeHtml(notifChan)}" selected>${escapeHtml(notifChan)}</option>` : ''}
                            </select>
                            <input type="text" class="form-input channel-picker-manual" value="${escapeHtml(notifChan)}" placeholder="ID du salon" style="display:none;">
                            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle">✎</button>
                        </div>
                    </div>
                </div>
                <div class="streamer-toggles">${toggles}</div>
            </div>`;
    }

    function toggleStreamerPlanning(checkbox) {
        const entry = checkbox.closest('.streamer-entry');
        if (!entry) return;
        entry.classList.toggle('planning-disabled', !checkbox.checked);
    }

    function addStreamerEntry(btn) {
        const list = btn.previousElementSibling;
        if (!list || !list.classList.contains('streamers-list')) return;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderStreamerEntry('', {}).trim();
        const entry = wrapper.firstElementChild;
        list.appendChild(entry);
        populateChannelPickers(entry);
        entry.querySelector('input[type="text"]').focus();
    }

    function renderYoutubeChannelEntry(handle, cfg) {
        const shorts = !!cfg.shorts;
        const live = !!cfg.live;
        const vod = cfg.vod !== false; // default true
        return `
            <div class="youtubechannel-entry">
                <input type="text" data-yt-field="handle" value="${escapeHtml(handle || '')}" placeholder="@handle">
                <input type="text" data-yt-field="label" value="${escapeHtml(cfg.label || '')}" placeholder="Libellé (optionnel)">
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="shorts" ${shorts ? 'checked' : ''}>
                    <span>Shorts</span>
                </label>
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="live" ${live ? 'checked' : ''}>
                    <span>Live</span>
                </label>
                <label class="youtubechannel-toggle">
                    <input type="checkbox" data-yt-field="vod" ${vod ? 'checked' : ''}>
                    <span>VOD</span>
                </label>
                <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.youtubechannel-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
            </div>`;
    }

    function addYoutubeChannelEntry(btn) {
        const list = btn.previousElementSibling;
        if (!list || !list.classList.contains('youtubechannel-list')) return;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderYoutubeChannelEntry('', { shorts: false, live: false, vod: true }).trim();
        const entry = wrapper.firstElementChild;
        list.appendChild(entry);
        entry.querySelector('input[type="text"]').focus();
    }

    function renderRssFeedEntry(feedId, cfg) {
        const safeChannel = escapeHtml(cfg.channel_id || '');
        const mode = (cfg.mode === 'embed') ? 'embed' : 'text';
        // Discord brand-blue default; #RRGGBB format expected by <input type="color">.
        let color = (cfg.embed_color || '#5865F2').toString().trim();
        if (!color.startsWith('#')) color = '#' + color;
        if (!/^#[0-9a-fA-F]{6}$/.test(color)) color = '#5865F2';
        const showImage = cfg.embed_show_image !== false;
        const showLinkButton = !!cfg.embed_show_link_button;
        // Feeds that came from a known preset render locked/compact by default so
        // the curated embed templates can't be broken by accident. Advanced editing
        // stays one click away via the toggle.
        const isPreset = !!(typeof RSS_PRESETS !== 'undefined' && RSS_PRESETS[feedId]);
        const advanced = isPreset ? 'off' : 'on';
        const advancedLabel = isPreset ? '⚙️ Édition avancée' : '▾ Réduire';
        const presetName = escapeHtml(cfg.label || feedId || '');
        const banner = isPreset ? `
                <div class="rssfeed-preset-banner">
                    <span class="rssfeed-preset-badge">🎮 Préréglage</span>
                    <span class="rssfeed-preset-name">${presetName}</span>
                    <span class="rssfeed-preset-hint">Mise en forme gérée automatiquement — choisissez le salon ci-dessous.</span>
                </div>` : '';
        return `
            <div class="rssfeed-entry${isPreset ? ' preset' : ''}" data-mode="${mode}" data-advanced="${advanced}">${banner}
                <div class="rssfeed-row">
                    <input class="rssfeed-identity" type="text" data-rss-field="feed_id" value="${escapeHtml(feedId || '')}" placeholder="ex: epic-free">
                    <input class="rssfeed-identity" type="text" data-rss-field="url" value="${escapeHtml(cfg.url || '')}" placeholder="https://exemple.com/feed.xml">
                    <input class="rssfeed-identity" type="text" data-rss-field="label" value="${escapeHtml(cfg.label || '')}" placeholder="Libellé (optionnel)">
                    <div class="channel-picker" data-rss-field="channel_id" data-current="${safeChannel}">
                        <select class="form-input channel-picker-select">
                            <option value="">— Salon par défaut —</option>
                            ${cfg.channel_id ? `<option value="${safeChannel}" selected>${safeChannel}</option>` : ''}
                        </select>
                        <input type="text" class="form-input channel-picker-manual" value="${safeChannel}" placeholder="ID de salon" style="display:none;">
                        <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle">✎</button>
                    </div>
                    <select class="rssfeed-identity" data-rss-field="mode" data-change="onRssModeChange" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                        <option value="text" ${mode === 'text' ? 'selected' : ''}>Texte</option>
                        <option value="embed" ${mode === 'embed' ? 'selected' : ''}>Embed</option>
                    </select>
                    <button type="button" class="btn-rss-advanced" data-click="toggleRssAdvanced" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Afficher / masquer l'éditeur complet">${advancedLabel}</button>
                    <button class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.rssfeed-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                </div>
                <div class="rssfeed-advanced">
                    <div class="rssfeed-template-row">
                        <span class="rssfeed-field-label">Modèle texte</span>
                        <textarea data-rss-field="template" rows="2" placeholder="ex: **{label}** — [{title}]({link})">${escapeHtml(cfg.template || '')}</textarea>
                    </div>
                    <div class="rssfeed-embed-panel">
                        <span class="rssfeed-field-label">Couleur</span>
                        <input type="color" data-rss-field="embed_color" value="${color}">
                        <span class="rssfeed-field-label">Image</span>
                        <label class="rssfeed-toggle">
                            <input type="checkbox" data-rss-field="embed_show_image" ${showImage ? 'checked' : ''}>
                            <span>Afficher l'image détectée</span>
                        </label>
                        <span class="rssfeed-field-label">Titre embed</span>
                        <input type="text" data-rss-field="embed_title" value="${escapeHtml(cfg.embed_title || '')}" placeholder="{title}">
                        <span class="rssfeed-field-label">Description</span>
                        <textarea data-rss-field="embed_description" rows="4" placeholder="{summary}">${escapeHtml(cfg.embed_description || '')}</textarea>
                        <span class="rssfeed-field-label">Bouton lien</span>
                        <label class="rssfeed-toggle">
                            <input type="checkbox" data-rss-field="embed_show_link_button" ${showLinkButton ? 'checked' : ''}>
                            <span>Afficher un bouton vers l'article</span>
                        </label>
                        <span class="rssfeed-field-label">Libellé bouton</span>
                        <input type="text" data-rss-field="embed_link_button_label" value="${escapeHtml(cfg.embed_link_button_label || '')}" placeholder="Voir l'article">
                    </div>
                    <div class="messagelist-variables" style="margin-top:8px;">
                        <span>Variables : </span>
                        ${['{title}','{link}','{summary}','{label}','{author}','{image}','{published_date}'].map(v =>
                            `<button type="button" class="var-chip" data-var="${v}" data-mousedown="insertVariable" data-mousedown-args="[{&quot;$&quot;:&quot;event&quot;},{&quot;$&quot;:&quot;el&quot;}]">${v}</button>`
                        ).join('')}
                    </div>
                </div>
            </div>`;
    }

    function onRssModeChange(select) {
        const entry = select.closest('.rssfeed-entry');
        if (entry) entry.dataset.mode = select.value;
    }

    function toggleRssAdvanced(btn) {
        const entry = btn.closest('.rssfeed-entry');
        if (!entry) return;
        const on = entry.dataset.advanced === 'on';
        entry.dataset.advanced = on ? 'off' : 'on';
        btn.textContent = on ? '⚙️ Édition avancée' : '▾ Réduire';
    }

    function addRssFeedEntry(btn) {
        const list = btn.previousElementSibling;
        if (!list || !list.classList.contains('rssfeed-list')) return;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderRssFeedEntry('', {}).trim();
        const entry = wrapper.firstElementChild;
        list.appendChild(entry);
        if (typeof populateChannelPickers === 'function') populateChannelPickers(entry);
        entry.querySelector('input[type="text"]').focus();
    }

    // Shared default templates for the LootScraper feeds. Description uses
    // {summary} flattened by the parser plus a Discord timestamp tag for the
    // publish time, and the link goes onto a Discord link button rather than
    // being embedded inline.
    // The feeds' base URL is config (global section "rss"), fetched on first use.
    const _LOOTSCRAPER_DESC = '{summary}\n\n🕐 Publié {published_relative} ({published_short})';
    let _lootScraperBase = null;
    async function lootScraperBase() {
        if (_lootScraperBase === null) {
            const data = await api('/api/presets/rss');
            _lootScraperBase = (data && data.lootScraperBaseUrl) || '';
        }
        return _lootScraperBase;
    }
    function _lsPreset(slug, file, label, color, btnLabel, opts = {}) {
        return {
            feed_id: slug,
            file,
            label,
            mode: 'embed',
            embed_color: color,
            embed_title: (opts.titleEmoji || '🎮') + ' {title}',
            embed_description: _LOOTSCRAPER_DESC,
            embed_show_image: true,
            embed_show_link_button: true,
            embed_link_button_label: btnLabel,
        };
    }
    const RSS_PRESETS = {
        // PC stores — free full games
        'lootscraper-epic': _lsPreset(
            'lootscraper-epic', 'lootscraper_epic_game.xml',
            'Epic Games — Gratuit', '#0078F2', 'Récupérer sur Epic Games'),
        'lootscraper-gog': _lsPreset(
            'lootscraper-gog', 'lootscraper_gog_game.xml',
            'GOG — Gratuit', '#86328A', 'Récupérer sur GOG'),
        'lootscraper-humble': _lsPreset(
            'lootscraper-humble', 'lootscraper_humble_game.xml',
            'Humble — Gratuit', '#CC2929', 'Récupérer sur Humble'),
        'lootscraper-itch': _lsPreset(
            'lootscraper-itch', 'lootscraper_itch_game.xml',
            'itch.io — Gratuit', '#FA5C5C', 'Récupérer sur itch.io'),
        // Subscription / loot — Amazon & Steam
        'lootscraper-amazon': _lsPreset(
            'lootscraper-amazon', 'lootscraper_amazon_game.xml',
            'Amazon Prime Gaming', '#FF9900', 'Récupérer sur Prime Gaming'),
        'lootscraper-amazon-loot': _lsPreset(
            'lootscraper-amazon-loot', 'lootscraper_amazon_loot.xml',
            'Amazon Prime — Loot', '#FF9900', 'Récupérer sur Prime Gaming',
            { titleEmoji: '🎁' }),
        'lootscraper-steam': _lsPreset(
            'lootscraper-steam', 'lootscraper_steam_game.xml',
            'Steam — Gratuit', '#66C0F4', 'Récupérer sur Steam'),
        'lootscraper-steam-loot': _lsPreset(
            'lootscraper-steam-loot', 'lootscraper_steam_loot.xml',
            'Steam — Loot', '#66C0F4', 'Récupérer sur Steam',
            { titleEmoji: '🎁' }),
        // Mobile stores
        'lootscraper-epic-android': _lsPreset(
            'lootscraper-epic-android', 'lootscraper_epic_game_android.xml',
            'Epic Games — Android', '#0078F2', 'Récupérer sur Epic Games',
            { titleEmoji: '📱' }),
        'lootscraper-epic-ios': _lsPreset(
            'lootscraper-epic-ios', 'lootscraper_epic_game_ios.xml',
            'Epic Games — iOS', '#0078F2', 'Récupérer sur Epic Games',
            { titleEmoji: '📱' }),
        'lootscraper-apple': _lsPreset(
            'lootscraper-apple', 'lootscraper_apple_game.xml',
            'App Store — Gratuit', '#007AFF', "Récupérer sur l'App Store",
            { titleEmoji: '📱' }),
        'lootscraper-google': _lsPreset(
            'lootscraper-google', 'lootscraper_google_game.xml',
            'Google Play — Gratuit', '#34A853', 'Récupérer sur Google Play',
            { titleEmoji: '📱' }),
    };
    // "All" bundle keeps the four originals (Epic / Amazon / itch / Steam) so
    // a fresh install matches the canonical free-PC-games setup.
    const _LOOTSCRAPER_ALL_PC_GAMES = [
        'lootscraper-epic', 'lootscraper-gog',
        'lootscraper-humble', 'lootscraper-itch',
        'lootscraper-amazon', 'lootscraper-steam',
    ];
    const _LOOTSCRAPER_ALL_BASIC = [
        'lootscraper-epic', 'lootscraper-amazon',
        'lootscraper-itch', 'lootscraper-steam',
    ];

    async function applyRssPreset(select) {
        const value = select.value;
        select.value = '';
        if (!value) return;
        let base = '';
        try {
            base = await lootScraperBase();
        } catch (e) {
            return;  // api() already showed the error
        }
        if (!base) {
            toast("Préréglages indisponibles : renseignez l'URL des flux LootScraper dans la section globale « Flux RSS ».", 'error');
            return;
        }
        if (!base.endsWith('/')) base += '/';
        let list = select.previousElementSibling;
        while (list && !(list.classList && list.classList.contains('rssfeed-list'))) {
            list = list.previousElementSibling;
        }
        if (!list) return;
        let ids;
        if (value === 'lootscraper-all') ids = _LOOTSCRAPER_ALL_BASIC;
        else if (value === 'lootscraper-all-pc') ids = _LOOTSCRAPER_ALL_PC_GAMES;
        else if (value === 'lootscraper-everything') ids = Object.keys(RSS_PRESETS);
        else ids = [value];
        const existing = new Set(
            Array.from(list.querySelectorAll('.rssfeed-entry input[data-rss-field="feed_id"]'))
                .map(i => (i.value || '').trim())
                .filter(Boolean)
        );
        for (const id of ids) {
            const preset = RSS_PRESETS[id];
            if (!preset || existing.has(preset.feed_id)) continue;
            const wrapper = document.createElement('div');
            const { file, ...cfg } = preset;
            wrapper.innerHTML = renderRssFeedEntry(preset.feed_id, { ...cfg, url: base + file }).trim();
            const entry = wrapper.firstElementChild;
            list.appendChild(entry);
            if (typeof populateChannelPickers === 'function') populateChannelPickers(entry);
            existing.add(preset.feed_id);
        }
    }

    function addSpotifyUserEntry(btn) {
        const list = btn.previousElementSibling;
        const entry = document.createElement('div');
        entry.className = 'spotifymap-entry';
        entry.innerHTML = `
            <input type="text" data-spotifymap-field="spotifyId" value="" placeholder="ex: exemple123">
            ${memberPickerHtml('', 'data-spotifymap-field="discordId"')}
            <button class="btn-remove-entry" data-click="removeSpotifyMapEntry" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
        `;
        list.appendChild(entry);
        populateMemberPickers(entry).then(() => refreshSpotifyMapListOptions(list));
        entry.querySelector('input').focus();
    }

    function removeSpotifyMapEntry(btn) {
        const entry = btn.closest('.spotifymap-entry');
        if (!entry) return;
        const list = entry.closest('.spotifymap-list');
        entry.remove();
        if (list) refreshSpotifyMapListOptions(list);
    }

    function refreshSpotifyMapListOptions(list) {
        if (list) refreshMemberPickerGroup(list.querySelectorAll('.member-picker'));
    }

    function renderTeamEntry(t, idx) {
        t = t || {};
        const chan = String(t.channelId || '').trim();
        const pin = !!t.pin;
        return `
            <div class="team-entry" data-team-idx="${idx}">
                <div class="team-entry-grid">
                    <div class="team-field">
                        <label>Nom</label>
                        <input type="text" data-team-field="name" value="${escapeHtml(t.name || '')}" placeholder="ex: Mandatory">
                    </div>
                    <div class="team-field">
                        <label>ID VLR.gg</label>
                        <input type="text" data-team-field="vlrTeamId" value="${escapeHtml(t.vlrTeamId || '')}" placeholder="ex: 7967">
                    </div>
                    <div class="team-field team-field-channel">
                        <label>Salon planning</label>
                        <div class="channel-picker" data-team-field="channelId" data-current="${escapeHtml(chan)}">
                            <select class="form-input channel-picker-select">
                                <option value="">— Chargement des salons… —</option>
                                ${chan ? `<option value="${escapeHtml(chan)}" selected>${escapeHtml(chan)}</option>` : ''}
                            </select>
                            <input type="text" class="form-input channel-picker-manual" value="${escapeHtml(chan)}" placeholder="ID du salon" style="display:none;">
                            <button type="button" class="btn-channel-toggle" data-click="toggleChannelPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle">✎</button>
                        </div>
                    </div>
                </div>
                <div class="team-entry-footer">
                    <label class="streamer-toggle-inline">
                        <input type="checkbox" data-team-field="pin" ${pin ? 'checked' : ''}>
                        <span>Épingler le message</span>
                    </label>
                    <button class="btn-remove-team" data-click="remove-closest" data-click-args="[&quot;.team-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
                </div>
            </div>`;
    }

    function addTeamEntry(btn) {
        const list = btn.previousElementSibling;
        const idx = list.querySelectorAll('.team-entry').length;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderTeamEntry({}, idx).trim();
        const entry = wrapper.firstElementChild;
        list.appendChild(entry);
        populateChannelPickers(entry);
        entry.querySelector('input[data-team-field="name"]').focus();
    }

    function insertVariable(event, btn) {
        event.preventDefault();
        const variable = btn.dataset.var;
        const input = document.activeElement;
        if (!input || (input.tagName !== 'INPUT' && input.tagName !== 'TEXTAREA')) return;
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? input.value.length;
        input.value = input.value.slice(0, start) + variable + input.value.slice(end);
        input.selectionStart = input.selectionEnd = start + variable.length;
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function addMessageEntry(btn) {
        // Find the messagelist-list container (may have a variables div in between)
        let list = btn.previousElementSibling;
        while (list && !list.classList.contains('messagelist-list')) {
            list = list.previousElementSibling;
        }
        if (!list) return;
        const entry = document.createElement('div');
        entry.className = 'messagelist-entry';
        entry.innerHTML = `
            <input type="text" data-msg-field="message" value="" placeholder="Message…">
            <input type="number" data-msg-field="weight" value="1" min="0" placeholder="1">
            <button class="btn-remove-msg" data-click="remove-closest" data-click-args="[&quot;.messagelist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
        `;
        list.appendChild(entry);
        entry.querySelector('input').focus();
    }

    function addCommandEntry(btn) {
        const list = btn.previousElementSibling;
        if (!list) return;
        const entry = document.createElement('div');
        entry.className = 'commandlist-entry';
        entry.innerHTML = `
            <div class="commandlist-entry-header">
                <div class="commandlist-entry-title">/nouvelle-commande</div>
                <button type="button" class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.commandlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cette commande">✕</button>
            </div>
            <div class="commandlist-entry-fields">
                <div><label class="commandlist-field-label">Nom</label>
                <input type="text" class="form-input" data-cmd-field="name" value="" placeholder="blague" maxlength="32" data-input="updateCommandTitle" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]"></div>
                <div><label class="commandlist-field-label">Description</label>
                <input type="text" class="form-input" data-cmd-field="description" value="" placeholder="Raconte une blague au hasard" maxlength="100"></div>
            </div>
            <div class="commandlist-responses">
                <div class="commandlist-responses-label">Réponses (tirées au hasard)</div>
            </div>
            <button type="button" class="btn-add-link" data-click="addCommandResponse" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter une réponse</button>
        `;
        list.appendChild(entry);
        // A command without a response is dropped at load, so start with one.
        addCommandResponse(entry.querySelector('.btn-add-link'));
        entry.querySelector('[data-cmd-field="name"]').focus();
    }

    function addCommandResponse(btn) {
        const commandEntry = btn.closest('.commandlist-entry');
        if (!commandEntry) return;
        const container = commandEntry.querySelector('.commandlist-responses');
        const responseEntry = document.createElement('div');
        responseEntry.className = 'commandlist-response-entry';
        responseEntry.innerHTML = `
            <input type="text" data-response-field="text" value="" placeholder="https://exemple.com/gif.gif ou un texte" maxlength="2000">
            <button type="button" class="btn-remove-response" data-click="remove-closest" data-click-args="[&quot;.commandlist-response-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
        `;
        container.appendChild(responseEntry);
        responseEntry.querySelector('input').focus();
    }

    function updateCommandTitle(input) {
        const entry = input.closest('.commandlist-entry');
        const title = entry?.querySelector('.commandlist-entry-title');
        if (title) {
            // Mirror the server-side normalisation: lowercase, no spaces.
            title.textContent = '/' + (input.value.trim().toLowerCase().replace(/\s+/g, '-') || 'nouvelle-commande');
        }
    }

    function addEmbedEntry(btn, fieldKey) {
        const list = btn.previousElementSibling;
        if (!list) return;
        const idx = list.querySelectorAll('.embedlist-entry').length;
        const entry = document.createElement('div');
        entry.className = 'embedlist-entry';
        entry.dataset.embedIdx = idx;
        entry.innerHTML = `
            <div class="embedlist-entry-header">
                <div class="embedlist-entry-title">Nouvel embed</div>
                <div class="embedlist-entry-controls">
                    <button class="btn-remove-embed" data-click="remove-closest" data-click-args="[&quot;.embedlist-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer cet embed">✕</button>
                </div>
            </div>
            <div class="embedlist-entry-fields">
                <div><label style="font-size:11px;color:var(--text-muted)">Titre</label>
                <input type="text" class="form-input" data-embed-field="title" value="Nouvel embed" placeholder="Titre de l'embed"></div>
                <div><label style="font-size:11px;color:var(--text-muted)">Couleur</label>
                <div class="embedlist-color-picker">
                    <input type="color" class="embedlist-color-input" data-embed-field="color" value="#3498db" data-change="updateEmbedColorText" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <div class="embedlist-color-text">3498db</div>
                </div></div>
            </div>
            <div class="embedlist-links">
                <div class="embedlist-links-label">Liens</div>
            </div>
            <button type="button" class="btn-add-link" data-click="addEmbedLink" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">+ Ajouter un lien</button>
        `;
        list.appendChild(entry);

        // Update title when title input changes
        const titleInput = entry.querySelector('[data-embed-field="title"]');
        titleInput.addEventListener('change', (e) => {
            entry.querySelector('.embedlist-entry-title').textContent = e.target.value || 'Nouvel embed';
        });
        titleInput.focus();
    }

    function addEmbedLink(btn) {
        const embedEntry = btn.closest('.embedlist-entry');
        if (!embedEntry) return;
        const linkContainer = embedEntry.querySelector('.embedlist-links');
        const idx = linkContainer.querySelectorAll('.embedlist-link-entry').length;
        const linkEntry = document.createElement('div');
        linkEntry.className = 'embedlist-link-entry';
        linkEntry.dataset.linkIdx = idx;
        linkEntry.innerHTML = `
            <input type="text" data-link-field="title" value="" placeholder="Titre du lien" maxlength="256">
            <input type="text" data-link-field="url" value="" placeholder="https://exemple.com" maxlength="2048">
            <button type="button" class="btn-remove-link" data-click="remove-closest" data-click-args="[&quot;.embedlist-link-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
        `;
        linkContainer.appendChild(linkEntry);
        linkEntry.querySelector('input').focus();
    }

    function updateEmbedColorText(input) {
        const color = input.value.substring(1); // Remove # from color
        const textDiv = input.nextElementSibling;
        if (textDiv) {
            textDiv.textContent = color;
        }
    }

    function togglePasswordVisibility(btn) {
        const input = btn.previousElementSibling;
        input.type = input.type === 'password' ? 'text' : 'password';
    }

    // ── Channel picker ────────────────────────────────
    const _channelCache = {}; // serverId -> {channels, categories}

    async function fetchServerChannels(serverId) {
        if (!serverId) return null;
        if (_channelCache[serverId]) return _channelCache[serverId];
        try {
            const data = await api(`/api/servers/${serverId}/channels`);
            if (data && data.channels) {
                _channelCache[serverId] = data;
                return data;
            }
        } catch (e) {
            console.warn('Could not fetch channels', e);
        }
        return null;
    }

    async function populateChannelPickers(container) {
        const serverId = state.currentServerId;
        if (!serverId || !container) return;
        const pickers = container.querySelectorAll('.channel-picker');
        if (pickers.length === 0) return;
        const data = await fetchServerChannels(serverId);
        if (!data) {
            // Fall back to manual text entry
            for (const picker of pickers) {
                toggleChannelPickerMode(picker.querySelector('.btn-channel-toggle'), true);
                const select = picker.querySelector('select');
                if (select) select.innerHTML = `<option value="">— Bot hors ligne —</option>`;
            }
            return;
        }
        const {channels, categories} = data;
        // Split top-level channels from threads and index threads by their parent text channel
        const topLevel = [];
        const threadsByParent = new Map();
        for (const c of channels) {
            if (c.is_thread) {
                const pid = c.parent_id || '__orphan__';
                if (!threadsByParent.has(pid)) threadsByParent.set(pid, []);
                threadsByParent.get(pid).push(c);
            } else {
                topLevel.push(c);
            }
        }
        // Group top-level channels by parent category
        const grouped = new Map();
        for (const c of topLevel) {
            const catId = c.parent_id || '__none__';
            if (!grouped.has(catId)) grouped.set(catId, []);
            grouped.get(catId).push(c);
        }
        // Build the options HTML once
        let opts = '<option value="">— Choisir un salon —</option>';
        const orderedCats = [
            ['__none__', 'Sans catégorie'],
            ...(categories || []).map(c => [c.id, c.name]),
        ];
        const renderThreadOption = (t) => {
            const prefix = t.archived ? '🗄️ ' : '🧵 ';
            return `<option value="${escapeHtml(t.id)}">&nbsp;&nbsp;↳ ${prefix}${escapeHtml(t.name)}</option>`;
        };
        for (const [catId, catName] of orderedCats) {
            const list = grouped.get(catId);
            if (!list || list.length === 0) continue;
            opts += `<optgroup label="${escapeHtml(catName)}">`;
            for (const ch of list) {
                opts += `<option value="${escapeHtml(ch.id)}">#${escapeHtml(ch.name)}</option>`;
                const threads = threadsByParent.get(ch.id);
                if (threads && threads.length) {
                    for (const t of threads) {
                        opts += renderThreadOption(t);
                    }
                    threadsByParent.delete(ch.id);
                }
            }
            opts += '</optgroup>';
        }
        // Any threads whose parent isn't in the visible channel list (e.g. no perms on parent)
        const orphanThreads = [];
        for (const list of threadsByParent.values()) {
            for (const t of list) orphanThreads.push(t);
        }
        if (orphanThreads.length) {
            opts += `<optgroup label="Fils (autres)">`;
            for (const t of orphanThreads) {
                const prefix = t.archived ? '🗄️ ' : '🧵 ';
                opts += `<option value="${escapeHtml(t.id)}">${prefix}${escapeHtml(t.name)}</option>`;
            }
            opts += '</optgroup>';
        }
        const validIds = new Set(channels.map(c => c.id));
        for (const picker of pickers) {
            const select = picker.querySelector('select');
            const manual = picker.querySelector('.channel-picker-manual');
            const current = picker.dataset.current || '';
            select.innerHTML = opts;
            if (current && validIds.has(current)) {
                select.value = current;
            } else if (current) {
                // Keep the unknown value visible at the top
                const opt = document.createElement('option');
                opt.value = current;
                opt.textContent = `ID inconnu: ${current}`;
                opt.selected = true;
                select.insertBefore(opt, select.firstChild.nextSibling);
            }
            // Sync manual value with select on change
            select.addEventListener('change', () => {
                if (manual) manual.value = select.value;
            });
        }
    }

    function toggleChannelPickerMode(btn, forceManual) {
        const picker = btn.closest('.channel-picker');
        if (!picker) return;
        const select = picker.querySelector('.channel-picker-select');
        const manual = picker.querySelector('.channel-picker-manual');
        const showManual = forceManual === true ? true : (manual.style.display === 'none');
        manual.style.display = showManual ? '' : 'none';
        select.style.display = showManual ? 'none' : '';
        btn.textContent = showManual ? '☰' : '✎';
        btn.title = showManual ? 'Choisir dans la liste' : 'Saisie manuelle';
        if (showManual && select.value && !manual.value) {
            manual.value = select.value;
        } else if (!showManual && manual.value && !select.value) {
            // Try to set select from manual
            const opt = select.querySelector(`option[value="${CSS.escape(manual.value)}"]`);
            if (opt) select.value = manual.value;
        }
    }

    // ── Member picker ─────────────────────────────────
    // A guild can have tens of thousands of members: the dashboard never loads
    // them all. Each picker shows the first page of the guild's members, or the
    // page its search box found (server-side, /members?q=), and the members a
    // form already references are resolved by id (/members?ids=) to be labelled.
    const MEMBER_PAGE = 50;
    const MEMBER_IDS_PER_REQUEST = 200;
    const _memberCache = {}; // serverId -> { byId: Map(id -> member), firstPage, total }

    function memberStore(serverId) {
        if (!_memberCache[serverId]) {
            _memberCache[serverId] = { byId: new Map(), firstPage: null, total: 0 };
        }
        return _memberCache[serverId];
    }

    async function queryServerMembers(serverId, params) {
        const qs = new URLSearchParams(params).toString();
        const data = await api(`/api/servers/${encodeURIComponent(serverId)}/members?${qs}`);
        if (!data || !Array.isArray(data.members)) return null;
        const store = memberStore(serverId);
        for (const m of data.members) store.byId.set(String(m.id), m);
        return data;
    }

    async function firstMemberPage(serverId) {
        if (!serverId) return null;
        const store = memberStore(serverId);
        if (!store.firstPage) {
            const data = await queryServerMembers(serverId, { limit: MEMBER_PAGE });
            if (!data) return null;
            store.firstPage = data.members;
            store.total = data.total || data.members.length;
        }
        return store.firstPage;
    }

    // Look up the members behind *ids* not seen yet; returns the id -> member map.
    async function resolveServerMembers(serverId, ids) {
        const store = memberStore(serverId);
        const missing = [...new Set(ids.map(String).filter(id => /^\d+$/.test(id) && !store.byId.has(id)))];
        for (let i = 0; i < missing.length; i += MEMBER_IDS_PER_REQUEST) {
            await queryServerMembers(serverId, { ids: missing.slice(i, i + MEMBER_IDS_PER_REQUEST).join(',') });
        }
        return store.byId;
    }

    function memberPickerHtml(currentId, fieldAttr) {
        const safeId = escapeHtml(currentId || '');
        return `
            <div class="member-picker" data-current="${safeId}">
                <input type="search" class="form-input member-picker-search" placeholder="🔍 Rechercher" data-input="onMemberPickerSearch" data-input-args="[{&quot;$&quot;:&quot;el&quot;}]" aria-label="Rechercher un membre">
                <select class="form-input member-picker-select" data-change="onMemberPickerChange" data-change-args="[{&quot;$&quot;:&quot;el&quot;}]">
                    <option value="">— Chargement des membres… —</option>
                    ${currentId ? `<option value="${safeId}" selected>${safeId}</option>` : ''}
                </select>
                <input type="text" class="form-input member-picker-manual" ${fieldAttr} value="${safeId}" placeholder="ex: 123456789012345678" style="display:none;">
                <button type="button" class="btn-member-toggle" data-click="toggleMemberPickerMode" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]" title="Saisie manuelle">✎</button>
            </div>`;
    }

    function getDiscord2NameMap() {
        const sid = state.currentServerId;
        const map = state.servers?.[sid]?.config?.discord2name;
        return (map && typeof map === 'object' && !Array.isArray(map)) ? map : {};
    }

    function memberOptionLabel(m) {
        const prenom = getDiscord2NameMap()[m.id];
        const fallback = m.display_name || m.username || m.id;
        const display = prenom || fallback;
        const uname = m.username && m.username !== display ? ` (${m.username})` : '';
        return `${display}${uname} — ${m.id}`;
    }

    function getPickerSelectedId(picker) {
        const manual = picker.querySelector('.member-picker-manual');
        const select = picker.querySelector('.member-picker-select');
        const manualVisible = manual && manual.style.display !== 'none';
        const val = manualVisible ? (manual?.value ?? '') : (select?.value ?? '');
        return (val || picker.dataset.current || '').trim();
    }

    // The pickers whose choices exclude each other: the rows of one list.
    function memberPickerGroup(picker) {
        const list = picker.closest('.namemap-list, .spotifymap-list');
        return list ? list.querySelectorAll('.member-picker') : [picker];
    }

    function refreshMemberPickerGroup(pickers) {
        const store = _memberCache[state.currentServerId];
        if (!store || !store.firstPage) return;
        const currentIds = new Map();
        for (const p of pickers) currentIds.set(p, getPickerSelectedId(p));
        for (const picker of pickers) {
            const select = picker.querySelector('select');
            if (!select) continue;
            const ownId = currentIds.get(picker) || '';
            const excludeIds = new Set();
            for (const p of pickers) {
                if (p === picker) continue;
                const id = currentIds.get(p);
                if (id) excludeIds.add(id);
            }
            const searching = Array.isArray(picker._results);
            const list = searching ? picker._results : store.firstPage;
            const total = searching ? picker._total : store.total;
            let opts = `<option value="">${searching && !list.length ? '— Aucun membre trouvé —' : '— Choisir un membre —'}</option>`;
            const shown = new Set();
            if (ownId) {
                const m = store.byId.get(ownId);
                opts += `<option value="${escapeHtml(ownId)}">${escapeHtml(m ? memberOptionLabel(m) : `ID inconnu: ${ownId}`)}</option>`;
                shown.add(ownId);
            }
            for (const m of list) {
                if (shown.has(m.id) || excludeIds.has(m.id)) continue;
                shown.add(m.id);
                opts += `<option value="${escapeHtml(m.id)}">${escapeHtml(memberOptionLabel(m))}</option>`;
            }
            if (total > list.length) {
                opts += `<option value="" disabled>… ${total - list.length} autre(s) : affinez la recherche</option>`;
            }
            select.innerHTML = opts;
            select.value = ownId;
        }
    }

    function refreshNameMapListOptions(list) {
        if (list) refreshMemberPickerGroup(list.querySelectorAll('.member-picker'));
    }

    async function populateMemberPickers(container) {
        const serverId = state.currentServerId;
        if (!serverId || !container) return;
        const pickers = container.querySelectorAll('.member-picker');
        if (pickers.length === 0) return;
        let page = null;
        try {
            page = await firstMemberPage(serverId);
            if (page) await resolveServerMembers(serverId, [...pickers].map(getPickerSelectedId).filter(Boolean));
        } catch (e) {
            console.warn('Could not fetch members', e);
        }
        if (!page) {
            for (const picker of pickers) {
                toggleMemberPickerMode(picker.querySelector('.btn-member-toggle'), true);
                const select = picker.querySelector('select');
                if (select) select.innerHTML = `<option value="">— Membres indisponibles —</option>`;
            }
            return;
        }
        const groups = new Set();
        for (const picker of pickers) {
            const list = picker.closest('.namemap-list, .spotifymap-list');
            groups.add(list || picker);
        }
        for (const group of groups) {
            refreshMemberPickerGroup(group.classList.contains('member-picker') ? [group] : group.querySelectorAll('.member-picker'));
        }
    }

    const _memberSearchTimers = new WeakMap();

    function onMemberPickerSearch(input) {
        const picker = input.closest('.member-picker');
        if (!picker) return;
        clearTimeout(_memberSearchTimers.get(picker));
        _memberSearchTimers.set(picker, setTimeout(() => runMemberPickerSearch(picker, input.value), 250));
    }

    async function runMemberPickerSearch(picker, query) {
        const serverId = state.currentServerId;
        const seq = (picker._searchSeq || 0) + 1;
        picker._searchSeq = seq;
        const q = query.trim();
        let results = null, total = 0;
        if (q) {
            let data = null;
            try {
                data = await queryServerMembers(serverId, { q, limit: MEMBER_PAGE });
            } catch (e) {
                console.warn('Member search failed', e);
            }
            if (picker._searchSeq !== seq) return;  // a newer search took over
            results = data ? data.members : [];
            total = data ? (data.total || 0) : 0;
        }
        picker._results = results;
        picker._total = total;
        refreshMemberPickerGroup(memberPickerGroup(picker));
    }

    function onMemberPickerChange(select) {
        const picker = select.closest('.member-picker');
        if (!picker) return;
        const manual = picker.querySelector('.member-picker-manual');
        if (manual) manual.value = select.value;
        // Auto-fill the adjacent name field if empty (namemap entries only)
        const namemapEntry = picker.closest('.namemap-entry');
        if (namemapEntry) {
            const nameInput = namemapEntry.querySelector('[data-namemap-field="name"]');
            if (nameInput && !nameInput.value.trim()) {
                const match = _memberCache[state.currentServerId]?.byId.get(select.value);
                if (match) nameInput.value = match.display_name || match.username || '';
            }
        }
        // Update sibling pickers so the newly-picked member is removed from their dropdowns
        const namemapList = picker.closest('.namemap-list');
        if (namemapList) refreshNameMapListOptions(namemapList);
        const spotifymapList = picker.closest('.spotifymap-list');
        if (spotifymapList) refreshSpotifyMapListOptions(spotifymapList);
    }

    function removeNameMapEntry(btn) {
        const entry = btn.closest('.namemap-entry');
        if (!entry) return;
        const list = entry.closest('.namemap-list');
        entry.remove();
        if (list) refreshNameMapListOptions(list);
    }

    function toggleMemberPickerMode(btn, forceManual) {
        const picker = btn.closest('.member-picker');
        if (!picker) return;
        const select = picker.querySelector('.member-picker-select');
        const manual = picker.querySelector('.member-picker-manual');
        const search = picker.querySelector('.member-picker-search');
        const showManual = forceManual === true ? true : (manual.style.display === 'none');
        manual.style.display = showManual ? '' : 'none';
        select.style.display = showManual ? 'none' : '';
        if (search) search.style.display = showManual ? 'none' : '';
        btn.textContent = showManual ? '☰' : '✎';
        btn.title = showManual ? 'Choisir dans la liste' : 'Saisie manuelle';
        const typed = manual.value.trim();
        if (showManual && select.value && !typed) {
            manual.value = select.value;
        } else if (!showManual && typed && typed !== select.value) {
            // The id typed by hand becomes the list's choice, labelled once resolved.
            if (!select.querySelector(`option[value="${CSS.escape(typed)}"]`)) {
                select.add(new Option(typed, typed), 1);
            }
            select.value = typed;
            const serverId = state.currentServerId;
            resolveServerMembers(serverId, [typed])
                .catch(e => console.warn('Could not resolve member', e))
                .finally(() => refreshMemberPickerGroup(memberPickerGroup(picker)));
        }
    }

    // ── Render ────────────────────────────────────────
    function render() {
        const app = document.getElementById('app');
        // The page is rebuilt from scratch below: release Chart.js instances
        // bound to the canvases about to be detached.
        destroyXpCharts();
        destroySpotifyCharts();
        if (state.publicView) {
            app.innerHTML = renderPublicPage();
            const { meta, kind } = state.publicView;
            if (meta && kind === 'xp') loadXpStats(meta.id);
            if (meta && kind === 'spotify') loadSpotifyStats(meta.id);
            return;
        }
        if (!state.user) {
            app.innerHTML = renderLogin();
            return;
        }
        app.innerHTML = `
            <div class="mobile-header">
                <button class="hamburger" data-click="toggleSidebar" aria-label="Menu">☰</button>
                <div class="status-dot" style="background: ${state.botInfo?.status === 'online' ? 'var(--success)' : 'var(--warning)'}"></div>
                <h1>Michel Bot</h1>
            </div>
            <div class="sidebar-overlay" data-click="closeSidebar"></div>
            <div class="app">
                ${renderSidebar()}
                <div class="main">
                    ${renderPage()}
                </div>
            </div>
        `;

        // Post-render hooks
        if (state.currentPage === 'logs') {
            const logContainer = document.getElementById('log-container');
            if (logContainer) {
                logContainer.addEventListener('scroll', handleLogScroll);
                if (state.logAutoScroll) {
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
            }
        }
        // Populate any channel / member pickers on the current page
        if (state.currentServerId) {
            populateChannelPickers(document);
            populateMemberPickers(document);
            refreshAiModelSelects(document);
        }
        // Server page: the messages the bot keeps up to date there.
        if (state.currentPage === 'server' && state.currentServerId) {
            loadBotMessages(state.currentServerId);
        }
        // Reaction-roles builder: load existing menus when on its module page.
        if (state.currentPage === 'module' && state.currentModule === 'moduleReactionRoles' && state.currentServerId) {
            loadRoleMenus(state.currentServerId);
        }
        // Moderation: load infraction history when on its module page.
        if (state.currentPage === 'module' && state.currentModule === 'moduleModeration' && state.currentServerId) {
            loadInfractions(state.currentServerId);
        }
        // Spotify: draw the playlist / vote charts when on its module page.
        if (state.currentPage === 'module' && state.currentModule === 'moduleSpotify' && state.currentServerId) {
            loadSpotifyStats(state.currentServerId);
        }
        // XP: load activity stats and draw the charts when on its module page.
        if (state.currentPage === 'module' && state.currentModule === 'moduleXp' && state.currentServerId) {
            loadXpStats(state.currentServerId);
        }
    }

    function renderLogin() {
        return `
            <div class="login-page">
                <div class="login-card">
                    <h1>🤖 Michel Bot</h1>
                    <p>Connectez-vous avec Discord pour accéder au dashboard de configuration.</p>
                    <a href="/auth/login" class="login-btn">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z"/>
                        </svg>
                        Se connecter avec Discord
                    </a>
                </div>
            </div>
        `;
    }

    function renderSidebar() {
        const serverItems = state.servers
            ? Object.entries(state.servers).map(([id, s]) => {
                const isActive = (state.currentPage === 'server' || state.currentPage === 'module') && state.currentServerId === id;
                const iconUrl = s.icon
                    ? `https://cdn.discordapp.com/icons/${id}/${s.icon}.png?size=64`
                    : null;
                return `
                    <div class="nav-item ${isActive ? 'active' : ''}" data-click="navigate" data-click-args="${actionArgs("server", id)}">
                        <div class="server-icon">
                            ${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="">` : escapeHtml(s.name?.charAt(0) || '?')}
                        </div>
                        <div class="nav-item-label">
                            <span class="nav-item-name">${escapeHtml(s.name || id)}</span>
                            <span class="nav-item-sub">${id}</span>
                        </div>
                    </div>
                `;
            }).join('')
            : '<div class="loading"><div class="spinner"></div> Chargement...</div>';

        return `
            <div class="sidebar ${state.sidebarOpen ? 'open' : ''}">
                <div class="sidebar-header">
                    <div class="status-dot" style="background: ${state.botInfo?.status === 'online' ? 'var(--success)' : 'var(--warning)'}"></div>
                    <h1>Michel Bot</h1>
                </div>
                <div class="sidebar-nav">
                    <div class="nav-section">
                        <div class="nav-section-title">Général</div>
                        <div class="nav-item ${state.currentPage === 'home' ? 'active' : ''}" data-click="navigate" data-click-args="[&quot;home&quot;]">
                            <span class="icon">🏠</span> Accueil
                        </div>
                        ${isDeveloper() ? `
                        <div class="nav-item ${state.currentPage === 'global' ? 'active' : ''}" data-click="navigate" data-click-args="[&quot;global&quot;]">
                            <span class="icon">⚙️</span> Config globale
                        </div>
                        <div class="nav-item ${state.currentPage === 'extensions' ? 'active' : ''}" data-click="navigate" data-click-args="[&quot;extensions&quot;]">
                            <span class="icon">🧩</span> Extensions
                        </div>
                        <div class="nav-item ${state.currentPage === 'links' ? 'active' : ''}" data-click="navigate" data-click-args="[&quot;links&quot;]">
                            <span class="icon">🔗</span> Liens courts
                        </div>
                        <div class="nav-item ${state.currentPage === 'logs' ? 'active' : ''}" data-click="navigate" data-click-args="[&quot;logs&quot;]">
                            <span class="icon">📋</span> Logs
                        </div>
                        ` : ''}
                    </div>
                    ${isAdmin() ? `
                    <div class="nav-section">
                        <div class="nav-section-title">Serveurs</div>
                        ${serverItems}
                    </div>
                    ` : ''}
                </div>
                <div class="sidebar-footer">
                    <div class="user-info">
                        <div class="user-avatar">
                            ${state.user.avatar
                                ? `<img src="https://cdn.discordapp.com/avatars/${encodeURIComponent(state.user.user_id)}/${encodeURIComponent(state.user.avatar)}.png?size=64" alt="">`
                                : escapeHtml(state.user.username.charAt(0))
                            }
                        </div>
                        <span class="user-name">${escapeHtml(state.user.username)}</span>
                        <button class="logout-btn" data-click="logout" title="Déconnexion">🚪</button>
                    </div>
                </div>
            </div>
        `;
    }

    function renderPage() {
        switch (state.currentPage) {
            case 'home': return renderHomePage();
            case 'global': return renderGlobalConfig();
            case 'extensions': return renderExtensionsPage();
            case 'server': return renderServerPage();
            case 'module': return renderModulePage();
            case 'links': return renderShortLinksPage();
            case 'logs': return renderLogsPage();
            default: return renderHomePage();
        }
    }

    // ── Short Links (Shlink) ──────────────────────────
    async function loadShortLinks() {
        state.shlink.loading = true;
        try {
            state.shlink.status = await api('/api/shlink/status');
            if (!state.shlink.status?.configured) {
                state.shlink.items = [];
                state.shlink.pagination = {};
                return;
            }
            const params = new URLSearchParams();
            params.set('page', String(state.shlink.page || 1));
            if (state.shlink.search) params.set('search', state.shlink.search);
            const data = await api(`/api/shlink/short-urls?${params}`);
            state.shlink.items = data?.short_urls || [];
            state.shlink.pagination = data?.pagination || {};
        } catch (e) {
            state.shlink.items = [];
            state.shlink.pagination = {};
        } finally {
            state.shlink.loading = false;
        }
    }

    function renderShortLinkRow(item) {
        const editing = state.shlink.editing === item.short_code;
        const code = escapeHtml(item.short_code);
        if (editing) {
            return `
                <div style="border:1px solid var(--accent); border-radius:var(--radius); padding:12px; margin-bottom:8px; display:flex; flex-direction:column; gap:8px;">
                    <div><a href="${escapeHtml(item.short_url)}" target="_blank" rel="noopener">${escapeHtml(item.short_url)}</a></div>
                    <div>
                        <label class="form-label">URL de destination</label>
                        <input type="url" class="form-input" id="shlink-edit-url" value="${escapeHtml(item.long_url)}">
                    </div>
                    <div>
                        <label class="form-label">Titre</label>
                        <input type="text" class="form-input" id="shlink-edit-title" value="${escapeHtml(item.title)}" placeholder="Optionnel">
                    </div>
                    <div>
                        <label class="form-label">Tags (séparés par des virgules)</label>
                        <input type="text" class="form-input" id="shlink-edit-tags" value="${escapeHtml((item.tags || []).join(', '))}">
                    </div>
                    <div style="display:flex; gap:6px; justify-content:flex-end;">
                        <button class="btn btn-ghost btn-sm" data-click="cancelEditShortLink">Annuler</button>
                        <button class="btn btn-success btn-sm" data-click="saveShortLink" data-click-args="${actionArgs(code)}">💾 Enregistrer</button>
                    </div>
                </div>
            `;
        }
        const tags = (item.tags || []).map(t =>
            `<span style="background:var(--bg-tertiary); border-radius:var(--radius); padding:2px 8px; font-size:12px; color:var(--text-secondary);">${escapeHtml(t)}</span>`
        ).join(' ');
        return `
            <div style="border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:8px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                    <div style="min-width:0;">
                        <a href="${escapeHtml(item.short_url)}" target="_blank" rel="noopener"><strong>${escapeHtml(item.short_url)}</strong></a>
                        ${item.title ? `<span style="color:var(--text-muted); margin-left:8px;">${escapeHtml(item.title)}</span>` : ''}
                    </div>
                    <div style="display:flex; gap:6px; align-items:center;">
                        <span style="color:var(--text-muted); font-size:12px;">${item.visits ?? 0} visite(s)</span>
                        <button class="btn btn-ghost btn-sm" data-click="copyShortLink" data-click-args="${actionArgs(item.short_url)}" title="Copier">📋</button>
                        <button class="btn btn-ghost btn-sm" data-click="startEditShortLink" data-click-args="${actionArgs(code)}">✏️ Modifier</button>
                        <button class="btn btn-ghost btn-sm" style="color:var(--danger)" data-click="deleteShortLink" data-click-args="${actionArgs(code)}">🗑️</button>
                    </div>
                </div>
                <div style="font-size:13px; color:var(--text-secondary); word-break:break-all;">↳ ${escapeHtml(item.long_url)}</div>
                ${tags ? `<div style="display:flex; gap:4px; flex-wrap:wrap;">${tags}</div>` : ''}
            </div>
        `;
    }

    function renderShortLinksPage() {
        const s = state.shlink;
        const st = s.status || {};
        if (!st.configured) {
            return `
                <div class="page-header">
                    <h2>Liens courts</h2>
                    <p>Raccourcisseur Shlink — création, modification et suppression des liens publiés par le bot.</p>
                </div>
                <div class="empty-state">
                    <div class="icon">🔗</div>
                    <h3>Shlink n'est pas configuré</h3>
                    <p>Renseignez l'URL de l'instance et la clé API dans la section « Shlink » de la
                    <a href="#" data-click="navigate" data-click-args="[&quot;global&quot;]" data-prevent>config globale</a>.</p>
                </div>
            `;
        }
        const pag = s.pagination || {};
        const currentPage = pag.currentPage || s.page || 1;
        const pagesCount = pag.pagesCount || 1;
        return `
            <div class="page-header">
                <h2>Liens courts</h2>
                <p>
                    Instance : <a href="${escapeHtml(st.base_url)}" target="_blank" rel="noopener">${escapeHtml(st.base_url)}</a> ·
                    Raccourcissement automatique des liens du bot :
                    ${st.enabled
                        ? '<span style="color:var(--success)">activé</span>'
                        : '<span style="color:var(--text-muted)">désactivé</span>'}
                    (<a href="#" data-click="navigate" data-click-args="[&quot;global&quot;]" data-prevent>config globale</a>)
                </p>
            </div>

            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Nouveau lien court</div>
                        <div class="card-subtitle">Le slug est généré automatiquement s'il est laissé vide.</div>
                    </div>
                </div>
                <div style="display:flex; flex-direction:column; gap:12px;">
                    <div>
                        <label class="form-label">URL de destination</label>
                        <input type="url" class="form-input" id="shlink-new-url" placeholder="https://exemple.com/une-page-tres-longue">
                    </div>
                    <div style="display:flex; gap:12px; flex-wrap:wrap;">
                        <div style="flex:1; min-width:180px;">
                            <label class="form-label">Titre</label>
                            <input type="text" class="form-input" id="shlink-new-title" placeholder="Optionnel">
                        </div>
                        <div style="flex:1; min-width:180px;">
                            <label class="form-label">Slug personnalisé</label>
                            <input type="text" class="form-input" id="shlink-new-slug" placeholder="ma-page">
                        </div>
                        <div style="flex:1; min-width:180px;">
                            <label class="form-label">Tags (séparés par des virgules)</label>
                            <input type="text" class="form-input" id="shlink-new-tags" placeholder="${escapeHtml((st.default_tags || []).join(', '))}">
                        </div>
                    </div>
                    <div style="display:flex; justify-content:flex-end;">
                        <button class="btn btn-primary btn-sm" data-click="createShortLink">➕ Créer</button>
                    </div>
                </div>
            </div>

            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">Liens existants</div>
                        <div class="card-subtitle">${pag.totalItems ?? s.items.length} lien(s) · page ${currentPage} / ${pagesCount}</div>
                    </div>
                    <div style="display:flex; gap:8px;">
                        <input type="text" class="form-input" id="shlink-search" style="width:200px;"
                            placeholder="Rechercher…" value="${escapeHtml(s.search || '')}"
                            data-keydown="search-on-enter" data-keydown-args="[{&quot;$&quot;:&quot;event&quot;}]">
                        <button class="btn btn-primary btn-sm" data-click="searchShortLinks">🔍</button>
                    </div>
                </div>
                ${s.loading
                    ? '<div class="loading"><div class="spinner"></div></div>'
                    : s.items.length === 0
                        ? `<div class="empty-state"><h3>Aucun lien</h3><p>Créez-en un avec le formulaire ci-dessus.</p></div>`
                        : s.items.map(renderShortLinkRow).join('')}
                ${pagesCount > 1 ? `
                    <div style="display:flex; gap:8px; justify-content:center; margin-top:12px;">
                        <button class="btn btn-ghost btn-sm" ${currentPage <= 1 ? 'disabled' : ''} data-click="gotoShortLinkPage" data-click-args="${actionArgs(currentPage - 1)}">← Précédent</button>
                        <button class="btn btn-ghost btn-sm" ${currentPage >= pagesCount ? 'disabled' : ''} data-click="gotoShortLinkPage" data-click-args="${actionArgs(currentPage + 1)}">Suivant →</button>
                    </div>
                ` : ''}
            </div>
        `;
    }

    function _shlinkTags(value) {
        return (value || '').split(',').map(t => t.trim()).filter(Boolean);
    }

    async function createShortLink() {
        const url = document.getElementById('shlink-new-url')?.value.trim();
        if (!url) {
            toast('URL de destination requise', 'error');
            return;
        }
        try {
            await api('/api/shlink/short-urls', {
                method: 'POST',
                body: JSON.stringify({
                    long_url: url,
                    title: document.getElementById('shlink-new-title')?.value.trim() || null,
                    custom_slug: document.getElementById('shlink-new-slug')?.value.trim() || null,
                    tags: _shlinkTags(document.getElementById('shlink-new-tags')?.value),
                }),
            });
            toast('Lien court créé', 'success');
            state.shlink.page = 1;
            await loadShortLinks();
            render();
        } catch (e) { /* handled by api() */ }
    }

    function startEditShortLink(shortCode) {
        state.shlink.editing = shortCode;
        render();
    }

    function cancelEditShortLink() {
        state.shlink.editing = null;
        render();
    }

    async function saveShortLink(shortCode) {
        const url = document.getElementById('shlink-edit-url')?.value.trim();
        if (!url) {
            toast('URL de destination requise', 'error');
            return;
        }
        try {
            await api(`/api/shlink/short-urls/${encodeURIComponent(shortCode)}`, {
                method: 'PATCH',
                body: JSON.stringify({
                    long_url: url,
                    title: document.getElementById('shlink-edit-title')?.value.trim() || '',
                    tags: _shlinkTags(document.getElementById('shlink-edit-tags')?.value),
                }),
            });
            toast('Lien court mis à jour', 'success');
            state.shlink.editing = null;
            await loadShortLinks();
            render();
        } catch (e) { /* handled by api() */ }
    }

    async function deleteShortLink(shortCode) {
        if (!confirm(`Supprimer le lien court « ${shortCode} » ? Les visiteurs recevront une erreur 404.`)) return;
        try {
            await api(`/api/shlink/short-urls/${encodeURIComponent(shortCode)}`, { method: 'DELETE' });
            toast('Lien court supprimé', 'success');
            await loadShortLinks();
            render();
        } catch (e) { /* handled by api() */ }
    }

    async function searchShortLinks() {
        state.shlink.search = document.getElementById('shlink-search')?.value.trim() || '';
        state.shlink.page = 1;
        await loadShortLinks();
        render();
    }

    async function gotoShortLinkPage(page) {
        state.shlink.page = Math.max(1, page);
        await loadShortLinks();
        render();
    }

    async function copyShortLink(shortUrl) {
        try {
            await navigator.clipboard.writeText(shortUrl);
            toast('Lien copié', 'success');
        } catch (e) {
            toast('Copie impossible', 'error');
        }
    }

    // ── Home Page ─────────────────────────────────────
    function renderHomePage() {
        const bi = state.botInfo || {};
        const admin = isAdmin();

        // Latency color coding
        const lat = bi.latency;
        const latColor = lat == null ? 'var(--text-primary)'
            : lat < 100 ? 'var(--success)'
            : lat < 200 ? 'var(--warning)'
            : 'var(--danger)';

        // ── Hero ──────────────────────────────────────
        const heroHtml = `
            <div class="home-hero">
                <div class="home-hero-bot">
                    <div class="bot-avatar">🤖</div>
                    <div>
                        <div class="bot-name">${escapeHtml(bi.user || 'Michel Bot')}</div>
                        ${state.user ? `<div class="bot-tag">Connecté en tant que ${escapeHtml(state.user.username)}</div>` : ''}
                    </div>
                </div>
                <div class="home-hero-right">
                    ${admin ? `
                    <div class="hero-stat">
                        <div class="hero-stat-value">${bi.guilds ?? '—'}</div>
                        <div class="hero-stat-label">Serveurs</div>
                    </div>
                    <div class="hero-stat">
                        <div class="hero-stat-value" style="color:${latColor}">${lat != null ? lat + ' ms' : '—'}</div>
                        <div class="hero-stat-label">Latence</div>
                    </div>
                    <div class="hero-stat">
                        <div class="hero-stat-value">${bi.extensions?.length ?? '—'}</div>
                        <div class="hero-stat-label">Extensions</div>
                    </div>
                    ` : `
                    <div class="hero-stat">
                        <div class="hero-stat-value" style="color:${latColor}">${lat != null ? lat + ' ms' : '—'}</div>
                        <div class="hero-stat-label">Latence</div>
                    </div>
                    `}
                    <span class="status-badge ${bi.status === 'online' ? 'online' : 'offline'}">
                        <span class="status-dot-pulse"></span>
                        ${bi.status === 'online' ? 'En ligne' : bi.status || 'Inconnu'}
                    </span>
                </div>
            </div>
        `;

        // ── Non-admin view ─────────────────────────────
        if (!admin) {
            return heroHtml + `
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title">Bienvenue, ${escapeHtml(state.user.username)} !</div>
                            <div class="card-subtitle">Vous êtes connecté en tant qu'utilisateur. Seuls les administrateurs peuvent modifier la configuration du bot.</div>
                        </div>
                    </div>
                </div>
            `;
        }

        // ── Server Cards ───────────────────────────────
        const servers = state.servers ? Object.entries(state.servers) : [];
        const serverCardsHtml = servers.length > 0 ? `
            <div class="section-label">Serveurs</div>
            <div class="server-cards-grid">
                ${servers.map(([id, s]) => {
                    const iconUrl = s.icon
                        ? `https://cdn.discordapp.com/icons/${id}/${s.icon}.png?size=128`
                        : null;
                    const cfg = s.config || {};
                    const modCount = Object.keys(cfg).length;
                    const activeCount = Object.values(cfg).filter(m => m && m.enabled === true).length;
                    return `
                        <div class="server-card" data-click="navigate" data-click-args="${actionArgs("server", id)}">
                            <div class="server-card-top">
                                <div class="server-card-icon">
                                    ${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="">` : `<span>${escapeHtml(s.name?.charAt(0) || '?')}</span>`}
                                </div>
                                <div style="min-width:0;flex:1;">
                                    <div class="server-card-name" title="${escapeHtml(s.name || id)}">${escapeHtml(s.name || id)}</div>
                                    <div class="server-card-id">${id}</div>
                                </div>
                            </div>
                            <div class="server-card-footer">
                                <div class="server-card-pills">
                                    <span class="server-card-pill modules">${modCount} module${modCount !== 1 ? 's' : ''}</span>
                                    ${activeCount > 0 ? `<span class="server-card-pill active">${activeCount} actif${activeCount !== 1 ? 's' : ''}</span>` : ''}
                                </div>
                                <span class="server-card-arrow">→</span>
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        ` : '';

        // ── Quick Actions ──────────────────────────────
        const loadedExts = (state.extensions || []).filter(e => e.loaded).length;
        const quickActionsHtml = !isDeveloper() ? '' : `
            <div class="section-label">Actions rapides</div>
            <div class="quick-actions-grid">
                <button class="quick-action-btn" data-click="navigate" data-click-args="[&quot;global&quot;]">
                    <span class="quick-action-icon">⚙️</span>
                    <span class="quick-action-label">Config globale</span>
                    <span class="quick-action-sub">Tokens, APIs…</span>
                </button>
                <button class="quick-action-btn" data-click="navigate" data-click-args="[&quot;extensions&quot;]">
                    <span class="quick-action-icon">🧩</span>
                    <span class="quick-action-label">Extensions</span>
                    <span class="quick-action-sub">${loadedExts} chargée${loadedExts !== 1 ? 's' : ''}</span>
                </button>
                <button class="quick-action-btn" id="reload-all-btn" data-click="reloadAllExtensions">
                    <span class="quick-action-icon">🔄</span>
                    <span class="quick-action-label">Recharger tout</span>
                    <span class="quick-action-sub">Extensions actives</span>
                </button>
                <button class="quick-action-btn" data-click="navigate" data-click-args="[&quot;logs&quot;]">
                    <span class="quick-action-icon">📋</span>
                    <span class="quick-action-label">Logs</span>
                    <span class="quick-action-sub">Temps réel</span>
                </button>
                <button class="quick-action-btn" data-click="downloadConfig">
                    <span class="quick-action-icon">⬇️</span>
                    <span class="quick-action-label">Exporter</span>
                    <span class="quick-action-sub">config.json</span>
                </button>
                <button class="quick-action-btn" data-click="cleanupConfig">
                    <span class="quick-action-icon">🧹</span>
                    <span class="quick-action-label">Nettoyer</span>
                    <span class="quick-action-sub">Clés obsolètes</span>
                </button>
            </div>
        `;

        // ── Mini Log Feed ──────────────────────────────
        const levelColors = { DEBUG: '#8b949e', INFO: '#58a6ff', WARNING: '#d29922', ERROR: '#f85149', CRITICAL: '#ff7b72' };
        const recentLogs = isDeveloper() && state.logs.length > 0 ? [...state.logs].reverse().slice(0, 10) : [];
        const miniLogsHtml = recentLogs.length > 0 ? `
            <div class="section-label">Activité récente</div>
            <div class="card" style="margin-bottom:0;">
                <div class="card-header">
                    <div>
                        <div class="card-title">Dernières entrées de log</div>
                        <div class="card-subtitle">${state.logs.length} entrée(s) en mémoire</div>
                    </div>
                    <button class="btn btn-ghost btn-sm" data-click="navigate" data-click-args="[&quot;logs&quot;]">Voir tout →</button>
                </div>
                <div style="background:#0d1117;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
                    <div class="mini-log-feed">
                        ${recentLogs.map(entry => {
                            const time = new Date(entry.timestamp * 1000).toLocaleTimeString('fr-FR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
                            const rowClass = entry.level === 'ERROR' || entry.level === 'CRITICAL' ? 'has-error'
                                : entry.level === 'WARNING' ? 'has-warning' : '';
                            return `
                                <div class="mini-log-line ${rowClass}">
                                    <span class="mini-log-time">${time}</span>
                                    <span class="mini-log-level" style="color:${levelColors[entry.level] || '#c9d1d9'}">${entry.level}</span>
                                    <span class="mini-log-msg" title="${escapeHtml(entry.message)}">${escapeHtml(entry.message)}</span>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            </div>
        ` : '';

        return heroHtml + serverCardsHtml + quickActionsHtml + miniLogsHtml;
    }

    // ── Global Config Page ────────────────────────────
    function renderGlobalConfig() {
        const globalConfig = state.config?.config || {};
        const allSections = new Set([
            ...Object.keys(globalConfig),
            ...Object.keys(state.globalSchemas),
        ]);
        const sections = [...allSections].sort();

        return `
            <div class="page-header">
                <h2>Configuration globale</h2>
                <p>Paramètres partagés entre tous les serveurs</p>
            </div>

            <div class="card" style="margin-bottom:16px; border: 1px solid var(--border);">
                <div class="card-header">
                    <div>
                        <div class="card-title">🗂️ Fichier de configuration</div>
                        <div class="card-subtitle">Toute la config est stockée dans <code>config/config.json</code></div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-danger btn-sm" data-click="cleanupConfig" title="Supprimer les clés non définies dans les schémas">
                            🧹 Nettoyer la config
                        </button>
                        <button class="btn btn-ghost btn-sm" data-click="downloadConfig">
                            ⬇️ Exporter
                        </button>
                    </div>
                </div>
            </div>

            ${sections.length === 0 ? `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <h3>Aucune configuration</h3>
                    <p>La configuration globale est vide.</p>
                </div>
            ` : sections.map(section => {
                const schema = state.globalSchemas[section];
                const sectionData = globalConfig[section] || {};
                const sectionLabel = schema?.label || section;
                const sectionIcon = schema?.icon || '📁';
                const fieldCount = Object.keys(schema?.fields || sectionData).length;

                return `
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <div class="card-title">${sectionIcon} ${sectionLabel}</div>
                                <div class="card-subtitle">${fieldCount} paramètre(s)</div>
                            </div>
                            <div class="card-actions">
                                <button class="btn btn-ghost btn-sm" data-click="editGlobalSectionJson" data-click-args="${actionArgs(section)}">
                                    { } JSON
                                </button>
                                <button class="btn btn-primary btn-sm" data-click="editGlobalSection" data-click-args="${actionArgs(section)}">
                                    ✏️ Éditer
                                </button>
                            </div>
                        </div>
                        ${schema ? renderSchemaPreview(schema, sectionData) : renderConfigPreview(sectionData)}
                        ${section === 'spotify' ? renderSpotifyAuthPanel() : ''}
                    </div>
                `;
            }).join('')}
        `;
    }

    // ── Extensions Page ───────────────────────────────
    function renderExtensionsPage() {
        const exts = state.extensions;
        return `
            <div class="page-header">
                <h2>Extensions</h2>
                <p>Activez ou désactivez les extensions globalement. Les modifications sont appliquées immédiatement et persistées au redémarrage.</p>
            </div>

            ${exts.length === 0 ? `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <h3>Aucune extension trouvée</h3>
                    <p>Vérifiez le dossier <code>extensions/</code>.</p>
                </div>
            ` : `
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="card-title">Extensions disponibles</div>
                            <div class="card-subtitle">${exts.filter(e => e.loaded).length} chargée(s) / ${exts.length} disponible(s)</div>
                        </div>
                    </div>
                    <div class="module-list">
                        ${exts.map(ext => {
                            const isUnderscored = ext.filename.startsWith('_');
                            return `
                                <div class="module-item">
                                    <div class="module-info">
                                        <span class="module-name">📦 ${ext.filename}</span>
                                        <span class="module-status">
                                            ${ext.loaded
                                                ? '<span style="color:var(--success)">● chargée</span>'
                                                : '<span style="color:var(--text-muted)">○ non chargée</span>'}
                                            ${isUnderscored ? ' · désactivée par défaut' : ''}
                                        </span>
                                    </div>
                                    <label class="toggle" data-click="stop">
                                        <input type="checkbox" ${ext.enabled ? 'checked' : ''}
                                            data-change="toggleExtension" data-change-args="${actionArgs(ext.path, CHECKED)}">
                                        <span class="toggle-slider"></span>
                                    </label>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `}
        `;
    }

    async function toggleExtension(extPath, enabled) {
        try {
            const result = await api(`/api/extensions/${extPath}/toggle`, {
                method: 'POST',
                body: JSON.stringify({ enabled }),
            });
            if (result?.error) {
                toast(`${extPath} ${enabled ? 'activée' : 'désactivée'} dans la config, mais erreur : ${result.error}`, 'warning');
            } else {
                const status = result?.loaded ? 'chargée' : 'déchargée';
                const syncSuffix = commandSyncSuffix(result);
                const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
                toast(`${extPath} ${enabled ? 'activée' : 'désactivée'} et ${status}${syncSuffix}`, syncFailed ? 'warning' : 'success');
            }
            await loadExtensions();
            await loadBotInfo();
            render();
        } catch (e) { /* handled by api() */ }
    }

    function renderSchemaPreview(schema, values) {
        const fields = schema.fields || {};
        let html = '<div class="schema-preview">';
        for (const [key, field] of Object.entries(fields)) {
            if (field.hidden) continue;
            const val = values[key];
            const hasVal = val !== undefined && val !== null;
            const isSecret = field.secret || false;
            const fieldType = field.type || 'string';

            if (fieldType === 'messagelist' && Array.isArray(val) && val.length > 0) {
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${val.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${val.map((msg) => {
                                const text = typeof msg === 'string' ? msg : String(msg?.text ?? '');
                                const w = (msg && typeof msg === 'object' && msg.weight !== undefined) ? msg.weight : 1;
                                return `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="flex:1;color:var(--text-primary);">${escapeHtml(text)}</span>
                                    <span style="color:var(--text-muted);font-size:12px;">×${escapeHtml(String(w))}</span>
                                </div>`;
                            }).join('')}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'models' && Array.isArray(val) && val.length > 0) {
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${val.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${val.map(m => `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="font-weight:600;color:var(--accent);min-width:80px;">${escapeHtml(m.provider || '')}</span>
                                    <span style="color:var(--text-muted);font-family:Consolas,monospace;flex:1;">${escapeHtml(m.model_id || '')}</span>
                                    <span style="color:var(--text-secondary);">${escapeHtml(m.display_name || '')}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'discord2name' && typeof val === 'object' && val !== null && !Array.isArray(val)) {
                const entries = Object.entries(val);
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${entries.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${entries.length > 0 ? entries.map(([id, name]) => `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="color:var(--text-muted);font-family:Consolas,monospace;min-width:180px;">${escapeHtml(id)}</span>
                                    <span style="font-weight:600;color:var(--accent);">${escapeHtml(String(name))}</span>
                                </div>
                            `).join('') : '<span style="color:var(--text-muted);font-size:13px;">Aucun mapping configuré</span>'}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'teams' && Array.isArray(val) && val.length > 0) {
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${val.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${val.map(t => `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="font-weight:600;color:var(--accent);min-width:120px;">${escapeHtml(t.name || '???')}</span>
                                    <span style="color:var(--text-muted);min-width:100px;">${escapeHtml(t.game || 'valorant')}</span>
                                    ${t.vlrTeamId ? `<span style="color:var(--text-secondary);font-family:Consolas,monospace;">VLR#${escapeHtml(t.vlrTeamId)}</span>` : ''}
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'spotifymap' && Array.isArray(val) && val.length > 0) {
                const nameMap = getDiscord2NameMap();
                const known = _memberCache[state.currentServerId]?.byId || new Map();
                const resolveName = (u) => {
                    if (u.name) return u.name;
                    if (u.discordId && nameMap[u.discordId]) return nameMap[u.discordId];
                    const m = known.get(String(u.discordId));
                    return m ? (m.display_name || m.username || '') : '';
                };
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${val.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${val.map(u => `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="color:var(--text-muted);font-family:Consolas,monospace;min-width:120px;">${escapeHtml(u.spotifyId || '???')}</span>
                                    <span style="font-weight:600;color:var(--accent);min-width:100px;">${escapeHtml(resolveName(u))}</span>
                                    <span style="color:var(--text-secondary);font-family:Consolas,monospace;">${escapeHtml(u.discordId || '')}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'rssfeedmap' && typeof val === 'object' && val !== null && !Array.isArray(val)) {
                const feeds = Object.entries(val);
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${feeds.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${feeds.length > 0 ? feeds.map(([feedId, cfg]) => {
                                const c = (typeof cfg === 'string') ? { url: cfg } : (cfg || {});
                                const label = c.label ? ` — ${escapeHtml(String(c.label))}` : '';
                                const modeBadge = (c.mode === 'embed') ? ' <span style="color:var(--accent);font-size:11px;">[embed]</span>' : '';
                                return `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="font-weight:600;color:var(--accent);min-width:140px;">${escapeHtml(feedId)}${label}${modeBadge}</span>
                                    <span style="color:var(--text-muted);font-family:Consolas,monospace;flex:1;">${escapeHtml(String(c.url || '—'))}</span>
                                </div>`;
                            }).join('') : '<span style="color:var(--text-muted);font-size:13px;">Aucun flux configuré</span>'}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'youtubechannelmap' && typeof val === 'object' && val !== null && !Array.isArray(val)) {
                const channels = Object.entries(val);
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${channels.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${channels.length > 0 ? channels.map(([handle, cfg]) => {
                                const flags = [];
                                if (cfg?.shorts) flags.push('Shorts');
                                if (cfg?.live) flags.push('Live');
                                if (cfg?.vod !== false) flags.push('VOD');
                                const label = cfg?.label ? ` (${escapeHtml(String(cfg.label))})` : '';
                                return `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="font-weight:600;color:var(--accent);min-width:140px;">${escapeHtml(handle)}${label}</span>
                                    <span style="color:var(--text-muted);font-family:Consolas,monospace;flex:1;">${flags.join(' · ') || '—'}</span>
                                </div>`;
                            }).join('') : '<span style="color:var(--text-muted);font-size:13px;">Aucune chaîne configurée</span>'}
                        </div>
                    </div>
                `;
                continue;
            }

            if (fieldType === 'streamermap' && typeof val === 'object' && val !== null && !Array.isArray(val)) {
                const streamers = Object.entries(val);
                html += `
                    <div class="preview-row" style="flex-direction:column;gap:6px;">
                        <span class="preview-label">${field.label || key} (${streamers.length})</span>
                        <div style="display:flex;flex-direction:column;gap:4px;width:100%;">
                            ${streamers.length > 0 ? streamers.map(([login, cfg]) => {
                                const pc = cfg?.twitchPlanningChannelId ? `planning #${escapeHtml(String(cfg.twitchPlanningChannelId))}` : '';
                                const nc = cfg?.twitchNotificationChannelId ? `notif #${escapeHtml(String(cfg.twitchNotificationChannelId))}` : '';
                                const extras = [pc, nc].filter(Boolean).join(' · ');
                                return `
                                <div style="display:flex;gap:8px;align-items:center;padding:4px 8px;background:var(--bg-primary);border-radius:var(--radius);font-size:13px;">
                                    <span style="font-weight:600;color:var(--accent);min-width:120px;">${escapeHtml(login)}</span>
                                    <span style="color:var(--text-muted);font-family:Consolas,monospace;flex:1;">${extras || '—'}</span>
                                </div>`;
                            }).join('') : '<span style="color:var(--text-muted);font-size:13px;">Aucun streamer configuré</span>'}
                        </div>
                    </div>
                `;
                continue;
            }

            let displayVal = '—';
            if (hasVal) {
                if (isSecret) displayVal = '••••••••';
                else if (typeof val === 'boolean') displayVal = val ? '✅ Oui' : '❌ Non';
                else if (Array.isArray(val)) displayVal = val.join(', ');
                else if (typeof val === 'object') displayVal = JSON.stringify(val);
                else displayVal = String(val);
            }
            html += `
                <div class="preview-row">
                    <span class="preview-label">${field.label || key}</span>
                    <span class="preview-value ${!hasVal ? 'empty' : ''}">${escapeHtml(displayVal)}</span>
                </div>
            `;
        }
        for (const key of Object.keys(values)) {
            if (!(key in fields)) {
                html += `
                    <div class="preview-row">
                        <span class="preview-label" style="color:var(--warning)">${escapeHtml(key)} <small>(non défini dans le schéma)</small></span>
                        <span class="preview-value">${escapeHtml(JSON.stringify(values[key]))}</span>
                    </div>
                `;
            }
        }
        html += '</div>';
        return html;
    }

    function renderConfigPreview(obj, prefix = '') {
        let html = '<div class="kv-grid">';
        for (const [key, value] of Object.entries(obj)) {
            const fullKey = prefix ? `${prefix}.${key}` : key;
            if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
                html += `<div class="kv-key" style="grid-column: 1 / -1; color: var(--accent); margin-top: 8px;">▸ ${key}</div>`;
                html += renderConfigPreview(value, fullKey);
            } else {
                const displayValue = typeof value === 'string' && (key.toLowerCase().includes('token') || key.toLowerCase().includes('secret') || key.toLowerCase().includes('password'))
                    ? '••••••••'
                    : JSON.stringify(value);
                html += `
                    <div class="kv-key">${key}</div>
                    <div class="kv-value"><input type="text" value="${escapeHtml(displayValue)}" readonly></div>
                `;
            }
        }
        html += '</div>';
        return html;
    }

    async function editGlobalSection(section) {
        const globalConfig = state.config?.config || {};
        const sectionData = globalConfig[section] || {};
        const schema = state.globalSchemas[section];

        if (schema) {
            const formId = `global-${section}`;
            const formHtml = renderSchemaForm(formId, schema, sectionData);
            document.getElementById('modal-title').textContent = `${schema.label || section}`;
            document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = formHtml;
            refreshAiModelSelects(document.getElementById('modal-overlay'));
            const errEl = document.getElementById('json-error');
            if (errEl) errEl.textContent = '';
            modalSaveCallback = async () => {
                const values = collectFormValues(formId);
                const merged = { ...sectionData, ...values };
                await api(`/api/global-config/${section}`, {
                    method: 'PUT',
                    body: JSON.stringify({ section, config: merged }),
                });
                // The per-guild default-model dropdowns list the global models.
                for (const sid in _aiGuildModelsCache) delete _aiGuildModelsCache[sid];
                await loadConfig();
                render();
            };
            document.getElementById('modal-overlay').classList.add('active');
        } else {
            editGlobalSectionJson(section);
        }
    }

    async function editGlobalSectionJson(section) {
        const globalConfig = state.config?.config || {};
        const sectionData = globalConfig[section] || {};
        document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = `
            <textarea class="json-editor" id="json-editor" spellcheck="false"></textarea>
            <div class="json-error" id="json-error"></div>
        `;
        openModal(`Éditer JSON : ${section}`, sectionData, async (newData) => {
            await api(`/api/global-config/${section}`, {
                method: 'PUT',
                body: JSON.stringify({ section, config: newData }),
            });
            await loadConfig();
            render();
        });
    }

    // ── Server Page ───────────────────────────────────
    function renderServerPage() {
        const sid = state.currentServerId;
        const server = state.servers?.[sid];
        if (!server) {
            return `<div class="loading"><div class="spinner"></div> Chargement...</div>`;
        }
        const serverConfig = server.config || {};
        // Module configs are objects; guild-level scalars that sit next to them
        // (e.g. publicDashboardToken) are not modules.
        const configuredModules = Object.keys(serverConfig).filter(k => {
            const v = serverConfig[k];
            return v && typeof v === 'object' && !Array.isArray(v);
        });
        const allModuleNames = new Set([
            ...configuredModules,
            ...Object.keys(state.moduleSchemas),
        ]);
        const moduleNames = [...allModuleNames].sort();

        const CATEGORY_ORDER = ['Communauté', 'Médias & Streaming', 'Esport & Jeux', 'Événements', 'Outils'];
        const grouped = {};
        for (const mod of moduleNames) {
            const cat = state.moduleSchemas[mod]?.category || 'Autre';
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push(mod);
        }
        const sortedCategories = Object.keys(grouped).sort((a, b) => {
            const ai = CATEGORY_ORDER.indexOf(a);
            const bi = CATEGORY_ORDER.indexOf(b);
            return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
        });

        const iconUrl = server.icon
            ? `https://cdn.discordapp.com/icons/${sid}/${server.icon}.png?size=128`
            : null;

        return `
            <div class="page-header">
                <div style="display:flex; align-items:center; gap:16px; flex-wrap:wrap;">
                    <div class="server-icon" style="width:48px;height:48px;font-size:20px;">
                        ${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="">` : escapeHtml(server.name?.charAt(0) || '?')}
                    </div>
                    <div>
                        <h2>${escapeHtml(server.name || sid)}</h2>
                        <p>ID: ${sid} — ${configuredModules.length} module(s) configuré(s)</p>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Modules</div>
                </div>
                <div class="module-list">
                    ${moduleNames.length === 0 ? `
                        <div class="empty-state">
                            <h3>Aucun module</h3>
                            <p>Ce serveur n'a aucun module configuré.</p>
                        </div>
                    ` : sortedCategories.map(cat => {
                        const mods = grouped[cat];
                        const modItems = mods.map(mod => {
                            const modConfig = serverConfig[mod] || {};
                            const schema = state.moduleSchemas[mod];
                            if (!schema) {
                                // Configured here, but no loaded extension registers it:
                                // nothing to edit until the extension is enabled.
                                return `
                                <div class="module-item inactive" title="L'extension de ce module n'est pas chargée">
                                    <div class="module-info">
                                        <span class="module-name">🧩 ${escapeHtml(mod)}</span>
                                        <span class="module-status">⏸ Extension inactive</span>
                                    </div>
                                </div>
                            `;
                            }
                            const noToggle = schema.noToggle === true;
                            const enabled = modConfig.enabled === true;
                            const label = schema.label || mod;
                            const icon = schema.icon || '🧩';
                            const isConfigured = mod in serverConfig;
                            return `
                                <div class="module-item" data-click="navigate" data-click-args="${actionArgs("module", sid, mod)}">
                                    <div class="module-info">
                                        <span class="module-name">${icon} ${label}</span>
                                        <span class="module-status">${
                                            noToggle
                                                ? (isConfigured && Object.keys(modConfig).length > 0 ? '📝 Configuré' : '⚪ Non configuré')
                                                : (isConfigured
                                                    ? (enabled ? '✅ Activé' : '❌ Désactivé')
                                                    : '⚪ Non configuré')
                                        }</span>
                                    </div>
                                    ${noToggle ? '' : `
                                    <label class="toggle" data-click="stop">
                                        <input type="checkbox" ${enabled ? 'checked' : ''}
                                            data-change="toggleModule" data-change-args="${actionArgs(sid, mod, CHECKED)}">
                                        <span class="toggle-slider"></span>
                                    </label>
                                    `}
                                </div>
                            `;
                        }).join('');
                        return `<div class="module-category-header">${cat}</div>${modItems}`;
                    }).join('')}
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Messages du bot</div>
                </div>
                <p style="color:var(--text-muted); margin:0 0 12px;">
                    Messages que le bot tient à jour dans ce serveur. « Republier » supprime le
                    message ; le module le reposte en bas de son salon à son prochain rafraîchissement.
                </p>
                <div id="bot-messages-list"><div class="loading"><div class="spinner"></div></div></div>
            </div>
        `;
    }

    async function loadBotMessages(serverId) {
        const container = document.getElementById('bot-messages-list');
        if (!container) return;
        let items = [];
        try {
            const data = await api(`/api/servers/${encodeURIComponent(serverId)}/bot-messages`);
            items = data?.messages || [];
        } catch (e) {
            container.innerHTML = `<p style="color:var(--text-muted)">Liste indisponible.</p>`;
            return;
        }
        if (items.length === 0) {
            container.innerHTML = `<p style="color:var(--text-muted)">Aucun message pour l'instant : chaque module poste le sien à son premier rafraîchissement.</p>`;
            return;
        }
        container.innerHTML = items.map(m => `
            <div class="message-status configured" style="margin-bottom:8px;">
                <span class="status-icon">📌</span>
                <span class="status-text"><strong>${escapeHtml(m.label)}</strong> · salon <code>${escapeHtml(m.channelId)}</code></span>
                <a class="status-link" href="${escapeHtml(m.url)}" target="_blank" rel="noreferrer">↗ voir</a>
                <button type="button" class="btn-status-reset" data-slot="${escapeHtml(m.slot)}"
                        data-click="republishBotMessage" data-click-args="${actionArgs(serverId, dataArg('slot'), EL)}"
                        title="Supprimer le message pour que le bot le reposte">🔄 Republier</button>
            </div>`).join('');
    }

    async function republishBotMessage(serverId, slot, btn) {
        if (!confirm('Supprimer ce message ? Le bot le repostera à son prochain rafraîchissement.')) return;
        if (btn) btn.disabled = true;
        try {
            await api(`/api/servers/${encodeURIComponent(serverId)}/bot-messages/republish`, {
                method: 'POST',
                body: JSON.stringify({ slot }),
            });
            toast('Message supprimé — il sera reposté au prochain rafraîchissement du module.', 'success');
        } catch (e) { /* handled by api() */ }
        await loadBotMessages(serverId);
    }

    async function toggleModule(serverId, moduleName, enabled) {
        try {
            const result = await api(`/api/servers/${serverId}/modules/${moduleName}/toggle`, {
                method: 'POST',
                body: JSON.stringify({ module: moduleName, enabled }),
            });
            const reloadSuffix = result?.reload?.error
                ? ` — rechargement échoué : ${result.reload.error}`
                : result?.reload?.reloaded
                    ? ' — extension rechargée'
                    : '';
            const syncSuffix = commandSyncSuffix(result);
            const failed = !!result?.reload?.error || !!(result?.commandSync && !result.commandSync.synced);
            toast(`${moduleName} ${enabled ? 'activé' : 'désactivé'}${reloadSuffix}${syncSuffix}`,
                failed ? 'warning' : 'success');
            await loadServers();
            if (isDeveloper()) await loadConfig();
            render();
        } catch (e) { /* handled by api() */ }
    }

    // ── Module Detail Page ────────────────────────────
    function renderModulePage() {
        const sid = state.currentServerId;
        const mod = state.currentModule;
        const server = state.servers?.[sid];
        if (!server) return '<div class="loading"><div class="spinner"></div></div>';

        const modConfig = server.config?.[mod] || {};
        const schema = state.moduleSchemas[mod];
        const noToggle = schema?.noToggle === true;
        const enabled = modConfig.enabled === true;
        const label = schema?.label || mod;
        const icon = schema?.icon || '🧩';
        const description = schema?.description || '';
        const formId = `module-${sid}-${mod}`;

        return `
            <div class="breadcrumb">
                <a data-click="navigate" data-click-args="${actionArgs("server", sid)}">${escapeHtml(server.name || sid)}</a>
                <span>›</span>
                <span>${icon} ${label}</span>
            </div>

            <div class="page-header">
                <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
                    <div>
                        <h2>${icon} ${label}</h2>
                        <p>${escapeHtml(description || 'Configuration du module pour ' + (server.name || sid))}</p>
                    </div>
                    ${noToggle ? '' : `
                    <label class="toggle" style="transform: scale(1.2);">
                        <input type="checkbox" ${enabled ? 'checked' : ''}
                            data-change="toggleModule" data-change-args="${actionArgs(sid, mod, CHECKED)}">
                        <span class="toggle-slider"></span>
                    </label>
                    `}
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Paramètres</div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="editModuleConfigJson" data-click-args="${actionArgs(sid, mod)}">
                            { } JSON
                        </button>
                        ${schema ? `
                            <button class="btn btn-success btn-sm" data-click="saveModuleForm" data-click-args="${actionArgs(sid, mod, formId)}">
                                💾 Sauvegarder
                            </button>
                        ` : ''}
                        ${mod === 'moduleEmbedManager' ? `
                        <button class="btn btn-primary btn-sm" data-click="publishEmbeds" data-click-args="${actionArgs(sid)}">
                            📢 Publier
                        </button>
                        ` : ''}
                        ${mod === 'moduleReactionRoles' ? `
                        <button class="btn btn-primary btn-sm" data-click="openRoleMenuBuilder" data-click-args="${actionArgs(sid, null)}">
                            ✨ Nouveau menu
                        </button>
                        ` : ''}
                    </div>
                </div>
                ${schema
                    ? renderSchemaForm(formId, schema,
                        schema.directValue ? { [mod]: modConfig } : modConfig,
                        { excludeEnabled: true })
                    : (Object.keys(modConfig).length === 0
                        ? `<div class="empty-state">
                            <h3>Configuration vide</h3>
                            <p>Utilisez le bouton "JSON" pour ajouter des paramètres.</p>
                        </div>`
                        : renderConfigPreview(modConfig)
                    )
                }
            </div>

            ${mod === 'moduleReactionRoles' ? `
            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">Menus de rôles</div>
                        <div class="card-subtitle">Boutons que les membres cliquent pour s'auto-attribuer un rôle.</div>
                    </div>
                </div>
                <div id="rolemenu-list" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            <div class="card" id="rolemenu-builder-card" data-server-id="${sid}" style="margin-top:16px; display:none;">
                <div class="card-header">
                    <div>
                        <div class="card-title" id="rolemenu-builder-title">Nouveau menu</div>
                        <div class="card-subtitle">Le menu est posté immédiatement dans le salon choisi.</div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="closeRoleMenuBuilder">Annuler</button>
                        <button class="btn btn-success btn-sm" data-click="saveRoleMenu">💾 Enregistrer</button>
                    </div>
                </div>
                <div id="rolemenu-builder-body"></div>
            </div>
            ` : ''}

            ${mod === 'moduleModeration' ? `
            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">Infractions</div>
                        <div class="card-subtitle">Historique des sanctions enregistrées pour ce serveur.</div>
                    </div>
                    <div class="card-actions" style="flex-wrap:wrap; gap:6px;">
                        <input id="inf-filter-user" class="form-input" placeholder="ID utilisateur" style="width:150px;">
                        <select id="inf-filter-type" class="form-input" style="width:150px;">
                            <option value="">Tous les types</option>
                            <option value="warn">Avertissement</option>
                            <option value="timeout">Timeout</option>
                            <option value="untimeout">Fin de timeout</option>
                            <option value="kick">Expulsion</option>
                            <option value="ban">Bannissement</option>
                            <option value="unban">Débannissement</option>
                            <option value="automod">Automod</option>
                            <option value="note">Note</option>
                        </select>
                        <select id="inf-filter-active" class="form-input" style="width:120px;">
                            <option value="">Tous</option>
                            <option value="true">Actifs</option>
                            <option value="false">Révoqués</option>
                        </select>
                        <button class="btn btn-primary btn-sm" data-click="loadInfractions" data-click-args="${actionArgs(sid)}">🔍 Filtrer</button>
                    </div>
                </div>
                <div id="infractions-list" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : ''}
            ${mod === 'moduleSpotify' ? `
            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Statistiques de la playlist</div>
                        <div class="card-subtitle">Playlist et votes tels que le bot les a enregistrés.</div>
                    </div>
                    <div class="card-actions">
                        ${publicPageButton(sid, 'spotify', modConfig)}
                        <button class="btn btn-ghost btn-sm" data-click="loadSpotifyStats" data-click-args="${actionArgs(sid, true)}">↻ Actualiser</button>
                    </div>
                </div>
                <div id="spotify-stats" class="viz-root" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : ''}

            ${mod === 'moduleXp' ? `
            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Statistiques</div>
                        <div class="card-subtitle">Activité XP du serveur, heures de Paris. L'XP vocal n'est journalisé que depuis l'ajout de ce tableau de bord.</div>
                    </div>
                    <div class="card-actions">
                        ${publicPageButton(sid, 'xp', modConfig)}
                        <select id="xp-stats-range" class="form-input" style="width:150px;" data-change="setXpStatsRange" data-change-args="${actionArgs(sid, VALUE)}">
                            ${XP_STATS_RANGES.map(d => `<option value="${d}" ${state.xpStatsDays === d ? 'selected' : ''}>${d} jours</option>`).join('')}
                        </select>
                        <button class="btn btn-ghost btn-sm" data-click="loadXpStats" data-click-args="${actionArgs(sid)}">🔄 Actualiser</button>
                    </div>
                </div>
                <div id="xp-stats" data-server-id="${sid}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>
            ` : ''}
            ${schema && !schema.directValue ? renderExtraKeysCard(schema, modConfig) : ''}
        `;
    }

    function renderExtraKeysCard(schema, config) {
        const schemaKeys = new Set(Object.keys(schema.fields || {}));
        const extraKeys = Object.keys(config).filter(k => !schemaKeys.has(k));
        if (extraKeys.length === 0) return '';
        return `
            <div class="card" style="margin-top:16px;">
                <div class="card-header">
                    <div>
                        <div class="card-title">⚠️ Paramètres supplémentaires</div>
                        <div class="card-subtitle">Clés présentes dans la config mais pas dans le schéma</div>
                    </div>
                </div>
                <div class="kv-grid">
                    ${extraKeys.map(key => `
                        <div class="kv-key" style="color:var(--warning)">${escapeHtml(key)}</div>
                        <div class="kv-value"><input type="text" value="${escapeHtml(JSON.stringify(config[key]))}" readonly></div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // ── Logs Page ─────────────────────────────────────
    function renderLogsPage() {
        const logCount = state.logs.length;
        const errorCount = state.logs.filter(l => l.level === 'ERROR' || l.level === 'CRITICAL').length;
        const warnCount = state.logs.filter(l => l.level === 'WARNING').length;

        return `
            <div class="page-header">
                <h2>📋 Logs en temps réel</h2>
                <p>Visualisation des logs du bot avec streaming en direct</p>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Total</div>
                    <div class="stat-value" id="log-count">${logCount}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Erreurs</div>
                    <div class="stat-value" style="color:var(--danger)">${errorCount}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avertissements</div>
                    <div class="stat-value" style="color:var(--warning)">${warnCount}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Streaming</div>
                    <div class="stat-value">
                        <span class="log-stat-badge ${state.logStreaming ? 'live' : 'paused'}">
                            ${state.logStreaming ? '● LIVE' : '○ Déconnecté'}
                        </span>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Console</div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="refreshLogs">🔄 Rafraîchir</button>
                        <button class="btn btn-ghost btn-sm" data-click="clearLogs">🗑️ Effacer</button>
                        <button class="btn btn-ghost btn-sm" data-click="downloadLogs">⬇️ Exporter</button>
                    </div>
                </div>

                <div class="log-toolbar">
                    <select data-change="onLogLevelChange" data-change-args="[{&quot;$&quot;:&quot;value&quot;}]" id="log-level-select">
                        <option value="">Tous les niveaux</option>
                        <option value="DEBUG" ${state.logLevel === 'DEBUG' ? 'selected' : ''}>DEBUG</option>
                        <option value="INFO" ${state.logLevel === 'INFO' ? 'selected' : ''}>INFO</option>
                        <option value="WARNING" ${state.logLevel === 'WARNING' ? 'selected' : ''}>WARNING</option>
                        <option value="ERROR" ${state.logLevel === 'ERROR' ? 'selected' : ''}>ERROR</option>
                        <option value="CRITICAL" ${state.logLevel === 'CRITICAL' ? 'selected' : ''}>CRITICAL</option>
                    </select>
                    <input type="text" placeholder="🔍 Rechercher dans les logs..."
                        value="${escapeHtml(state.logSearch)}"
                        data-input="onLogSearchInput" data-input-args="[{&quot;$&quot;:&quot;value&quot;}]">
                    <label class="verbose-toggle" title="Afficher le nom du logger et l'emplacement (fichier:ligne)">
                        <input type="checkbox" ${state.logVerbose ? 'checked' : ''}
                            data-change="onLogVerboseChange" data-change-args="[{&quot;$&quot;:&quot;checked&quot;}]">
                        Verbose
                    </label>
                </div>

                <div class="log-container" id="log-container">
                    <div class="log-lines" id="log-lines">
                        ${getFilteredLogs().map(e =>
                            `<div class="log-line">${formatLogLine(e)}</div>`
                        ).join('')}
                    </div>
                    <button class="autoscroll-btn" id="autoscroll-btn" data-click="scrollToBottom">
                        ↓ Défiler en bas
                    </button>
                </div>
            </div>
        `;
    }

    function getFilteredLogs() {
        let filtered = state.logs;
        if (state.logLevel) {
            const levels = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50 };
            const minLevel = levels[state.logLevel] || 0;
            filtered = filtered.filter(e => (levels[e.level] || 0) >= minLevel);
        }
        if (state.logSearch) {
            const s = state.logSearch.toLowerCase();
            filtered = filtered.filter(e =>
                e.message.toLowerCase().includes(s) || e.logger.toLowerCase().includes(s)
            );
        }
        return filtered;
    }

    function downloadLogs() {
        const filtered = getFilteredLogs();
        const text = filtered.map(e => {
            const time = new Date(e.timestamp * 1000).toISOString();
            return `[${time}][${e.level}][${e.logger}] ${e.message}`;
        }).join('\n');
        const blob = new Blob([text], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `michel-bot-logs-${new Date().toISOString().slice(0,10)}.log`;
        a.click();
        URL.revokeObjectURL(url);
    }

    // ── Module save/reload ────────────────────────────
    // Commands are pushed to Discord right after a save/reload/toggle; report it.
    function commandSyncSuffix(result) {
        if (!result?.commandSync) return '';
        return result.commandSync.synced
            ? ' — commandes synchronisées avec Discord'
            : ` — synchronisation des commandes échouée : ${result.commandSync.error}`;
    }

    async function saveModuleForm(serverId, moduleName, formId) {
        try {
            const values = collectFormValues(formId);
            const schema = state.moduleSchemas[moduleName];
            const server = state.servers?.[serverId];
            let config;
            if (schema?.directValue) {
                // For directValue modules (e.g. discord2name), the field value IS the config
                config = values[moduleName] ?? {};
            } else {
                const existing = server?.config?.[moduleName] || {};
                config = { ...existing, ...values };
            }
            const result = await api(`/api/servers/${serverId}/modules/${moduleName}`, {
                method: 'PUT',
                body: JSON.stringify({ config }),
            });
            const syncSuffix = commandSyncSuffix(result);
            const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
            if (result?.reload?.reloaded && !result.reload.error) {
                toast(`Configuration sauvegardée et extension rechargée !${syncSuffix}`,
                    syncFailed ? 'warning' : 'success');
            } else if (result?.reload?.skipped || !result?.reload) {
                toast(`Configuration sauvegardée !${syncSuffix}`, syncFailed ? 'warning' : 'success');
            } else {
                toast(`Configuration sauvegardée, rechargement échoué : ${result.reload.error}`, 'warning');
            }
            await loadServers();
            if (isDeveloper()) await loadConfig();
            render();
        } catch (e) { /* handled by api() */ }
    }

    async function publishEmbeds(serverId) {
        try {
            const result = await api(`/api/servers/${serverId}/modules/moduleEmbedManager/publish`, { method: 'POST' });
            toast(`✓ ${result.count} embed(s) publié(s) avec succès`, 'success');
        } catch (e) { /* handled by api() */ }
    }

    // ── Reaction Roles builder ────────────────────────
    const _rolesCache = {}; // serverId -> [{id, name, color, position}]
    let _rolemenuEditing = null;  // null = create, otherwise menu id being edited

    async function fetchServerRoles(serverId) {
        if (!serverId) return [];
        if (_rolesCache[serverId]) return _rolesCache[serverId];
        try {
            const data = await api(`/api/servers/${serverId}/roles`);
            const roles = data?.roles || [];
            _rolesCache[serverId] = roles;
            return roles;
        } catch (e) {
            console.warn('Could not fetch roles', e);
            return [];
        }
    }

    async function loadRoleMenus(serverId) {
        const container = document.getElementById('rolemenu-list');
        if (!container) return;
        try {
            const data = await api(`/api/servers/${serverId}/rolemenus`);
            const menus = data?.menus || [];
            if (menus.length === 0) {
                container.innerHTML = `<div class="empty-state"><h3>Aucun menu</h3><p>Cliquez sur « Nouveau menu » pour en créer un.</p></div>`;
                return;
            }
            container.innerHTML = menus.map(m => {
                const jumpUrl = m.message_id
                    ? `https://discord.com/channels/${m.guild_id}/${m.channel_id}/${m.message_id}`
                    : null;
                const entriesPreview = (m.entries || []).slice(0, 5).map(e =>
                    `<span style="margin-right:8px;">${escapeHtml(e.emoji)} ${escapeHtml(e.label)}</span>`
                ).join('');
                const more = (m.entries?.length || 0) > 5 ? ` <span style="color:var(--text-muted)">+${m.entries.length - 5}</span>` : '';
                return `
                    <div style="border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:8px; display:flex; flex-direction:column; gap:6px;">
                        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                            <div>
                                <strong>${escapeHtml(m.title || '(sans titre)')}</strong>
                                <span style="color:var(--text-muted); margin-left:8px;">&lt;#${escapeHtml(m.channel_id)}&gt;</span>
                                ${jumpUrl ? `<a href="${jumpUrl}" target="_blank" style="margin-left:8px;">↗ Discord</a>` : ''}
                            </div>
                            <div style="display:flex; gap:6px;">
                                <button class="btn btn-ghost btn-sm" data-click="openRoleMenuBuilder" data-click-args="${actionArgs(serverId, m.id)}">✏️ Modifier</button>
                                <button class="btn btn-ghost btn-sm" style="color:var(--danger)" data-click="deleteRoleMenu" data-click-args="${actionArgs(serverId, m.id)}">🗑️ Supprimer</button>
                            </div>
                        </div>
                        <div style="font-size:13px;">${entriesPreview}${more}</div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(String(e))}</p></div>`;
        }
    }

    function _renderRoleSelect(roles, currentRoleId) {
        let opts = '<option value="">— Choisir un rôle —</option>';
        for (const r of roles) {
            const sel = String(r.id) === String(currentRoleId) ? 'selected' : '';
            opts += `<option value="${escapeHtml(r.id)}" ${sel}>@${escapeHtml(r.name)}</option>`;
        }
        return opts;
    }

    function _renderEntryRow(roles, entry) {
        const e = entry || { role_id: '', emoji: '', label: '' };
        return `
            <div class="rolemenu-entry" style="display:grid; grid-template-columns: 1fr 80px 1fr 36px; gap:8px; align-items:center; margin-bottom:6px;">
                <select class="form-input" data-rrf="role_id">${_renderRoleSelect(roles, e.role_id)}</select>
                <input type="text" class="form-input" data-rrf="emoji" value="${escapeHtml(e.emoji)}" placeholder="🎮" maxlength="64">
                <input type="text" class="form-input" data-rrf="label" value="${escapeHtml(e.label)}" placeholder="Libellé du bouton" maxlength="80">
                <button type="button" class="btn-remove-entry" data-click="remove-closest" data-click-args="[&quot;.rolemenu-entry&quot;,{&quot;$&quot;:&quot;el&quot;}]" title="Supprimer">✕</button>
            </div>
        `;
    }

    async function openRoleMenuBuilder(serverId, menuId) {
        const card = document.getElementById('rolemenu-builder-card');
        const body = document.getElementById('rolemenu-builder-body');
        const titleEl = document.getElementById('rolemenu-builder-title');
        if (!card || !body) return;
        _rolemenuEditing = menuId || null;
        titleEl.textContent = menuId ? 'Modifier le menu' : 'Nouveau menu';

        body.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
        card.style.display = 'block';
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });

        const [roles, channelData, menus] = await Promise.all([
            fetchServerRoles(serverId),
            fetchServerChannels(serverId),
            menuId ? api(`/api/servers/${serverId}/rolemenus`) : Promise.resolve(null),
        ]);

        let menu = null;
        if (menuId && menus?.menus) {
            menu = menus.menus.find(m => m.id === menuId);
        }
        const initial = menu || { channel_id: '', title: '', description: '', entries: [{}] };
        if (!initial.entries || initial.entries.length === 0) initial.entries = [{}];

        let channelOpts = '<option value="">— Choisir un salon —</option>';
        if (channelData?.channels) {
            const grouped = new Map();
            for (const c of channelData.channels) {
                if (c.is_thread) continue;
                const catId = c.parent_id || '__none__';
                if (!grouped.has(catId)) grouped.set(catId, []);
                grouped.get(catId).push(c);
            }
            const orderedCats = [
                ['__none__', 'Sans catégorie'],
                ...((channelData.categories || []).map(c => [c.id, c.name])),
            ];
            for (const [catId, catName] of orderedCats) {
                const list = grouped.get(catId);
                if (!list || list.length === 0) continue;
                channelOpts += `<optgroup label="${escapeHtml(catName)}">`;
                for (const ch of list) {
                    const sel = String(ch.id) === String(initial.channel_id) ? 'selected' : '';
                    channelOpts += `<option value="${escapeHtml(ch.id)}" ${sel}>#${escapeHtml(ch.name)}</option>`;
                }
                channelOpts += `</optgroup>`;
            }
        }

        body.innerHTML = `
            <div class="form-grid" style="display:flex; flex-direction:column; gap:12px;">
                <div>
                    <label class="form-label">Salon</label>
                    <select class="form-input" id="rrf-channel" ${menuId ? 'disabled title="Le salon est figé une fois le menu créé."' : ''}>
                        ${channelOpts}
                    </select>
                </div>
                <div>
                    <label class="form-label">Titre</label>
                    <input type="text" class="form-input" id="rrf-title" value="${escapeHtml(initial.title || '')}" maxlength="256" placeholder="Ex : Choisis tes rôles">
                </div>
                <div>
                    <label class="form-label">Description (optionnel)</label>
                    <textarea class="form-textarea" id="rrf-description" rows="2" placeholder="Affichée au-dessus de la liste des rôles">${escapeHtml(initial.description || '')}</textarea>
                </div>
                <div>
                    <label class="form-label">Entrées (max 25)</label>
                    <div id="rrf-entries">
                        ${initial.entries.map(e => _renderEntryRow(roles, e)).join('')}
                    </div>
                    <button type="button" class="btn-add-entry" data-click="addRoleMenuEntry">+ Ajouter une entrée</button>
                </div>
            </div>
        `;
    }

    function addRoleMenuEntry() {
        const list = document.getElementById('rrf-entries');
        if (!list) return;
        if (list.querySelectorAll('.rolemenu-entry').length >= 25) {
            toast('Maximum 25 entrées par menu (limite Discord).', 'warning');
            return;
        }
        const sid = state.currentServerId;
        const roles = _rolesCache[sid] || [];
        list.insertAdjacentHTML('beforeend', _renderEntryRow(roles, null));
    }

    function closeRoleMenuBuilder() {
        const card = document.getElementById('rolemenu-builder-card');
        if (card) card.style.display = 'none';
        _rolemenuEditing = null;
    }

    function _collectRoleMenuPayload() {
        const channel_id = document.getElementById('rrf-channel')?.value?.trim() || '';
        const title = document.getElementById('rrf-title')?.value?.trim() || '';
        const description = document.getElementById('rrf-description')?.value?.trim() || '';
        const entries = [];
        document.querySelectorAll('#rrf-entries .rolemenu-entry').forEach(row => {
            const role_id = row.querySelector('[data-rrf="role_id"]')?.value?.trim() || '';
            const emoji = row.querySelector('[data-rrf="emoji"]')?.value?.trim() || '';
            const label = row.querySelector('[data-rrf="label"]')?.value?.trim() || '';
            if (role_id || emoji || label) entries.push({ role_id, emoji, label });
        });
        return { channel_id, title, description: description || null, entries };
    }

    async function saveRoleMenu() {
        const sid = state.currentServerId;
        if (!sid) return;
        const payload = _collectRoleMenuPayload();
        if (!_rolemenuEditing && !payload.channel_id) return toast('Salon requis.', 'warning');
        if (!payload.title) return toast('Titre requis.', 'warning');
        if (!payload.entries.length) return toast('Au moins une entrée requise.', 'warning');
        for (const [i, e] of payload.entries.entries()) {
            if (!e.role_id) return toast(`Entrée ${i + 1} : rôle manquant.`, 'warning');
            if (!e.emoji) return toast(`Entrée ${i + 1} : emoji manquant.`, 'warning');
            if (!e.label) return toast(`Entrée ${i + 1} : libellé manquant.`, 'warning');
        }

        try {
            if (_rolemenuEditing) {
                const body = {
                    title: payload.title,
                    description: payload.description,
                    entries: payload.entries,
                };
                await api(`/api/servers/${sid}/rolemenus/${_rolemenuEditing}`, {
                    method: 'PUT',
                    body: JSON.stringify(body),
                });
                toast('Menu mis à jour.', 'success');
            } else {
                await api(`/api/servers/${sid}/rolemenus`, {
                    method: 'POST',
                    body: JSON.stringify(payload),
                });
                toast('Menu créé.', 'success');
            }
            closeRoleMenuBuilder();
            loadRoleMenus(sid);
        } catch (e) { /* handled by api() */ }
    }

    async function deleteRoleMenu(serverId, menuId) {
        if (!confirm('Supprimer ce menu ? Le message Discord associé sera également supprimé.')) return;
        try {
            await api(`/api/servers/${serverId}/rolemenus/${menuId}`, { method: 'DELETE' });
            toast('Menu supprimé.', 'success');
            if (_rolemenuEditing === menuId) closeRoleMenuBuilder();
            loadRoleMenus(serverId);
        } catch (e) { /* handled by api() */ }
    }

    // ── Moderation: infraction browser ──────────────────────────────────
    const INFRACTION_TYPE_LABELS = {
        warn: '⚠️ Avertissement', timeout: '🔇 Timeout', untimeout: '🔊 Fin de timeout',
        kick: '👢 Expulsion', ban: '🔨 Bannissement', unban: '♻️ Débannissement',
        note: '📝 Note', automod: '🛡️ Automod',
    };

    async function loadInfractions(serverId) {
        const container = document.getElementById('infractions-list');
        if (!container) return;
        container.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
        const userId = (document.getElementById('inf-filter-user')?.value || '').trim();
        const type = document.getElementById('inf-filter-type')?.value || '';
        const active = document.getElementById('inf-filter-active')?.value || '';
        const params = new URLSearchParams();
        if (userId) params.set('user_id', userId);
        if (type) params.set('type', type);
        if (active) params.set('active', active);
        try {
            const data = await api(`/api/servers/${serverId}/infractions?${params.toString()}`);
            const items = data?.infractions || [];
            if (items.length === 0) {
                container.innerHTML = `<div class="empty-state"><h3>Aucune infraction</h3><p>Aucune sanction ne correspond aux filtres.</p></div>`;
                return;
            }
            container.innerHTML = items.map(i => {
                const label = INFRACTION_TYPE_LABELS[i.type] || i.type;
                const date = i.created_at ? new Date(i.created_at).toLocaleString('fr-FR') : '';
                const dur = i.duration_seconds ? ` · ${Math.round(i.duration_seconds / 60)} min` : '';
                const by = i.moderator_id === 'automod' ? '🛡️ Automod' : i.moderator_id;
                const revokeBtn = i.active
                    ? `<button class="btn btn-ghost btn-sm" style="color:var(--danger)" data-click="revokeInfraction" data-click-args="${actionArgs(serverId, i.id)}">Révoquer</button>`
                    : `<span style="color:var(--text-muted); font-size:12px;">Révoqué</span>`;
                return `
                    <div style="border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:8px; ${i.active ? '' : 'opacity:0.55;'} display:flex; flex-direction:column; gap:6px;">
                        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                            <div>
                                <strong>#${i.id} · ${escapeHtml(label)}</strong>${dur}
                                <span style="color:var(--text-muted); margin-left:8px;">utilisateur ${escapeHtml(i.user_id)}</span>
                            </div>
                            <div style="display:flex; gap:6px; align-items:center;">${revokeBtn}</div>
                        </div>
                        <div style="font-size:13px;">${escapeHtml(i.reason || 'Aucune raison')}</div>
                        <div style="font-size:12px; color:var(--text-muted);">Par ${escapeHtml(by)} · ${escapeHtml(date)}</div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(String(e))}</p></div>`;
        }
    }

    async function revokeInfraction(serverId, caseId) {
        if (!confirm(`Révoquer le cas #${caseId} ?`)) return;
        try {
            await api(`/api/servers/${serverId}/infractions/${caseId}`, { method: 'DELETE' });
            toast(`Cas #${caseId} révoqué.`, 'success');
            loadInfractions(serverId);
        } catch (e) { /* handled by api() */ }
    }

    // ── Spotify: playlist & vote statistics ─────────────────────────────
    // Drawn with Chart.js through the XP section's lazy loader (ensureChartJs).
    // Every chart also gets a "Voir les données" table: a canvas is opaque to
    // screen readers, and the tables stay usable if Chart.js can't load.
    const SPOTIFY_STATS_TTL_MS = 5 * 60 * 1000;
    const _spotifyStatsCache = {};  // serverId -> { data, nameOf, at }
    let _spotifyCharts = [];
    let _spotifyStatsSeq = 0;   // fetches: a newer load supersedes this one
    let _spotifyRenderSeq = 0;  // draws: a newer render supersedes this one
    // Diverging keep / no-opinion / remove set; ink = count colour inside a segment.
    const VOTE_KEYS = [
        { key: 'conserver', label: 'Conserver', color: '--viz-keep', ink: '#111214' },
        { key: 'menfou', label: 'Menfou', color: '--viz-neutral', ink: '#ffffff' },
        { key: 'supprimer', label: 'Supprimer', color: '--viz-remove', ink: '#111214' },
    ];

    const fmtDec = n => Number(n || 0).toLocaleString('fr-FR', { maximumFractionDigits: 1 });
    const fmtPct = r => `${(Number(r || 0) * 100).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %`;
    const plural = (n, word) => `${fmtNum(n)} ${word}${n >= 2 ? 's' : ''}`;

    function fmtClock(seconds) {  // 215 -> "3:35"
        const s = Math.round(seconds);
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }

    function fmtLongDuration(ms) {
        const minutes = Math.round(ms / 60000);
        const d = Math.floor(minutes / 1440), h = Math.floor((minutes % 1440) / 60), m = minutes % 60;
        if (d) return `${d} j ${h} h`;
        if (h) return `${h} h ${String(m).padStart(2, '0')}`;
        return `${m} min`;
    }

    function fmtMonth(key, long = false) {  // "2026-01" -> "janv. 26" / "janvier 2026"
        const [y, m] = key.split('-').map(Number);
        return new Date(y, m - 1, 1).toLocaleDateString('fr-FR',
            long ? { month: 'long', year: 'numeric' } : { month: 'short', year: '2-digit' });
    }

    // Long spans label each January with its year; shorter ones every 1st/3rd month.
    function monthAxisLabels(months) {
        if (months.length > 24) return months.map(m => m.endsWith('-01') ? m.slice(0, 4) : '');
        const every = months.length > 8 ? 3 : 1;
        return months.map((m, i) => i % every === 0 ? fmtMonth(m) : '');
    }

    function destroySpotifyCharts() {
        _spotifyCharts.forEach(chart => { try { chart.destroy(); } catch (_) { /* already gone */ } });
        _spotifyCharts = [];
    }

    function vizPanel(title, sub, body, wide = false) {
        return `<div class="viz-panel${wide ? ' viz-wide' : ''}">
            <h4>${escapeHtml(title)}</h4>
            ${sub ? `<div class="viz-sub">${escapeHtml(sub)}</div>` : ''}
            ${body}
        </div>`;
    }

    function vizCanvas(id, height, label) {
        return `<div class="viz-canvas" style="height:${height}px">
            <canvas id="${id}" role="img" aria-label="${escapeHtml(label)}"></canvas>
        </div>`;
    }

    function vizTable(headers, rows) {
        // headers: [{ label, num }]; rows: arrays of display strings (escaped here).
        const cls = i => headers[i].num ? ' class="num"' : '';
        return `<details class="viz-data"><summary>Voir les données</summary>
            <div class="viz-table-scroll"><table>
                <thead><tr>${headers.map((h, i) => `<th${cls(i)}>${escapeHtml(h.label)}</th>`).join('')}</tr></thead>
                <tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${cls(i)}>${escapeHtml(c)}</td>`).join('')}</tr>`).join('')}</tbody>
            </table></div>
        </details>`;
    }

    // Inline Chart.js plugin: the value after each bar's end ('end'), or the
    // count centred in a stacked segment when it fits ('inside').
    const spotifyValueLabels = {
        id: 'spotifyValueLabels',
        afterDatasetsDraw(chart, _args, opts) {
            const { ctx } = chart;
            ctx.save();
            ctx.font = `600 11px ${window.Chart.defaults.font.family}`;
            ctx.textBaseline = 'middle';
            chart.data.datasets.forEach((ds, di) => {
                if (!chart.isDatasetVisible(di)) return;
                chart.getDatasetMeta(di).data.forEach((bar, i) => {
                    const count = ds.counts ? ds.counts[i] : ds.data[i];
                    if (!count) return;
                    const text = fmtNum(count);
                    if (opts.mode === 'inside') {
                        if (ctx.measureText(text).width + 8 > Math.abs(bar.x - bar.base)) return;
                        ctx.fillStyle = ds.valueInk;
                        ctx.textAlign = 'center';
                        ctx.fillText(text, (bar.x + bar.base) / 2, bar.y);
                    } else {
                        ctx.fillStyle = opts.color;
                        ctx.textAlign = 'left';
                        ctx.fillText(text, bar.x + 6, bar.y);
                    }
                });
            });
            ctx.restore();
        },
    };

    async function spotifyNameResolver(serverId, d) {
        const ids = [
            d.current_poll?.added_by,
            ...(d.top_contributors || []).map(c => c.key),
            ...(d.voters || []).map(u => u.user_id),
        ].filter(Boolean).map(String);
        let byId = new Map();
        try {
            byId = await resolveServerMembers(serverId, ids);
        } catch (e) {
            console.warn('Could not resolve Spotify member names', e);
        }
        const prenoms = getDiscord2NameMap();
        return id => prenoms[id] || byId.get(String(id))?.display_name || byId.get(String(id))?.username || id;
    }

    async function loadSpotifyStats(serverId, force = false) {
        const container = document.getElementById('spotify-stats');
        if (!container) return;
        const seq = ++_spotifyStatsSeq;
        const stale = () => seq !== _spotifyStatsSeq || !container.isConnected;
        const cacheKey = state.publicView ? `public:${state.publicView.token}` : serverId;
        const cached = _spotifyStatsCache[cacheKey];
        if (cached) {
            showSpotifyStats(container, cached);
            if (!force && Date.now() - cached.at < SPOTIFY_STATS_TTL_MS) return;
            container.classList.add('viz-refreshing');  // keep the frame while refetching
        }
        let data, nameOf;
        try {
            // The public payload already carries names in place of user ids.
            if (state.publicView) {
                data = await publicApi(`/api/public/${state.publicView.token}/spotify/stats`);
                nameOf = id => id;
            } else {
                data = await api(`/api/servers/${encodeURIComponent(serverId)}/spotify/stats`);
                nameOf = data ? await spotifyNameResolver(serverId, data) : (id => id);
            }
        } catch (e) {
            if (stale()) return;
            container.classList.remove('viz-refreshing');
            if (!cached) {
                container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(e?.message || String(e))}</p></div>`;
            }
            return;
        }
        if (stale()) return;
        if (!data) {
            container.innerHTML = `<div class="empty-state"><h3>Accès refusé</h3><p>Statistiques réservées aux administrateurs de ce serveur.</p></div>`;
            return;
        }
        const entry = { data, nameOf, at: Date.now() };
        _spotifyStatsCache[cacheKey] = entry;
        showSpotifyStats(container, entry);
    }

    async function showSpotifyStats(container, { data, nameOf }) {
        const token = ++_spotifyRenderSeq;
        destroySpotifyCharts();
        container.classList.remove('viz-refreshing');
        container.innerHTML = renderSpotifyStats(data, nameOf);
        if (!container.querySelector('canvas')) return;
        const stale = () => token !== _spotifyRenderSeq || !container.isConnected;
        try {
            await ensureChartJs();
        } catch (e) {
            if (stale()) return;
            container.querySelectorAll('.viz-canvas').forEach(el => {
                el.innerHTML = `<div class="viz-fallback">Graphique indisponible : Chart.js n'a pas pu être chargé. Les valeurs restent dans « Voir les données ».</div>`;
            });
            return;
        }
        if (stale()) return;
        drawSpotifyCharts(data, nameOf);
    }

    function renderSpotifyStats(d, nameOf) {
        const s = d.summary, v = d.votes;
        if (!s.track_count && !v.polls_closed && !d.current_poll) {
            return `<div class="empty-state"><h3>Pas encore de données</h3>
                <p>Le bot n'a pas encore synchronisé la playlist de ce serveur.</p></div>`;
        }

        const playlistTiles = [
            ['Titres', fmtNum(s.track_count)],
            ['Durée totale', fmtLongDuration(s.total_duration_ms), `${fmtClock(s.avg_duration_ms / 1000)} par titre en moyenne`],
            ['Artistes', fmtNum(s.artist_count)],
            ['Contributeurs', fmtNum(s.contributor_count)],
        ];
        const voteTiles = v.polls_closed ? [
            ['Votes clos', fmtNum(v.polls_closed), `${plural(v.kept, 'conservée')} · ${plural(v.removed, 'supprimée')}`],
            ['Taux de suppression', fmtPct(v.removal_rate)],
            ['Participation', fmtDec(v.avg_participation), `votants par vote · max ${fmtNum(v.max_participation)}`],
        ] : [];
        const tileRow = tiles => tiles.length ? `<div class="stats-grid">${tiles.map(([label, value, sub]) => `
                <div class="stat-card">
                    <div class="stat-label">${escapeHtml(label)}</div>
                    <div class="stat-value">${escapeHtml(value)}</div>
                    ${sub ? `<div class="stat-sub">${escapeHtml(sub)}</div>` : ''}
                </div>`).join('')}
            </div>` : '';
        const voteHeaders = [...VOTE_KEYS.map(k => ({ label: k.label, num: true })), { label: 'Total', num: true }];
        const voteCells = r => [...VOTE_KEYS.map(k => fmtNum(r[k.key])),
            fmtNum(VOTE_KEYS.reduce((sum, k) => sum + r[k.key], 0))];

        const panels = [];
        const p = d.current_poll;
        if (p && p.results_hidden) {
            const meta = [
                p.date ? `Ouvert le ${parseIsoDay(p.date).toLocaleDateString('fr-FR')}` : '',
                p.added_by ? `ajoutée par ${nameOf(p.added_by)}` : '',
            ].filter(Boolean).join(' · ');
            panels.push(vizPanel('Vote en cours', '',
                `<div class="viz-poll-name">${escapeHtml(p.name)}</div>
                 ${meta ? `<div class="viz-sub">${escapeHtml(meta)}</div>` : ''}
                 <div class="viz-sub" style="margin:0;">${escapeHtml(p.voter_count
                     ? `${plural(p.voter_count, 'votant')} pour l'instant`
                     : 'Pas encore de votes')} · résultats masqués jusqu'à la clôture du vote.</div>`, true));
        } else if (p) {
            const total = p.conserver + p.menfou + p.supprimer;
            const meta = [
                p.date ? `Ouvert le ${parseIsoDay(p.date).toLocaleDateString('fr-FR')}` : '',
                p.added_by ? `ajoutée par ${nameOf(p.added_by)}` : '',
            ].filter(Boolean).join(' · ');
            const body = total
                ? vizCanvas('spotify-chart-poll', 76, 'Répartition des votes en cours')
                  + `<div class="viz-sub" style="margin:8px 0 0;">${escapeHtml(`${plural(total, 'votant')} : ${p.voters.map(nameOf).join(', ')}`)}</div>`
                  + vizTable(voteHeaders, [voteCells(p)])
                : `<div class="viz-sub" style="margin:0;">Pas encore de votes.</div>`;
            panels.push(vizPanel('Vote en cours', '',
                `<div class="viz-poll-name">${escapeHtml(p.name)}</div>
                 ${meta ? `<div class="viz-sub">${escapeHtml(meta)}</div>` : ''}${body}`, true));
        }

        if (d.additions_by_month.length) {
            panels.push(vizPanel('Ajouts par mois', 'Titres encore présents dans la playlist, par mois d\'ajout',
                vizCanvas('spotify-chart-additions', 220, 'Titres ajoutés par mois')
                + vizTable([{ label: 'Mois' }, { label: 'Titres', num: true }],
                    d.additions_by_month.map(m => [fmtMonth(m.month, true), fmtNum(m.count)])),
                true));
        }

        const rankTable = (rows, labelOf, head) => vizTable(
            [{ label: head }, { label: 'Titres', num: true }, { label: 'Part', num: true }],
            rows.map(r => [labelOf(r), fmtNum(r.count), fmtPct(r.count / s.track_count)]));
        if (d.top_contributors.length) {
            panels.push(vizPanel('Top contributeurs', 'Titres présents dans la playlist, par personne',
                vizCanvas('spotify-chart-contributors', d.top_contributors.length * 26 + 24, 'Top contributeurs')
                + rankTable(d.top_contributors, c => nameOf(c.key), 'Personne')));
        }
        if (d.top_artists.length) {
            panels.push(vizPanel('Top artistes', 'Nombre de titres par artiste',
                vizCanvas('spotify-chart-artists', d.top_artists.length * 26 + 24, 'Top artistes')
                + rankTable(d.top_artists, a => a.key, 'Artiste')));
        }

        if (d.duration_buckets.length) {
            panels.push(vizPanel('Durée des titres', 'Nombre de titres par tranche de 30 secondes',
                vizCanvas('spotify-chart-durations', 220, 'Répartition des durées des titres')
                + vizTable([{ label: 'Durée' }, { label: 'Titres', num: true }],
                    d.duration_buckets.map(b => [durationRange(b), fmtNum(b.count)]))));
        }

        if (d.participation_by_month.length) {
            panels.push(vizPanel('Participation aux votes', 'Votants par vote, en moyenne sur le mois',
                vizCanvas('spotify-chart-participation', 220, 'Participation moyenne aux votes par mois')
                + vizTable([{ label: 'Mois' }, { label: 'Votes', num: true }, { label: 'Votants (moy.)', num: true }],
                    d.participation_by_month.map(m => [fmtMonth(m.month, true), fmtNum(m.polls),
                        m.average == null ? '—' : fmtDec(m.average)]))));
        }

        if (d.voters.length) {
            panels.push(vizPanel('Votes par personne', 'Répartition des choix sur les votes clos, par nombre de votes',
                vizCanvas('spotify-chart-voters', d.voters.length * 30 + 56, 'Répartition des votes par personne')
                + vizTable([{ label: 'Personne' }, ...voteHeaders],
                    d.voters.map(u => [nameOf(u.user_id), ...voteCells(u)])),
                true));
        }

        return `${tileRow(playlistTiles)}${tileRow(voteTiles)}
            <div class="viz-grid">${panels.join('')}</div>`;
    }

    function durationRange(b) {
        return b.end_s == null ? `${fmtClock(b.start_s)} et plus` : `${fmtClock(b.start_s)} – ${fmtClock(b.end_s)}`;
    }

    function drawSpotifyCharts(d, nameOf) {
        destroySpotifyCharts();
        const Chart = window.Chart;
        applyChartTheme(Chart);
        const series = cssVar('--viz-series', '#1aa34a');
        const grid = cssVar('--viz-grid', '#35373c');
        const surface = cssVar('--bg-card', '#2b2d31');
        const ink = cssVar('--text-primary', '#f2f3f5');
        const s = d.summary;
        const add = (id, config) => {
            const canvas = document.getElementById(id);
            if (canvas) _spotifyCharts.push(new Chart(canvas, config));
        };
        const countAxis = { beginAtZero: true, grid: { color: grid }, ticks: { precision: 0, maxTicksLimit: 6, callback: v => fmtNum(v) } };
        const labelAxis = labels => ({
            grid: { display: false },
            ticks: { autoSkip: false, maxRotation: 0, callback: (_v, i) => labels[i] || '' },
        });
        const truncate = (text, max) => text.length > max ? `${text.slice(0, max - 1)}…` : text;

        // One bar series: months or duration buckets.
        const columns = (id, labels, axisLabels, values, title, label) => add(id, {
            type: 'bar',
            data: {
                labels,
                datasets: [{ data: values, backgroundColor: series, borderRadius: 4, maxBarThickness: 24 }],
            },
            options: {
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { title: items => title(items[0].dataIndex), label: item => label(item.parsed.y) } },
                },
                scales: { x: labelAxis(axisLabels), y: countAxis },
            },
        });

        // Ranked horizontal bars with the value at the tip.
        const ranking = (id, rows, labelOf) => add(id, {
            type: 'bar',
            data: {
                labels: rows.map(labelOf),
                datasets: [{ data: rows.map(r => r.count), backgroundColor: series, borderRadius: 4, maxBarThickness: 16 }],
            },
            options: {
                indexAxis: 'y',
                maintainAspectRatio: false,
                layout: { padding: { right: 36 } },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: {
                        label: item => `${plural(item.parsed.x, 'titre')} · ${fmtPct(item.parsed.x / s.track_count)} de la playlist`,
                    } },
                    spotifyValueLabels: { mode: 'end', color: ink },
                },
                scales: {
                    x: { display: false, beginAtZero: true },
                    y: { grid: { display: false }, ticks: { callback(v) { return truncate(this.getLabelForValue(v), 22); } } },
                },
            },
            plugins: [spotifyValueLabels],
        });

        // keep | menfou | remove, horizontal and stacked. `percent` draws each
        // row at 100 % (voters); otherwise raw counts on hidden axes (open poll).
        const voteStack = (id, labels, rows, percent) => {
            const totals = rows.map(r => VOTE_KEYS.reduce((sum, k) => sum + (r[k.key] || 0), 0));
            add(id, {
                type: 'bar',
                data: {
                    labels,
                    datasets: VOTE_KEYS.map(k => ({
                        label: k.label,
                        data: rows.map((r, i) => percent ? (totals[i] ? r[k.key] / totals[i] * 100 : 0) : r[k.key]),
                        counts: rows.map(r => r[k.key]),
                        valueInk: k.ink,
                        backgroundColor: cssVar(k.color, '#808080'),
                        // A surface-coloured edge keeps a 2 px gap between segments.
                        borderColor: surface,
                        borderWidth: { right: 2 },
                        borderSkipped: 'start',
                        borderRadius: 4,
                        maxBarThickness: 20,
                    })),
                },
                options: {
                    indexAxis: 'y',
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'top', align: 'start', labels: { boxWidth: 10, boxHeight: 10 } },
                        tooltip: { callbacks: {
                            label: item => {
                                const n = item.dataset.counts[item.dataIndex];
                                const total = totals[item.dataIndex];
                                return `${item.dataset.label} : ${plural(n, 'vote')} (${fmtPct(total ? n / total : 0)})`;
                            },
                        } },
                        spotifyValueLabels: { mode: 'inside' },
                    },
                    scales: {
                        x: {
                            stacked: true, display: percent, max: percent ? 100 : totals[0],
                            grid: { color: grid }, ticks: { callback: v => `${v} %` },
                        },
                        y: { stacked: true, display: percent, grid: { display: false } },
                    },
                },
                plugins: [spotifyValueLabels],
            });
        };

        if (d.current_poll && !d.current_poll.results_hidden) voteStack('spotify-chart-poll', [''], [d.current_poll], false);

        const months = d.additions_by_month;
        columns('spotify-chart-additions', months.map(m => fmtMonth(m.month)),
            monthAxisLabels(months.map(m => m.month)), months.map(m => m.count),
            i => fmtMonth(months[i].month, true), n => plural(n, 'titre'));

        ranking('spotify-chart-contributors', d.top_contributors, c => nameOf(c.key));
        ranking('spotify-chart-artists', d.top_artists, a => a.key);

        const buckets = d.duration_buckets;
        const labelStep = buckets.length > 16 ? 120 : 60;
        columns('spotify-chart-durations', buckets.map(durationRange),
            buckets.map(b => b.start_s % labelStep === 0 ? `${fmtClock(b.start_s)}${b.end_s == null ? '+' : ''}` : ''),
            buckets.map(b => b.count), i => durationRange(buckets[i]), n => plural(n, 'titre'));

        // Monthly turnout; months without a poll are null, which breaks the line.
        const part = d.participation_by_month;
        const has = i => part[i] && part[i].average != null;
        const lastIdx = part.map((_, i) => has(i)).lastIndexOf(true);
        const dotted = i => i === lastIdx || (has(i) && !has(i - 1) && !has(i + 1));
        add('spotify-chart-participation', {
            type: 'line',
            data: {
                labels: part.map(m => fmtMonth(m.month)),
                datasets: [{
                    data: part.map(m => m.average),
                    borderColor: series, backgroundColor: series, borderWidth: 2, tension: 0, spanGaps: false,
                    pointRadius: ctx => dotted(ctx.dataIndex) ? 4 : 0,
                    pointHoverRadius: 5, pointBorderColor: surface, pointBorderWidth: 2,
                }],
            },
            options: {
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: {
                        title: items => fmtMonth(part[items[0].dataIndex].month, true),
                        label: item => `${fmtDec(item.parsed.y)} votant${item.parsed.y >= 2 ? 's' : ''} en moyenne · ${plural(part[item.dataIndex].polls, 'vote')}`,
                    } },
                },
                scales: {
                    x: labelAxis(monthAxisLabels(part.map(m => m.month))),
                    y: { beginAtZero: true, grid: { color: grid } },
                },
            },
        });

        voteStack('spotify-chart-voters',
            d.voters.map(u => `${truncate(nameOf(u.user_id), 18)} (${fmtNum(u.conserver + u.menfou + u.supprimer)})`),
            d.voters, true);
    }

    // ── XP: activity stats + charts ─────────────────────────────────────
    // Chart.js is loaded lazily, only once an XP module page is opened.
    // Pinned build + SRI hash: bump both together.
    const CHARTJS_SRC = 'https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js';
    const CHARTJS_SRI = 'sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ';  // pragma: allowlist secret — public SRI digest
    const XP_STATS_RANGES = [7, 30, 90, 365];
    const XP_WEEKDAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
    const XP_MEDALS = ['🥇', '🥈', '🥉'];
    let _chartJsPromise = null;
    let _xpCharts = [];
    let _xpStatsSeq = 0;

    function ensureChartJs() {
        if (window.Chart) return Promise.resolve(window.Chart);
        if (!_chartJsPromise) {
            _chartJsPromise = new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = CHARTJS_SRC;
                script.integrity = CHARTJS_SRI;
                script.crossOrigin = 'anonymous';
                script.async = true;
                script.onload = () => (window.Chart ? resolve(window.Chart) : reject(new Error('Chart.js introuvable')));
                script.onerror = () => {
                    script.remove();
                    reject(new Error("Chart.js n'a pas pu être chargé"));
                };
                document.head.appendChild(script);
            }).catch(err => {
                _chartJsPromise = null;  // let the next load retry
                throw err;
            });
        }
        return _chartJsPromise;
    }

    function destroyXpCharts() {
        _xpCharts.forEach(chart => { try { chart.destroy(); } catch (_) { /* already gone */ } });
        _xpCharts = [];
    }

    function setXpStatsRange(serverId, value) {
        const days = parseInt(value, 10);
        if (!XP_STATS_RANGES.includes(days)) return;
        state.xpStatsDays = days;
        loadXpStats(serverId);
    }

    function fmtNum(n) {
        return Number(n || 0).toLocaleString('fr-FR');
    }

    function parseIsoDay(iso) {
        // 'YYYY-MM-DD' as a local date — new Date(iso) would parse it as UTC midnight.
        const [y, m, d] = iso.split('-').map(Number);
        return new Date(y, m - 1, d);
    }

    function formatVoiceMinutes(minutes) {
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return h ? `${fmtNum(h)} h ${String(m).padStart(2, '0')}` : `${m} min`;
    }

    async function loadXpStats(serverId) {
        const container = document.getElementById('xp-stats');
        if (!container) return;
        // A newer load (range change, re-render) supersedes this one.
        const seq = ++_xpStatsSeq;
        const stale = () => seq !== _xpStatsSeq || !container.isConnected;
        destroyXpCharts();
        container.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
        let data;
        try {
            const sid = encodeURIComponent(serverId);
            data = state.publicView
                ? await publicApi(`/api/public/${state.publicView.token}/xp/stats?days=${state.xpStatsDays}`)
                : await api(`/api/servers/${sid}/xp/stats?days=${state.xpStatsDays}`);
        } catch (e) {
            if (!stale()) {
                container.innerHTML = `<div class="empty-state"><h3>Erreur</h3><p>${escapeHtml(e.message || String(e))}</p></div>`;
            }
            return;
        }
        if (stale()) return;
        if (!data) {
            container.innerHTML = `<div class="empty-state"><h3>Accès refusé</h3><p>Statistiques réservées aux administrateurs de ce serveur.</p></div>`;
            return;
        }
        if (!data.summary?.members) {
            container.innerHTML = `<div class="empty-state"><h3>Aucune donnée</h3><p>Personne n'a encore gagné d'XP sur ce serveur.</p></div>`;
            return;
        }
        container.innerHTML = renderXpStats(data);
        try {
            await ensureChartJs();
        } catch (e) {
            if (stale()) return;
            container.querySelectorAll('.xp-chart-canvas').forEach(el => {
                el.innerHTML = `<div class="xp-chart-fallback">Graphique indisponible : Chart.js n'a pas pu être chargé.</div>`;
            });
            return;
        }
        if (stale()) return;
        drawXpCharts(data);
    }

    function renderXpStats(data) {
        const s = data.summary;
        const period = `${data.days} j`;
        const cards = [
            ['Membres classés', fmtNum(s.members)],
            ['XP total', fmtNum(s.total_xp)],
            [`XP · ${period}`, fmtNum(s.period_xp)],
            [`Membres actifs · ${period}`, fmtNum(s.active_members)],
            [`Messages · ${period}`, fmtNum(s.period_messages)],
            [`Vocal · ${period}`, formatVoiceMinutes(s.period_voice_minutes || 0)],
        ];
        return `
            <div class="stats-grid xp-stats-cards">
                ${cards.map(([label, value]) => `
                    <div class="stat-card">
                        <div class="stat-label">${escapeHtml(label)}</div>
                        <div class="stat-value">${escapeHtml(value)}</div>
                    </div>
                `).join('')}
            </div>
            <div class="xp-stats-grid">
                <div class="xp-panel">
                    <div class="xp-panel-title">Activité quotidienne</div>
                    <div class="xp-chart-canvas"><canvas id="xp-chart-activity"></canvas></div>
                </div>
                <div class="xp-panel">
                    <div class="xp-panel-title">Répartition des niveaux</div>
                    <div class="xp-chart-canvas"><canvas id="xp-chart-levels"></canvas></div>
                </div>
            </div>
            <div class="xp-panel">
                <div class="xp-panel-title">XP par jour de la semaine et par heure · ${period}</div>
                <div class="xp-scroll">${renderXpHeatmap(data.heatmap)}</div>
            </div>
            <div class="xp-panel">
                <div class="xp-panel-title">Top ${data.top.length} membres</div>
                <div class="xp-scroll">${renderXpTopTable(data.top)}</div>
            </div>
        `;
    }

    function renderXpHeatmap(grid) {
        const max = Math.max(0, ...grid.flat());
        const hours = Array.from({ length: 24 }, (_, h) =>
            `<div class="xp-heatmap-hour">${h % 3 === 0 ? `${h}h` : ''}</div>`).join('');
        const rows = grid.map((row, d) => `
            <div class="xp-heatmap-day">${XP_WEEKDAYS[d]}</div>
            ${row.map((xp, h) => {
                const title = escapeHtml(`${XP_WEEKDAYS[d]} ${h}h–${h + 1}h : ${fmtNum(xp)} XP`);
                if (!xp) return `<div class="xp-heatmap-cell zero" title="${title}"></div>`;
                const opacity = (0.15 + 0.85 * xp / max).toFixed(2);
                return `<div class="xp-heatmap-cell" style="opacity:${opacity}" title="${title}"></div>`;
            }).join('')}
        `).join('');
        return `<div class="xp-heatmap"><div></div>${hours}${rows}</div>`;
    }

    function renderXpTopTable(top) {
        const rows = top.map(m => {
            const pct = m.xp_to_next ? Math.min(100, Math.round(100 * m.xp_in_level / m.xp_to_next)) : 0;
            const handle = m.username && m.username !== m.display_name ? `@${m.username}` : '';
            return `
                <tr>
                    <td class="num">${XP_MEDALS[m.rank - 1] || m.rank}</td>
                    <td>
                        <div class="xp-top-name">${escapeHtml(m.display_name)}</div>
                        ${handle ? `<div class="xp-top-sub">${escapeHtml(handle)}</div>` : ''}
                    </td>
                    <td class="num">${fmtNum(m.level)}</td>
                    <td>
                        <div class="xp-progress" title="${pct} %"><div style="width:${pct}%"></div></div>
                        <div class="xp-top-sub">${fmtNum(m.xp_in_level)} / ${fmtNum(m.xp_to_next)} XP</div>
                    </td>
                    <td class="num">${fmtNum(m.xp)}</td>
                    <td class="num">${fmtNum(m.messages)}</td>
                </tr>
            `;
        }).join('');
        return `
            <table class="xp-top-table">
                <thead>
                    <tr>
                        <th class="num">#</th><th>Membre</th><th class="num">Niveau</th>
                        <th>Progression</th><th class="num">XP</th><th class="num">Messages</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;
    }

    function cssVar(name, fallback) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    }

    function withAlpha(hex, alpha) {
        const m = /^#([0-9a-f]{6})$/i.exec(hex);
        if (!m) return hex;
        const n = parseInt(m[1], 16);
        return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
    }

    // Shared by every Chart.js view (XP, Spotify): axis text, gridlines and font
    // follow the dashboard's theme tokens.
    function applyChartTheme(Chart) {
        Chart.defaults.color = cssVar('--text-secondary', '#b5bac1');
        Chart.defaults.borderColor = cssVar('--border', '#3f4147');
        Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    }

    function drawXpCharts(data) {
        destroyXpCharts();
        const Chart = window.Chart;
        const accent = cssVar('--accent', '#5865f2');
        const success = cssVar('--success', '#23a55a');
        const warning = cssVar('--warning', '#f0b232');
        applyChartTheme(Chart);
        const numTick = value => fmtNum(value);

        const days = data.daily.map(d => parseIsoDay(d.date));
        const activityCanvas = document.getElementById('xp-chart-activity');
        if (activityCanvas) {
            _xpCharts.push(new Chart(activityCanvas, {
                data: {
                    labels: days.map(d => d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })),
                    datasets: [
                        {
                            type: 'bar', label: 'XP messages', data: data.daily.map(d => d.message_xp),
                            backgroundColor: withAlpha(accent, 0.85), stack: 'xp', yAxisID: 'y', order: 2,
                        },
                        {
                            type: 'bar', label: 'XP vocal', data: data.daily.map(d => d.voice_xp),
                            backgroundColor: withAlpha(success, 0.85), stack: 'xp', yAxisID: 'y', order: 2,
                        },
                        {
                            type: 'line', label: 'Membres actifs', data: data.daily.map(d => d.active_users),
                            borderColor: warning, backgroundColor: warning, yAxisID: 'y1', order: 1,
                            tension: 0.3, borderWidth: 2, pointRadius: data.daily.length > 60 ? 0 : 2,
                        },
                    ],
                },
                options: {
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 12 } },
                        tooltip: {
                            callbacks: {
                                title: items => days[items[0].dataIndex].toLocaleDateString('fr-FR', {
                                    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
                                }),
                                label: item => `${item.dataset.label} : ${fmtNum(item.parsed.y)}`,
                            },
                        },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false }, ticks: { autoSkip: true, maxTicksLimit: 12, maxRotation: 0 } },
                        y: { stacked: true, beginAtZero: true, title: { display: true, text: 'XP' }, ticks: { callback: numTick } },
                        y1: {
                            position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
                            title: { display: true, text: 'Membres actifs' }, ticks: { precision: 0, callback: numTick },
                        },
                    },
                },
            }));
        }

        const levelsCanvas = document.getElementById('xp-chart-levels');
        if (levelsCanvas) {
            _xpCharts.push(new Chart(levelsCanvas, {
                type: 'bar',
                data: {
                    labels: data.levels.map(l => l.level),
                    datasets: [{
                        label: 'Membres', data: data.levels.map(l => l.users),
                        backgroundColor: withAlpha(accent, 0.85), borderRadius: 3,
                    }],
                },
                options: {
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                title: items => `Niveau ${items[0].label}`,
                                label: item => `${fmtNum(item.parsed.y)} membre${item.parsed.y > 1 ? 's' : ''}`,
                            },
                        },
                    },
                    scales: {
                        x: { grid: { display: false }, title: { display: true, text: 'Niveau' } },
                        y: { beginAtZero: true, ticks: { precision: 0 } },
                    },
                },
            }));
        }
    }

    async function editModuleConfigJson(serverId, moduleName) {
        const server = state.servers?.[serverId];
        const modConfig = server?.config?.[moduleName] || {};
        document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = `
            <textarea class="json-editor" id="json-editor" spellcheck="false"></textarea>
            <div class="json-error" id="json-error"></div>
        `;
        openModal(`${moduleName} — ${server?.name || serverId}`, modConfig, async (newData) => {
            const result = await api(`/api/servers/${serverId}/modules/${moduleName}`, {
                method: 'PUT',
                body: JSON.stringify({ config: newData }),
            });
            const syncSuffix = commandSyncSuffix(result);
            const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
            if (result?.reload?.reloaded && !result.reload.error) {
                toast(`Configuration sauvegardée et extension rechargée !${syncSuffix}`,
                    syncFailed ? 'warning' : 'success');
            } else if (result?.reload?.skipped || !result?.reload) {
                toast(`Configuration sauvegardée !${syncSuffix}`, syncFailed ? 'warning' : 'success');
            } else {
                toast(`Configuration sauvegardée, rechargement échoué : ${result.reload.error}`, 'warning');
            }
            await loadServers();
            if (isDeveloper()) await loadConfig();
            render();
        });
    }

    // ── Reload extensions ─────────────────────────────
    async function reloadAllExtensions() {
        const btn = document.getElementById('reload-all-btn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Rechargement...'; }
        try {
            const result = await api('/api/reload', { method: 'POST' });
            if (result) {
                const ok = result.reloaded?.length || 0;
                const fail = result.failed?.length || 0;
                const syncSuffix = commandSyncSuffix(result);
                const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
                if (fail > 0) {
                    toast(`${ok} rechargée(s), ${fail} en erreur : ${result.failed.map(f => f.name).join(', ')}`, 'error');
                } else {
                    toast(`${ok} extension(s) rechargée(s) — config appliquée !${syncSuffix}`, syncFailed ? 'warning' : 'success');
                }
            }
        } catch (e) { /* handled by api() */ }
        if (btn) { btn.disabled = false; btn.textContent = '🔄 Recharger tout'; }
    }

    async function reloadExtension(extName) {
        try {
            const result = await api(`/api/reload/${extName}`, { method: 'POST' });
            const syncSuffix = commandSyncSuffix(result);
            const syncFailed = !!(result?.commandSync && !result.commandSync.synced);
            toast(`${extName} rechargée — config appliquée !${syncSuffix}`, syncFailed ? 'warning' : 'success');
        } catch (e) { /* handled by api() */ }
    }

    // ── Public dashboards ─────────────────────────────
    // /public/{token}/{xp|spotify}: read-only, no login. Same renderers as the
    // module pages, fed by the unauthenticated /api/public/... endpoints
    // (src/webui/routes/public.py). Opt-in per guild via `publicDashboard`;
    // `token` is the guild's random secret, not its id.
    const PUBLIC_DASHBOARDS = {
        xp: { tab: '⭐ XP', title: 'Statistiques XP' },
        spotify: { tab: '🎵 Playlist Spotify', title: 'Playlist Spotify' },
    };

    function parsePublicPath(pathname = location.pathname) {
        // Any /public/... path stays on the public page; the server decides
        // whether the token is valid (an old /public/{guild id} link gets the 404).
        const m = /^\/public\/([^/]+)(?:\/(xp|spotify))?\/?$/.exec(pathname);
        return m ? { token: m[1], kind: m[2] || null } : null;
    }

    // Like api(), minus the session handling and toasts: errors are rendered in place.
    async function publicApi(url) {
        const resp = await fetch(url);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return resp.json();
    }

    async function initPublic(route) {
        state.publicView = { ...route, meta: null, error: null };
        render();
        try {
            state.publicView.meta = await publicApi(`/api/public/${route.token}`);
        } catch (e) {
            state.publicView.error = e.message || String(e);
        }
        const meta = state.publicView.meta;
        if (meta && !meta.dashboards.includes(state.publicView.kind)) {
            // /public/{token} or an unpublished kind: land on the first published one.
            state.publicView.kind = meta.dashboards[0];
            history.replaceState(null, '', `/public/${route.token}/${state.publicView.kind}`);
        }
        window.addEventListener('popstate', () => {
            const next = parsePublicPath();
            if (!next || !state.publicView?.meta?.dashboards.includes(next.kind)) {
                location.reload();
                return;
            }
            state.publicView.kind = next.kind;
            render();
        });
        render();
    }

    function navigatePublic(event, kind) {
        // Let modified clicks (new tab, ...) through to the plain link.
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return true;
        event.preventDefault();
        if (state.publicView.kind !== kind) {
            history.pushState(null, '', `/public/${state.publicView.token}/${kind}`);
            state.publicView.kind = kind;
            render();
        }
        return false;
    }

    function renderPublicPage() {
        const { meta, kind, error } = state.publicView;
        if (error) {
            document.title = 'Michel Bot';
            return `<div class="public-page"><div class="empty-state">
                <div class="icon">🔒</div><h3>Page indisponible</h3>
                <p>${escapeHtml(error)}</p></div></div>`;
        }
        if (!meta) {
            return `<div class="public-page"><div class="loading"><div class="spinner"></div></div></div>`;
        }
        const dash = PUBLIC_DASHBOARDS[kind];
        document.title = `${meta.name} — ${dash.title}`;
        const iconUrl = meta.icon
            ? `https://cdn.discordapp.com/icons/${meta.id}/${encodeURIComponent(meta.icon)}.png?size=128`
            : null;
        const tabs = meta.dashboards.map(k => `
            <a class="public-tab ${k === kind ? 'active' : ''}" href="/public/${state.publicView.token}/${k}"
               data-click="navigatePublic" data-click-args="${actionArgs(EVENT, k)}">${PUBLIC_DASHBOARDS[k].tab}</a>`).join('');
        const body = kind === 'xp' ? `
            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Activité XP</div>
                        <div class="card-subtitle">Classement et activité du serveur, heures de Paris.</div>
                    </div>
                    <div class="card-actions">
                        <select class="form-input" style="width:150px;" aria-label="Période" data-change="setXpStatsRange" data-change-args="${actionArgs(meta.id, VALUE)}">
                            ${XP_STATS_RANGES.map(d => `<option value="${d}" ${state.xpStatsDays === d ? 'selected' : ''}>${d} jours</option>`).join('')}
                        </select>
                    </div>
                </div>
                <div id="xp-stats" data-server-id="${meta.id}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>` : `
            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">📊 Playlist et votes</div>
                        <div class="card-subtitle">Playlist et votes tels que le bot les a enregistrés.</div>
                    </div>
                </div>
                <div id="spotify-stats" class="viz-root" data-server-id="${meta.id}">
                    <div class="loading"><div class="spinner"></div></div>
                </div>
            </div>`;
        return `
            <div class="public-page">
                <div class="public-header">
                    <div class="server-icon">
                        ${iconUrl ? `<img src="${iconUrl}" alt="">` : escapeHtml(meta.name.charAt(0) || '?')}
                    </div>
                    <div>
                        <h1>${escapeHtml(meta.name)}</h1>
                        <p>${escapeHtml(dash.title)}</p>
                    </div>
                </div>
                ${meta.dashboards.length > 1 ? `<nav class="public-tabs">${tabs}</nav>` : ''}
                ${body}
                <div class="public-footer">Michel Bot · données actualisées toutes les 5 minutes</div>
            </div>
        `;
    }

    // Module page: the public link is a per-guild secret, fetched (and minted
    // on first use) from the server, then shown in the modal.
    function publicPageButton(serverId, kind, modConfig) {
        if (!modConfig?.enabled || !modConfig?.publicDashboard) return '';
        return `<button class="btn btn-ghost btn-sm" data-click="showPublicLink" data-click-args="${actionArgs(serverId, kind)}"
                    title="Lien secret vers la page publique, accessible sans connexion">🌐 Page publique</button>`;
    }

    async function showPublicLink(serverId, kind, rotate = false) {
        let data;
        try {
            data = await api(`/api/servers/${encodeURIComponent(serverId)}/public-dashboard${rotate ? '/rotate' : ''}`,
                { method: 'POST' });
        } catch (e) { return; /* handled by api() */ }
        if (!data) return;
        const url = `${location.origin}/public/${data.token}/${kind}`;
        const overlay = document.getElementById('modal-overlay');
        document.getElementById('modal-title').textContent = `🌐 Page publique — ${PUBLIC_DASHBOARDS[kind].title}`;
        document.getElementById('modal-save-btn').style.display = 'none';
        overlay.querySelector('.modal-body').innerHTML = `
            <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px;">
                Toute personne disposant de ce lien peut consulter la page, sans connexion.
                Régénérer le lien désactive immédiatement l'ancien (pour les deux pages publiques du serveur).
            </p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <input id="public-link-url" class="form-input" readonly value="${escapeHtml(url)}"
                       style="flex:1;min-width:220px;" data-click="select-self" data-click-args="[{&quot;$&quot;:&quot;el&quot;}]">
                <button class="btn btn-primary btn-sm" data-click="copyPublicLink">📋 Copier</button>
                <a class="btn btn-ghost btn-sm" href="${escapeHtml(url)}" target="_blank" rel="noopener">Ouvrir ↗</a>
            </div>
            <div style="margin-top:16px;">
                <button class="btn btn-ghost btn-sm" data-click="rotatePublicLink" data-click-args="${actionArgs(serverId, kind)}">🔄 Régénérer le lien</button>
            </div>
        `;
        overlay.classList.add('active');
        if (rotate) toast('Nouveau lien généré : l\'ancien ne fonctionne plus.', 'success');
    }

    async function copyPublicLink() {
        const input = document.getElementById('public-link-url');
        if (!input) return;
        try {
            await navigator.clipboard.writeText(input.value);
            toast('Lien copié !', 'success');
        } catch (e) {
            input.select();
            toast('Copie impossible : sélectionnez le lien et copiez-le manuellement.', 'warning');
        }
    }

    function rotatePublicLink(serverId, kind) {
        if (!confirm('Régénérer le lien ? Les liens déjà partagés (XP et Spotify) cesseront de fonctionner.')) return;
        showPublicLink(serverId, kind, true);
    }

    async function logout() {
        try {
            await fetch('/auth/logout', { method: 'POST' });
        } finally {
            window.location = '/';
        }
    }

    // ── Utility ───────────────────────────────────────
    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML.replace(/"/g, '&quot;');
    }

    async function cleanupConfig() {
        try {
            // First do a dry run to show what would be removed
            const preview = await api('/api/cleanup-config?dry_run=true', { method: 'POST' });
            if (!preview) return;
            if (preview.removed_count === 0) {
                toast('La configuration est déjà propre — aucune clé superflue trouvée.', 'success');
                return;
            }
            const details = preview.removed.map(r => `• ${r.location} → ${r.key}`).join('\n');
            if (!confirm(
                `${preview.removed_count} clé(s) non présente(s) dans les schémas seront supprimée(s) :\n\n${details}\n\nConfirmer la suppression ?`
            )) return;
            // Execute the real cleanup
            const result = await api('/api/cleanup-config', { method: 'POST' });
            if (result) {
                toast(`${result.removed_count} clé(s) supprimée(s) de la configuration.`, 'success');
                await Promise.all([loadConfig(), loadServers()]);
                render();
            }
        } catch (e) { /* handled by api() */ }
    }

    async function downloadConfig() {
        try {
            const data = await api('/api/config');
            const blob = new Blob([JSON.stringify(data, null, 4)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'config.json';
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) { /* handled */ }
    }

    // ── Initialize ────────────────────────────────────
    async function init() {
        const publicRoute = parsePublicPath();
        if (publicRoute) {
            await initPublic(publicRoute);
            return;
        }
        await loadUserInfo();
        if (state.user) {
            const promises = [loadBotInfo()];
            if (isAdmin()) {
                promises.push(loadServers(), loadSchemas());
            }
            if (isDeveloper()) {
                promises.push(loadConfig(), loadLogs(), loadExtensions());
            }
            await Promise.all(promises);
        }

        // Handle redirect back from Spotify OAuth callback (hash-routed link).
        const hashParams = new URLSearchParams((location.hash.split('?')[1]) || '');
        if (hashParams.get('spotify_auth')) {
            const ok = hashParams.get('spotify_auth') === 'ok';
            toast(hashParams.get('msg') || (ok ? 'Spotify connecté' : 'Échec Spotify'), ok ? 'success' : 'error');
            if (isDeveloper()) {
                await loadSpotifyAuth();
                navigate('global');
            }
            // Clear the hash without re-triggering navigation.
            history.replaceState(null, '', location.pathname);
        }
        render();

        // Periodically refresh bot info
        setInterval(async () => {
            if (state.user) {
                await loadBotInfo();
                if (state.currentPage === 'home') render();
            }
        }, 30000);
    }


    // ── Actions ─────────────────────────────────────────
    // The page is served with a Content-Security-Policy that forbids inline
    // code (src/webui/security_headers.py): markup never carries an inline on… handler.
    // A control names what it does instead, data-<event>="action", with its
    // arguments as escaped JSON in data-<event>-args, built with actionArgs().
    // One listener per event type looks up the nearest such element and calls
    // the action from this allowlist. EL, CHECKED, VALUE, EVENT and dataArg()
    // stand for the element, its state and the event, resolved at that point.
    // An action returning false, or an element with data-prevent, cancels the
    // event's default (a link's navigation), as `return false` did inline.
    const EL = { $: 'el' };
    const CHECKED = { $: 'checked' };
    const VALUE = { $: 'value' };
    const EVENT = { $: 'event' };
    const dataArg = (key) => ({ $: 'data', k: key });

    function actionArgs(...args) {
        return escapeHtml(JSON.stringify(args));
    }

    function resolveActionArg(arg, el, event) {
        if (arg === null || typeof arg !== 'object' || !('$' in arg)) return arg;
        switch (arg.$) {
            case 'el': return el;
            case 'checked': return el.checked;
            case 'value': return el.value;
            case 'event': return event;
            case 'data': return el.dataset[arg.k];
            default: return undefined;
        }
    }

    const ACTIONS = {
        addCommandEntry, addCommandResponse, addEmbedEntry, addEmbedLink, addKeyValueMapEntry,
        addMessageEntry, addModelEntry, addNameMapEntry, addRoleMenuEntry, addRssFeedEntry,
        addSpotifyUserEntry, addStreamerEntry, addTeamEntry, addYoutubeChannelEntry,
        applyRssPreset, cancelEditShortLink, cleanupConfig, clearLogs, closeModal,
        closeRoleMenuBuilder, closeSidebar, copyPublicLink, copyShortLink, createShortLink,
        deleteRoleMenu, deleteShortLink, downloadConfig, downloadLogs, editGlobalSection,
        editGlobalSectionJson, editModuleConfigJson, gotoShortLinkPage, insertVariable,
        loadInfractions, loadSpotifyStats, loadXpStats, logout, navigate, navigatePublic,
        onLogLevelChange, onLogSearchInput, onLogVerboseChange, onMemberPickerChange,
        onMemberPickerSearch, onRssModeChange, openModelCatalog, openRoleMenuBuilder,
        publishEmbeds, refreshLogs, reloadAllExtensions, removeNameMapEntry,
        removeSpotifyMapEntry, republishBotMessage, resetFieldToDefault, revokeInfraction,
        rotatePublicLink, saveModal, saveModuleForm, saveRoleMenu, saveShortLink,
        scrollToBottom, searchShortLinks, setXpStatsRange, showPublicLink, startEditShortLink,
        startSpotifyAuth, toggleChannelPickerMode, toggleExtension, toggleMemberPickerMode,
        toggleModule, togglePasswordVisibility, toggleRssAdvanced, toggleSidebar,
        toggleStreamerPlanning, updateCommandTitle, updateEmbedColorText,
        // Small ones that were inline code:
        'stop': () => {},  // a toggle inside a clickable row: the row's action doesn't run
        'remove-closest': (selector, el) => el.closest(selector)?.remove(),
        'select-self': (el) => el.select(),
        'search-on-enter': (event) => { if (event.key === 'Enter') searchShortLinks(); },
        'refresh-ai-models': (el) => refreshAiModelSelects(el.closest('.schema-form')),
        'remove-model-entry': (el) => {
            const form = el.closest('.schema-form');
            el.closest('.model-entry').remove();
            refreshAiModelSelects(form);
        },
    };

    for (const type of ['click', 'change', 'input', 'mousedown', 'keydown']) {
        document.addEventListener(type, (event) => {
            const el = event.target instanceof Element ? event.target.closest(`[data-${type}]`) : null;
            if (!el) return;
            const name = el.dataset[type];
            const action = Object.hasOwn(ACTIONS, name) ? ACTIONS[name] : null;
            if (!action) {
                console.error('Action inconnue :', name);
                return;
            }
            const raw = el.dataset[`${type}Args`];
            const args = raw ? JSON.parse(raw).map((arg) => resolveActionArg(arg, el, event)) : [];
            const result = action(...args);
            if (result === false || el.hasAttribute('data-prevent')) event.preventDefault();
        });
    }

    init();
