// The Succès module page's card: each badge, in play or not, and how many won it (P25).
import { html } from '../core/html.js';
import { cardHtml, loadCard } from '../ui/card.js';

export function achievementsCardHtml() {
    return cardHtml({
        className: 'u-mt-16',
        bodyId: 'achievements-badges',
        title: '🏆 Badges du serveur',
        subtitle: "Combien de membres ont débloqué chaque badge. Un badge hors jeu n'est ni vérifié ni affiché.",
    });
}

export async function loadAchievements(sid) {
    await loadCard('achievements-badges', `/api/servers/${encodeURIComponent(sid)}/achievements`, data => html`<ul class="badge-stats">${data.badges.map(b => html`
        <li class="badge-stat ${b.off ? 'is-off' : ''}">
            <span class="me-badge-emoji" aria-hidden="true">${b.emoji}</span>
            <div class="me-row-main">
                <div class="me-row-title">${b.name}</div>
                <p class="me-row-sub">${b.off ? `Hors jeu : ${b.off}` : b.goal}</p>
            </div>
            <span class="badge-stat-count">${b.holders} membre${b.holders > 1 ? 's' : ''}</span>
        </li>`)}</ul>`);
}
