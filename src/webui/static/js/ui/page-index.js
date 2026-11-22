// The sticky index of a page with three cards or more.
import { html, raw } from '../core/html.js';
import { actionArgs, registerActions } from '../core/actions.js';
import { state } from '../core/state.js';

// ── Section index (P3) ────────────────────────────
// A long page (a server, the global settings, a module with several cards)
// gets a sticky row of links to its cards: 5 000 px of server page on a
// phone is otherwise a long scroll to the journal.
export const INDEXED_PAGES = new Set(['server', 'global', 'module']);

export function buildPageIndex() {
    const main = document.querySelector('.main');
    if (!main || !INDEXED_PAGES.has(state.currentPage)) return;
    const cards = [...main.querySelectorAll('.card')]
        .filter(card => !card.parentElement.closest('.card') && card.querySelector(':scope > .card-header .card-title'));
    if (cards.length < 3) return;
    const links = cards.map((card, i) => {
        card.id = card.id || `section-${i}`;
        const title = card.querySelector(':scope > .card-header .card-title').textContent.trim();
        return String(html`<button type="button" class="page-index-link" data-click="scrollToSection" data-click-args="${actionArgs(card.id)}">${title}</button>`);
    }).join('');
    const header = main.querySelector('.page-header');
    const nav = `<nav class="page-index" aria-label="Sur cette page">${links}</nav>`;
    if (header) header.insertAdjacentHTML('afterend', nav);
    else main.insertAdjacentHTML('afterbegin', nav);
}

export function scrollToSection(id) {
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const focusable = target.querySelector('.card-title');
    if (focusable) {
        focusable.setAttribute('tabindex', '-1');
        focusable.focus({ preventScroll: true });
    }
}

registerActions({
    scrollToSection,
});
