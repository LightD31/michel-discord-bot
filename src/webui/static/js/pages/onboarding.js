// The "Pour bien démarrer" list (C1).
import { EL, actionArgs, registerActions } from '../core/actions.js';
import { api } from '../core/api.js';
import { render } from '../core/render.js';
import { html } from '../core/html.js';
import { routeHash } from '../core/router.js';
import { state } from '../core/state.js';
import { toast } from '../ui/toast.js';

// ── First steps (C1) ──────────────────────────────
// The "Pour bien démarrer" list on the server page, and the wizard at
// #/setup/<id>: a pack of modules, their channels and roles, a test of
// the welcome message, Michel's announcement. Every step is read from
// the server's state (src/webui/onboarding.py); hiding the list is the
// only thing stored.
export async function loadOnboarding(sid, { rerender = true } = {}) {
    try {
        const data = await api(`/api/servers/${encodeURIComponent(sid)}/onboarding`);
        state.onboarding = { ...(state.onboarding || {}), [sid]: data };
    } catch (e) {
        return;
    }
    if (!rerender || state.currentServerId !== sid) return;
    if (state.currentPage === 'server') {
        const slot = document.getElementById('onboarding-slot');
        if (slot) slot.innerHTML = onboardingHtml(sid);
    } else if (state.currentPage === 'setup') {
        render();
    }
}

export function onboardingStepAction(sid, step, steps) {
    const welcomeOn = steps.find(s => s.key === 'welcome')?.done;
    const module = (mod, label = 'Régler') => html`<a class="btn btn-ghost btn-sm" href="${routeHash('module', sid, mod)}"
            data-click="navigate" data-click-args="${actionArgs('module', sid, mod)}" data-prevent>${label}</a>`;
    switch (step.key) {
        case 'pack': return html`<button type="button" class="btn btn-primary btn-sm" data-click="openSetup" data-click-args="${actionArgs(sid, 1)}">🚀 Lancer l'assistant</button>`;
        case 'welcome': return module('moduleWelcome');
        case 'moderators': return module('moduleModeration');
        case 'welcome_test': return welcomeOn
            ? html`<button type="button" class="btn btn-ghost btn-sm" data-click="testWelcome" data-click-args="${actionArgs(sid, EL)}">✉️ Me l'envoyer</button>`
            : '';
        case 'announce': return html`<button type="button" class="btn btn-ghost btn-sm" data-click="openSetup" data-click-args="${actionArgs(sid, 4)}">Annoncer</button>`;
        default: return '';
    }
}

export function onboardingHtml(sid) {
    const data = state.onboarding?.[sid];
    if (!data) return html``;
    const steps = data.steps || [];
    const done = steps.filter(s => s.done).length;
    if (done === steps.length) return html``;
    if (data.dismissed) {
        return html`<p class="onboarding-hidden"><button type="button" class="btn-link" data-click="dismissOnboarding" data-click-args="${actionArgs(sid, false)}">
                🚀 Pour bien démarrer (${done}/${steps.length}) : réafficher</button></p>`;
    }
    const fresh = !steps.find(s => s.key === 'pack')?.done;
    const name = state.servers?.[sid]?.name || '';
    const items = steps.map(step => html`
            <li class="checklist-item ${step.done ? 'done' : ''}">
                <span class="checklist-mark" aria-hidden="true">${step.done ? '✓' : ''}</span>
                <span class="checklist-text">${step.label}
                    <span class="sr-only">${step.done ? '(fait)' : '(à faire)'}</span>
                    ${step.hint && html`<span class="checklist-hint">${step.hint}</span>`}</span>
                ${!step.done && onboardingStepAction(sid, step, steps)}
            </li>`);
    return html`
            <div class="card onboarding-card" id="onboarding-card">
                <div class="card-header">
                    <div class="card-title">Pour bien démarrer · ${done}/${steps.length}</div>
                    <button type="button" class="btn btn-ghost btn-sm" data-click="dismissOnboarding" data-click-args="${actionArgs(sid, true)}">Masquer</button>
                </div>
                ${fresh && html`<p class="onboarding-intro">Bienvenue sur ${name} 👋 Michel vient d'arriver : l'assistant active les bons modules pour ton type de communauté, en quatre étapes. Tout reste modifiable ensuite.</p>`}
                <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${steps.length}" aria-valuenow="${done}" aria-label="Étapes faites">
                    <div class="progress-fill" style="width:${Math.round(100 * done / Math.max(steps.length, 1))}%"></div></div>
                <ul class="checklist">${items}</ul>
            </div>`;
}

export async function dismissOnboarding(sid, dismissed) {
    try {
        await api(`/api/servers/${encodeURIComponent(sid)}/onboarding/dismiss`, {
            method: 'POST',
            body: JSON.stringify({ dismissed }),
        });
        toast(dismissed ? 'Liste masquée : tu la retrouves en haut de la page.' : 'Liste réaffichée.', 'success');
    } catch (e) { return; }
    await loadOnboarding(sid);
}

// The welcome messages as saved, sent to the admin by DM.
export async function testWelcome(sid, btn) {
    const texts = (state.servers?.[sid]?.config?.moduleWelcome?.welcomeMessageList || [])
        .map(m => (m?.text || '').trim()).filter(Boolean);
    if (!texts.length) {
        toast('Aucun message de bienvenue enregistré : écris-en un dans le module Bienvenue.', 'info');
        return;
    }
    if (btn) btn.disabled = true;
    try {
        const result = await api(`/api/servers/${encodeURIComponent(sid)}/template-test`, {
            method: 'POST',
            body: JSON.stringify({ module: 'moduleWelcome', field: 'welcomeMessageList', texts }),
        });
        toast(`${result.sent} message(s) de test envoyé(s) en MP.`, 'success');
        for (const warning of result.warnings || []) toast(warning, 'warning');
    } catch (e) { /* handled by api() */ }
    finally { if (btn) btn.disabled = false; }
    await loadOnboarding(sid);
}

registerActions({
    dismissOnboarding,
    testWelcome,
});
