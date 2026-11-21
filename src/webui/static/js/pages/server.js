// The server page: header, module cards, search and filters (C2).
import { CHECKED, VALUE, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { isDeveloper, loadConfig, loadServers } from '../core/data.js';
import { confirmDiscard } from '../core/dirty.js';
import { render } from '../core/render.js';
import { html, raw } from '../core/html.js';
import { routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { commandSyncSuffix } from '../forms/save.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';
import { renderAuditCard } from './audit.js';
import { onboardingHtml } from './onboarding.js';
import { overviewHtml } from './overview.js';

// ── Server Page ───────────────────────────────────
// ── Server page (C2) ──────────────────────────────
// The server first: how the week went, what needs fixing, what changed;
// then the modules as cards, searched and filtered (Actifs / Disponibles /
// Archivés). A module of an event that is over (schema.archived) shows
// under Archivés only, unless this server still has it on.
export const MODULE_FILTERS = [
    ['actifs', 'Actifs'],
    ['disponibles', 'Disponibles'],
    ['archives', 'Archivés'],
];
export const CATEGORY_ORDER = ['Communauté', 'Modération', 'Médias & Streaming', 'Esport & Jeux', 'Événements', 'Outils'];

export function fmtDay(iso) {
    return iso ? new Date(iso).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' }) : '';
}

export function foldText(text) {
    return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// Every module of the server, with what the grid needs to file and show it.
export function serverModules(sid) {
    const serverConfig = state.servers?.[sid]?.config || {};
    const configured = Object.keys(serverConfig).filter(k => {
        const v = serverConfig[k];
        return v && typeof v === 'object' && !Array.isArray(v);
    });
    const names = [...new Set([...configured, ...Object.keys(state.moduleSchemas)])].sort();
    return names.map(mod => {
        const schema = state.moduleSchemas[mod];
        const config = serverConfig[mod] || {};
        const isConfigured = mod in serverConfig;
        const noToggle = schema?.noToggle === true;
        const on = noToggle ? isConfigured && Object.keys(config).length > 0 : config.enabled === true;
        let group = 'disponibles';
        if (schema && on) group = 'actifs';
        else if (schema?.archived) group = 'archives';
        return {
            mod, schema, config, isConfigured, noToggle, on, group,
            label: schema?.label || mod,
            icon: schema?.icon || '🧩',
            category: schema?.category || 'Autre',
        };
    });
}

export function moduleFilterOf(sid, modules) {
    const chosen = state.moduleFilter?.[sid];
    if (chosen) return chosen;
    return modules.some(m => m.group === 'actifs') ? 'actifs' : 'disponibles';
}

export function setModuleFilter(sid, filter) {
    state.moduleFilter = { ...(state.moduleFilter || {}), [sid]: filter };
    refreshModuleBrowser(sid);
}

export function setModuleSearch(sid, text) {
    state.moduleSearch = { ...(state.moduleSearch || {}), [sid]: text };
    refreshModuleBrowser(sid);
}

export function refreshModuleBrowser(sid) {
    const el = document.getElementById('module-browser');
    if (el) el.innerHTML = moduleBrowserHtml(sid);
}

export function moduleCardHtml(sid, m) {
    if (!m.schema) {
        // Configured here, but no loaded extension registers it:
        // nothing to edit until the extension is enabled.
        return html`
                <div class="module-card inactive" title="L'extension de ce module n'est pas chargée">
                    <div class="module-card-head"><span class="module-card-icon">🧩</span>
                        <span class="module-name">${m.mod}</span></div>
                    <div class="module-card-desc">Extension inactive : rien à régler tant qu'elle n'est pas chargée.</div>
                </div>`;
    }
    const status = m.noToggle
        ? (m.on ? '📝 Réglé' : '⚪ Pas encore réglé')
        : m.on ? '✅ Activé'
            : m.schema.archived ? '🗄️ Archivé'
                : m.isConfigured ? '⏸ Désactivé' : '⚪ Disponible';
    const changed = state.overview?.[sid]?.lastChanges?.[m.mod];
    return html`
            <div class="module-card ${m.on ? 'on' : ''}" data-click="navigate" data-click-args="${actionArgs('module', sid, m.mod)}">
                <div class="module-card-head">
                    <span class="module-card-icon" aria-hidden="true">${m.icon}</span>
                    <a class="module-name" href="${routeHash('module', sid, m.mod)}" data-click="navigate" data-click-args="${actionArgs('module', sid, m.mod)}" data-prevent>${m.label}</a>
                    ${!m.noToggle && html`
                    <label class="toggle" data-click="stop">
                        <input type="checkbox" ${m.on ? 'checked' : ''} aria-label="Activer le module ${m.label}"
                            data-change="toggleModule" data-change-args="${actionArgs(sid, m.mod, CHECKED)}">
                        <span class="toggle-slider"></span>
                    </label>`}
                </div>
                <div class="module-card-desc">${m.schema.description || ''}</div>
                <div class="module-card-foot">
                    <span class="module-status">${status}</span>
                    ${changed && html`<span class="module-status" title="${new Date(changed).toLocaleString('fr-FR')}">modifié le ${fmtDay(changed)}</span>`}
                </div>
            </div>`;
}

export function moduleBrowserHtml(sid) {
    const modules = serverModules(sid);
    const search = foldText(state.moduleSearch?.[sid]).trim();
    const filter = moduleFilterOf(sid, modules);
    const counts = Object.fromEntries(MODULE_FILTERS.map(([key]) => [key, modules.filter(m => m.group === key).length]));
    // A search looks through every module, whatever the filter.
    const shown = search
        ? modules.filter(m => foldText(`${m.label} ${m.schema?.description || ''} ${m.mod}`).includes(search))
        : modules.filter(m => m.group === filter);
    const chips = MODULE_FILTERS.map(([key, label]) => html`
            <button type="button" class="filter-chip ${!search && key === filter ? 'active' : ''}" aria-pressed="${String(!search && key === filter)}"
                data-click="setModuleFilter" data-click-args="${actionArgs(sid, key)}">${label} <span class="filter-count">${counts[key]}</span></button>`);
    let body;
    if (!shown.length) {
        body = html`<p class="module-empty">${search
            ? 'Aucun module ne correspond à ta recherche.'
            : filter === 'actifs' ? 'Aucun module activé pour l\'instant : choisis-en dans « Disponibles », ou lance l\'assistant.'
                : filter === 'archives' ? 'Aucun module archivé.' : 'Tous les modules sont déjà activés.'}</p>`;
    } else {
        const grouped = {};
        for (const m of shown) (grouped[m.category] ||= []).push(m);
        const order = (c) => { const i = CATEGORY_ORDER.indexOf(c); return i === -1 ? 99 : i; };
        body = Object.keys(grouped).sort((a, b) => order(a) - order(b)).map(cat => html`
                <div class="module-category-header">${cat}</div>
                <div class="module-grid">${grouped[cat].map(m => moduleCardHtml(sid, m))}</div>`);
    }
    return html`<div class="filter-chips" role="group" aria-label="Filtrer les modules">${chips}</div>${body}`;
}

export function renderServerPage() {
    const sid = state.currentServerId;
    const server = state.servers?.[sid];
    if (!server) {
        return html`<div class="loading"><div class="spinner"></div> Chargement...</div>`;
    }
    const active = serverModules(sid).filter(m => m.group === 'actifs').length;
    const iconUrl = server.icon
        ? `https://cdn.discordapp.com/icons/${sid}/${server.icon}.png?size=128`
        : null;

    return html`
            <div class="page-header">
                <div style="display:flex; align-items:center; gap:16px; flex-wrap:wrap;">
                    <div class="server-icon" style="width:48px;height:48px;font-size:20px;">
                        ${iconUrl ? html`<img src="${iconUrl}" alt="">` : server.name?.charAt(0) || '?'}
                    </div>
                    <div>
                        <h2>${server.name || sid}</h2>
                        <p title="ID : ${sid}">${active ? `${active} module${active > 1 ? 's' : ''} actif${active > 1 ? 's' : ''}` : 'Aucun module actif'}</p>
                    </div>
                </div>
            </div>

            <div id="onboarding-slot">${onboardingHtml(sid)}</div>

            <div class="card" id="overview-card">
                <div class="card-header">
                    <div class="card-title">Aperçu</div>
                </div>
                <div id="overview-body">${overviewHtml(sid)}</div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Modules</div>
                    <input type="search" class="form-input module-search" placeholder="Chercher un module…" aria-label="Chercher un module"
                        value="${state.moduleSearch?.[sid] || ''}"
                        data-input="setModuleSearch" data-input-args="${actionArgs(sid, VALUE)}">
                </div>
                <div id="module-browser">${moduleBrowserHtml(sid)}</div>
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

            <div class="card" id="health-card">
                <div class="card-header">
                    <div class="card-title">Santé</div>
                </div>
                <p style="color:var(--text-muted); margin:0 0 12px;">
                    Ce que le bot n'arrive pas à faire sur ce serveur : salons où il lui manque des
                    droits, flux RSS en échec, tâches qui échouent.
                </p>
                <div id="health-report"><div class="loading"><div class="spinner"></div></div></div>
            </div>

            ${raw(renderAuditCard())}
        `;
}

export async function toggleModule(serverId, moduleName, enabled) {
    const label = state.moduleSchemas[moduleName]?.label || moduleName;
    if (!(await askConfirm(`${enabled ? 'Activer' : 'Désactiver'} le module ${label} sur ce serveur ?`,
            { title: enabled ? 'Activer le module' : 'Désactiver le module', confirm: enabled ? 'Activer' : 'Désactiver', danger: !enabled }))
        || !(await confirmDiscard())) {
        render();  // put the switch back
        return;
    }
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

registerActions({
    setModuleFilter,
    setModuleSearch,
    toggleModule,
});
