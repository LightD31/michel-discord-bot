// The global settings page.
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { loadConfig, loadServers } from '../core/data.js';
import { render } from '../core/render.js';
import { html } from '../core/html.js';
import { state } from '../core/state.js';
import { renderSpotifyAuthPanel } from '../features/spotify-auth.js';
import { renderTwitchAuthPanel } from '../features/twitch-auth.js';
import { _memberCache, getDiscord2NameMap } from '../forms/pickers/members.js';
import { collectFormValues, renderSchemaForm } from '../forms/schema-form.js';
import { populateAccountNames } from '../forms/widgets/accounts.js';
import { _aiGuildModelsCache, refreshAiModelSelects } from '../forms/widgets/models.js';
import { askConfirm } from '../ui/confirm.js';
import { onModalSave, openModal, showModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';
import { renderAuditCard } from './audit.js';

// ── Global Config Page ────────────────────────────
export function renderGlobalConfig() {
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

            <div class="card card-bordered">
                <div class="card-header">
                    <div>
                        <div class="card-title">🗂️ Fichier de configuration</div>
                        <div class="card-subtitle">Toute la config est stockée dans <code>config/config.json</code></div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-ghost btn-sm" data-click="downloadConfig">
                            ⬇️ Exporter
                        </button>
                        <button class="btn btn-ghost btn-sm btn-ghost-danger" data-click="cleanupConfig" title="Supprimer les clés non définies dans les schémas (aperçu d'abord)">
                            🧹 Nettoyer…
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
                        ${section === 'twitch' ? renderTwitchAuthPanel() : ''}
                    </div>
                `;
        }).join('')}
            ${renderAuditCard()}
        `;
}

// One row of a list setting, as the preview shows it: its parts, most telling first.
function previewList(label, count, items, empty) {
    return html`
            <div class="preview-row preview-row-list">
                <span class="preview-label">${label} (${count})</span>
                <div class="preview-list">
                    ${items.length ? items : html`<span class="preview-muted">${empty}</span>`}
                </div>
            </div>`;
}

function previewItem(...parts) {
    return html`<div class="preview-item">${parts}</div>`;
}

const strong = (text, wide = '') => html`<span class="preview-strong ${wide}">${text}</span>`;
const mono = (text, wide = '') => html`<span class="preview-mono ${wide}">${text}</span>`;
const muted = (text) => html`<span class="preview-muted">${text}</span>`;

// Each field of a settings section, read-only (C8: html`…`, no hand escaping, P17).
export function renderSchemaPreview(schema, values) {
    const fields = schema.fields || {};
    const rows = [];
    for (const [key, field] of Object.entries(fields)) {
        if (field.hidden) continue;
        const val = values[key];
        const hasVal = val !== undefined && val !== null;
        const label = field.label || key;
        const type = field.type || 'string';
        const isMap = typeof val === 'object' && val !== null && !Array.isArray(val);

        if (type === 'messagelist' && Array.isArray(val) && val.length) {
            rows.push(previewList(label, val.length, val.map((msg) => {
                const text = typeof msg === 'string' ? msg : String(msg?.text ?? '');
                const weight = (msg && typeof msg === 'object' && msg.weight !== undefined) ? msg.weight : 1;
                return previewItem(html`<span class="preview-grow">${text}</span>`, muted(`×${weight}`));
            })));
            continue;
        }
        if (type === 'models' && Array.isArray(val) && val.length) {
            rows.push(previewList(label, val.length, val.map(m => previewItem(
                strong(m.provider || '', 'w-80'), mono(m.model_id || '', 'preview-grow'), html`<span>${m.display_name || ''}</span>`))));
            continue;
        }
        if (type === 'discord2name' && isMap) {
            const entries = Object.entries(val);
            rows.push(previewList(label, entries.length,
                entries.map(([id, name]) => previewItem(mono(id, 'w-180'), strong(String(name)))), 'Aucun mapping configuré'));
            continue;
        }
        if (type === 'teams' && Array.isArray(val) && val.length) {
            rows.push(previewList(label, val.length, val.map(t => previewItem(
                strong(t.name || '???', 'w-120'), muted(t.game || 'valorant'), t.vlrTeamId && mono(`VLR#${t.vlrTeamId}`)))));
            continue;
        }
        if (type === 'spotifymap' && Array.isArray(val) && val.length) {
            const nameMap = getDiscord2NameMap();
            const known = _memberCache[state.currentServerId]?.byId || new Map();
            const resolveName = (u) => {
                if (u.name) return u.name;
                if (u.discordId && nameMap[u.discordId]) return nameMap[u.discordId];
                const m = known.get(String(u.discordId));
                return m ? (m.display_name || m.username || '') : '';
            };
            rows.push(previewList(label, val.length, val.map(u => previewItem(
                mono(u.spotifyId || '???', 'w-120'), strong(resolveName(u), 'w-100'), mono(u.discordId || '')))));
            continue;
        }
        if (type === 'rssfeedmap' && isMap) {
            const feeds = Object.entries(val);
            rows.push(previewList(label, feeds.length, feeds.map(([feedId, cfg]) => {
                const c = (typeof cfg === 'string') ? { url: cfg } : (cfg || {});
                return previewItem(
                    html`<span class="preview-strong w-140">${feedId}${c.label && ` — ${c.label}`}${c.mode === 'embed' && html` <span class="preview-badge">[embed]</span>`}</span>`,
                    mono(String(c.url || '—'), 'preview-grow'));
            }), 'Aucun flux configuré'));
            continue;
        }
        if (type === 'youtubechannelmap' && isMap) {
            const channels = Object.entries(val);
            rows.push(previewList(label, channels.length, channels.map(([handle, cfg]) => {
                const flags = [cfg?.shorts && 'Shorts', cfg?.live && 'Live', cfg?.vod !== false && 'VOD'].filter(Boolean);
                return previewItem(strong(`${handle}${cfg?.label ? ` (${cfg.label})` : ''}`, 'w-140'),
                    mono(flags.join(' · ') || '—', 'preview-grow'));
            }), 'Aucune chaîne configurée'));
            continue;
        }
        if (type === 'streamermap' && isMap) {
            const streamers = Object.entries(val);
            rows.push(previewList(label, streamers.length, streamers.map(([login, cfg]) => {
                const extras = [
                    cfg?.twitchPlanningChannelId && `planning #${cfg.twitchPlanningChannelId}`,
                    cfg?.twitchNotificationChannelId && `notif #${cfg.twitchNotificationChannelId}`,
                ].filter(Boolean).join(' · ');
                return previewItem(strong(login, 'w-120'), mono(extras || '—', 'preview-grow'));
            }), 'Aucun streamer configuré'));
            continue;
        }

        let shown = '—';
        if (hasVal) {
            if (field.secret) shown = '••••••••';
            else if (typeof val === 'boolean') shown = val ? '✅ Oui' : '❌ Non';
            else if (Array.isArray(val)) shown = val.join(', ');
            else if (typeof val === 'object') shown = JSON.stringify(val);
            else shown = String(val);
        }
        rows.push(html`
                <div class="preview-row">
                    <span class="preview-label">${label}</span>
                    <span class="preview-value ${hasVal ? '' : 'empty'}">${shown}</span>
                </div>`);
    }
    for (const key of Object.keys(values)) {
        if (key in fields) continue;
        rows.push(html`
                <div class="preview-row">
                    <span class="preview-label preview-unknown">${key} <small>(non défini dans le schéma)</small></span>
                    <span class="preview-value">${JSON.stringify(values[key])}</span>
                </div>`);
    }
    return html`<div class="schema-preview">${rows}</div>`;
}

const SECRET_KEY = /token|secret|password/i;

export function renderConfigPreview(obj, prefix = '') {
    const rows = Object.entries(obj).map(([key, value]) => {
        const fullKey = prefix ? `${prefix}.${key}` : key;
        if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
            return html`<div class="kv-key kv-section">▸ ${key}</div>${renderConfigPreview(value, fullKey)}`;
        }
        const shown = typeof value === 'string' && SECRET_KEY.test(key) ? '••••••••' : JSON.stringify(value);
        return html`<div class="kv-key">${key}</div><div class="kv-value"><input type="text" value="${shown}" readonly></div>`;
    });
    return html`<div class="kv-grid">${rows}</div>`;
}

export async function editGlobalSection(section) {
    const globalConfig = state.config?.config || {};
    const sectionData = globalConfig[section] || {};
    const schema = state.globalSchemas[section];

    if (schema) {
        const formId = `global-${section}`;
        const formHtml = renderSchemaForm(formId, schema, sectionData);
        document.getElementById('modal-title').textContent = `${schema.label || section}`;
        document.getElementById('modal-overlay').querySelector('.modal-body').innerHTML = formHtml;
        refreshAiModelSelects(document.getElementById('modal-overlay'));
        populateAccountNames(document.getElementById('modal-overlay'));
        const errEl = document.getElementById('json-error');
        if (errEl) errEl.textContent = '';
        onModalSave(async () => {
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
        });
        showModal();
    } else {
        editGlobalSectionJson(section);
    }
}

export async function editGlobalSectionJson(section) {
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

export async function cleanupConfig() {
    try {
        // First do a dry run to show what would be removed
        const preview = await api('/api/cleanup-config?dry_run=true', { method: 'POST' });
        if (!preview) return;
        if (preview.removed_count === 0) {
            toast('La configuration est déjà propre — aucune clé superflue trouvée.', 'success');
            return;
        }
        const details = preview.removed.map(r => `• ${r.location} → ${r.key}`).join('\n');
        if (!(await askConfirm(
            `${preview.removed_count} clé(s) absente(s) des schémas seront supprimée(s) :\n\n${details}`,
            { title: 'Nettoyer la configuration ?', confirm: 'Supprimer ces clés', danger: true },
        ))) return;
        // Execute the real cleanup
        const result = await api('/api/cleanup-config', { method: 'POST' });
        if (result) {
            toast(`${result.removed_count} clé(s) supprimée(s) de la configuration.`, 'success');
            await Promise.all([loadConfig(), loadServers()]);
            render();
        }
    } catch (e) { /* handled by api() */ }
}

export async function downloadConfig() {
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

registerActions({
    cleanupConfig,
    downloadConfig,
    editGlobalSection,
    editGlobalSectionJson,
});
