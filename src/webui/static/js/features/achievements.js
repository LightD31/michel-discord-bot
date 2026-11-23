// The Succès module page's card: each badge, in play or not, and how many won it (P25).
import { api } from '../core/api.js';
import { html } from '../core/html.js';

export function achievementsCardHtml(sid) {
    return html`
        <div class="u-mt-16 card">
            <div class="card-header">
                <div>
                    <div class="card-title">🏆 Badges du serveur</div>
                    <div class="card-subtitle">Combien de membres ont débloqué chaque badge. Un badge hors jeu n'est ni vérifié ni affiché.</div>
                </div>
            </div>
            <div id="achievements-badges" data-server-id="${sid}"><div class="loading"><div class="spinner"></div></div></div>
        </div>`;
}

export async function loadAchievements(sid) {
    const el = document.getElementById('achievements-badges');
    if (!el) return;
    let data;
    try {
        data = await api(`/api/servers/${encodeURIComponent(sid)}/achievements`);
    } catch (e) {
        el.innerHTML = String(html`<p class="me-empty">Les badges sont indisponibles pour le moment : réessaie dans un instant.</p>`);
        return;
    }
    el.innerHTML = String(html`<ul class="badge-stats">${data.badges.map(b => html`
        <li class="badge-stat ${b.off ? 'is-off' : ''}">
            <span class="me-badge-emoji" aria-hidden="true">${b.emoji}</span>
            <div class="me-row-main">
                <div class="me-row-title">${b.name}</div>
                <p class="me-row-sub">${b.off ? `Hors jeu : ${b.off}` : b.goal}</p>
            </div>
            <span class="badge-stat-count">${b.holders} membre${b.holders > 1 ? 's' : ''}</span>
        </li>`)}</ul>`);
}
