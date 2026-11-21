// The first-steps wizard (C1).
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { loadServers } from '../core/data.js';
import { html, raw } from '../core/html.js';
import { render } from '../core/render.js';
import { navigate, routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { commandSyncSuffix } from '../forms/save.js';
import { collectFormValues, mdInline, renderSchemaForm } from '../forms/schema-form.js';
import { askConfirm } from '../ui/confirm.js';
import { toast } from '../ui/toast.js';
import { loadOnboarding } from './onboarding.js';

// ── The wizard ────────────────────────────────────
export const SETUP_STEPS = ['Type de communauté', 'Salons et rôles', 'Message de bienvenue', 'Annonce'];

export function openSetup(sid, step = 1) {
    state.setup = { serverId: sid, step, pack: state.setup?.serverId === sid ? state.setup.pack : null, result: null };
    navigate('setup', sid);
}

export function setupState(sid) {
    if (state.setup?.serverId !== sid) state.setup = { serverId: sid, step: 1, pack: null, result: null };
    return state.setup;
}

export function setupGo(step) {
    state.setup.step = step;
    render();
    document.querySelector('.main')?.scrollTo?.(0, 0);
}

export function choosePack(key) {
    state.setup.pack = key;
    render();
}

export function setupLeave(sid) {
    state.setup = null;
    navigate('server', sid);
}

export function setupContinueFromPacks(sid) {
    const setup = state.setup;
    if (!setup?.pack) return;
    if (setup.pack === 'sur-mesure') {
        state.moduleFilter = { ...(state.moduleFilter || {}), [sid]: 'disponibles' };
        toast('Voici tous les modules : active ceux qui te servent.', 'info');
        setupLeave(sid);
        return;
    }
    setupGo(2);
}

export function setupPack(sid) {
    return (state.onboarding?.[sid]?.packs || []).find(p => p.key === state.setup?.pack);
}

export function moduleNames(mods) {
    return mods.map(m => state.moduleSchemas[m]?.label || m);
}

export function setupPacksHtml(sid, data) {
    const total = Object.values(state.moduleSchemas).filter(s => !s.archived).length;
    const cards = (data.packs || []).map(pack => {
        const chosen = state.setup.pack === pack.key;
        const lines = pack.modules.length
            ? moduleNames(pack.modules).map(l => html`<li>${l}</li>`)
            : html`<li>Le catalogue complet des ${total} modules</li>`;
        return html`<button type="button" class="pack-card ${chosen ? 'chosen' : ''}" role="radio" aria-checked="${String(chosen)}"
                    data-click="choosePack" data-click-args="${actionArgs(pack.key)}">
                <span class="pack-icon" aria-hidden="true">${pack.icon}</span>
                <span class="pack-title">${pack.label}</span>
                <span class="pack-tagline">${pack.tagline}</span>
                <ul class="pack-modules">${lines}</ul>
            </button>`;
    });
    return html`<div class="pack-grid" role="radiogroup" aria-label="Type de communauté">${cards}</div>
        <div class="setup-actions">
            <button type="button" class="btn btn-ghost" data-click="setupLeave" data-click-args="${actionArgs(sid)}">Passer</button>
            <button type="button" class="btn btn-primary" ${state.setup.pack ? '' : 'disabled'}
                data-click="setupContinueFromPacks" data-click-args="${actionArgs(sid)}">Continuer →</button>
        </div>`;
}

export function setupAsksHtml(sid) {
    const pack = setupPack(sid);
    if (!pack) return html`<p>Choisis d'abord un type de communauté.</p>`;
    const serverConfig = state.servers?.[sid]?.config || {};
    const kept = pack.modules.filter(m => m in serverConfig);
    const fields = {};
    for (const ask of pack.asks) {
        if (ask.module in serverConfig) continue;
        const label = state.moduleSchemas[ask.module]?.label || ask.module;
        fields[`${ask.module}__${ask.field}`] = { ...ask.meta, label: `${label} · ${ask.meta.label || ask.field}` };
    }
    const willEnable = pack.modules.filter(m => !(m in serverConfig));
    return html`
            <p>Le pack <strong>${pack.label}</strong> active : ${moduleNames(willEnable).join(', ') || 'rien de plus'}.</p>
            ${kept.length > 0 && html`<p class="setup-note">Déjà réglés sur ce serveur, laissés tels quels : ${moduleNames(kept).join(', ')}.</p>`}
            ${Object.keys(fields).length > 0 && html`<p class="setup-note">Choisis où Michel poste. Un module dont le salon requis reste vide est laissé de côté : tu pourras l'activer plus tard.</p>
                   ${raw(renderSchemaForm('setup-asks', { fields }, {}, { serverId: sid }))}`}
            <div class="setup-actions">
                <button type="button" class="btn btn-ghost" data-click="setupGo" data-click-args="${actionArgs(1)}">← Retour</button>
                <button type="button" class="btn btn-primary" ${willEnable.length ? '' : 'disabled'}
                    data-click="applyPack" data-click-args="${actionArgs(sid, EL)}">Activer ces modules</button>
            </div>`;
}

export async function applyPack(sid, btn) {
    const values = collectFormValues('setup-asks');
    const answers = {};
    for (const [key, value] of Object.entries(values)) {
        const [module, field] = key.split('__');
        (answers[module] ||= {})[field] = value;
    }
    if (btn) btn.disabled = true;
    let result;
    try {
        result = await api(`/api/servers/${encodeURIComponent(sid)}/onboarding/pack`, {
            method: 'POST',
            body: JSON.stringify({ pack: state.setup.pack, answers }),
        });
    } catch (e) {
        if (btn) btn.disabled = false;
        return;
    }
    const n = result.applied.length;
    const failed = !!result.reload?.error || !!(result.commandSync && !result.commandSync.synced);
    toast(n ? `${n} module${n > 1 ? 's' : ''} activé${n > 1 ? 's' : ''}${commandSyncSuffix(result)}` : 'Aucun module activé.',
        failed ? 'warning' : 'success');
    state.setup.result = result;
    await loadServers();
    await loadOnboarding(sid, { rerender: false });
    setupGo(3);
}

export function setupResultHtml(sid) {
    const result = state.setup.result;
    if (!result) return html``;
    const pack = setupPack(sid);
    const link = (mod) => html`<a href="${routeHash('module', sid, mod)}" data-click="navigate" data-click-args="${actionArgs('module', sid, mod)}" data-prevent>${state.moduleSchemas[mod]?.label || mod}</a>`;
    const list = (items, render) => items.map((item, i) => html`${i ? ', ' : ''}${render(item)}`);
    const skipped = result.skipped || [];
    const later = pack?.later || [];
    return html`<div class="setup-result">
            ${result.applied.length > 0 && html`<p>✅ Activés : ${list(result.applied, link)}.</p>`}
            ${skipped.length > 0 && html`<p>Laissés de côté : ${list(skipped, s => html`${link(s.module)} (${s.reason})`)}.</p>`}
            ${later.length > 0 && html`<p>À régler ensuite, à ton rythme : ${list(later, link)}.</p>`}
        </div>`;
}

export function setupWelcomeHtml(sid) {
    const welcome = state.servers?.[sid]?.config?.moduleWelcome;
    const on = welcome?.enabled && welcome?.welcomeChannelId;
    return html`${setupResultHtml(sid)}
            ${on
            ? html`<p>Michel accueille chaque nouveau membre. Reçois le message en MP pour voir ce qu'il dira, avec toi à la place du nouveau.</p>
                   <button type="button" class="btn btn-ghost" data-click="testWelcome" data-click-args="${actionArgs(sid, EL)}">✉️ Me l'envoyer</button>
                   <p class="setup-note">Pour changer le texte : module <a href="${routeHash('module', sid, 'moduleWelcome')}" data-click="navigate" data-click-args="${actionArgs('module', sid, 'moduleWelcome')}" data-prevent>Bienvenue</a>.</p>`
            : html`<p>Le module Bienvenue n'est pas activé ici : rien à tester. Tu peux passer.</p>`}
            <div class="setup-actions">
                <button type="button" class="btn btn-primary" data-click="setupGo" data-click-args="${actionArgs(4)}">Continuer →</button>
            </div>`;
}

export function setupAnnounceHtml(sid, data) {
    const field = { label: 'Salon de l\'annonce', type: 'channel', required: true,
        description: 'Là où Michel se présente. Il te faut le droit d\'y écrire.' };
    // mdInline() escapes the text before it adds its own markup.
    const lines = String(data.announcement || '').split('\n')
        .map((line, i) => html`${i ? raw('<br>') : ''}${raw(mdInline(line) || '&nbsp;')}`);
    return html`
            <p>Michel se présente aux membres et liste ce qu'il fait ici. Facultatif : tu peux terminer sans annoncer.</p>
            ${raw(renderSchemaForm('setup-announce', { fields: { channelId: field } }, {}, { serverId: sid }))}
            <div class="announce-preview" aria-label="Aperçu de l'annonce">
                <div class="announce-title">👋 Michel arrive sur le serveur</div>
                <div class="announce-text">${lines}</div>
            </div>
            <div class="setup-actions">
                <button type="button" class="btn btn-ghost" data-click="setupLeave" data-click-args="${actionArgs(sid)}">Terminer</button>
                <button type="button" class="btn btn-primary" data-click="announceMichel" data-click-args="${actionArgs(sid, EL)}">Publier l'annonce</button>
            </div>`;
}

export async function announceMichel(sid, btn) {
    const { channelId } = collectFormValues('setup-announce');
    if (!channelId) {
        toast('Choisis d\'abord le salon de l\'annonce.', 'info');
        return;
    }
    if (!(await askConfirm('Michel va poster son annonce dans ce salon, visible de tous les membres qui y ont accès.',
        { title: 'Annoncer Michel', confirm: 'Publier' }))) return;
    if (btn) btn.disabled = true;
    try {
        await api(`/api/servers/${encodeURIComponent(sid)}/onboarding/announce`, {
            method: 'POST',
            body: JSON.stringify({ channelId }),
        });
    } catch (e) {
        if (btn) btn.disabled = false;
        return;
    }
    toast('Annonce publiée 🎉', 'success');
    setupLeave(sid);
}

export function renderSetupPage() {
    const sid = state.currentServerId;
    const server = state.servers?.[sid];
    const data = state.onboarding?.[sid];
    if (!server || !data) {
        if (server && !data) loadOnboarding(sid);
        return html`<div class="loading"><div class="spinner"></div> Chargement...</div>`;
    }
    const setup = setupState(sid);
    const step = setup.step;
    const bodies = {
        1: () => setupPacksHtml(sid, data),
        2: () => setupAsksHtml(sid),
        3: () => setupWelcomeHtml(sid),
        4: () => setupAnnounceHtml(sid, data),
    };
    const intros = {
        1: 'Quel genre de communauté es-tu ? Michel active les bons modules pour toi, modifiables ensuite.',
        2: 'Où Michel poste-t-il ? Choisis les salons et les rôles des modules du pack.',
        3: 'Vérifie le message que reçoit chaque nouveau membre.',
        4: 'Dernière étape : présente Michel aux membres.',
    };
    return html`
            <div class="page-header">
                <h2>Bienvenue sur ${server.name || sid} 👋</h2>
                <p>${intros[step]}</p>
            </div>
            <ol class="setup-progress" aria-label="Étapes">
                ${SETUP_STEPS.map((label, i) => html`<li class="${i + 1 < step ? 'done' : i + 1 === step ? 'current' : ''}" ${i + 1 === step && raw('aria-current="step"')}>
                    <span class="setup-bar"></span><span class="setup-step-label">${i + 1}. ${label}</span></li>`)}
            </ol>
            <div class="card setup-card">${bodies[step]()}</div>`;
}

registerActions({
    announceMichel,
    applyPack,
    choosePack,
    openSetup,
    setupContinueFromPacks,
    setupGo,
    setupLeave,
});
