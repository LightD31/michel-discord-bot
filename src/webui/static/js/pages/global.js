// The global settings page.
import { actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { loadConfig, loadServers } from '../core/data.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { escapeHtml } from '../core/util.js';
import { renderSpotifyAuthPanel } from '../features/spotify-auth.js';
import { _memberCache, getDiscord2NameMap } from '../forms/pickers/members.js';
import { collectFormValues, renderSchemaForm } from '../forms/schema-form.js';
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

            <div class="card" style="margin-bottom:16px; border: 1px solid var(--border);">
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
                    </div>
                `;
        }).join('')}
            ${renderAuditCard()}
        `;
}

export function renderSchemaPreview(schema, values) {
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

export function renderConfigPreview(obj, prefix = '') {
    let html = '<div class="kv-grid">';
    for (const [key, value] of Object.entries(obj)) {
        const fullKey = prefix ? `${prefix}.${key}` : key;
        if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
            html += `<div class="kv-key" style="grid-column: 1 / -1; color: var(--accent); margin-top: 8px;">▸ ${escapeHtml(key)}</div>`;
            html += renderConfigPreview(value, fullKey);
        } else {
            const displayValue = typeof value === 'string' && (key.toLowerCase().includes('token') || key.toLowerCase().includes('secret') || key.toLowerCase().includes('password'))
                ? '••••••••'
                : JSON.stringify(value);
            html += `
                    <div class="kv-key">${escapeHtml(key)}</div>
                    <div class="kv-value"><input type="text" value="${escapeHtml(displayValue)}" readonly></div>
                `;
        }
    }
    html += '</div>';
    return html;
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
