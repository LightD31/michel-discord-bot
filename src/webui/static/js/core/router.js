// Hash routes and navigation between pages.
import { loadTwitchAuth } from '../features/twitch-auth.js';
import { loadShortLinks } from '../pages/links.js';
import { startLogStream, stopLogStream } from '../pages/logs.js';
import { closeSidebar } from '../ui/sidebar.js';
import { registerActions } from './actions.js';
import { isAdmin, isDeveloper, loadExtensions } from './data.js';
import { confirmDiscard } from './dirty.js';
import { render } from './render.js';
import { state } from './state.js';

// ── Routing ───────────────────────────────────────
// Each page has a hash URL (#/server/<id>, #/module/<id>/<module>, #/logs…),
// so a link opens it, a reload stays on it and the back button goes back.
export const ROUTED_PAGES = ['global', 'extensions', 'logs'];

export function routeHash(page, serverId = null, moduleName = null) {
    if (page === 'server' && serverId) return `#/server/${encodeURIComponent(serverId)}`;
    if (page === 'setup' && serverId) return `#/setup/${encodeURIComponent(serverId)}`;
    if (page === 'me') return serverId ? `#/me/${encodeURIComponent(serverId)}` : '#/me';
    if (page === 'module' && serverId && moduleName) {
        return `#/module/${encodeURIComponent(serverId)}/${encodeURIComponent(moduleName)}`;
    }
    if (ROUTED_PAGES.includes(page)) return `#/${page}`;
    return '#/';
}

export function parseHash(hash = location.hash) {
    const path = hash.replace(/^#\/?/, '').split('?')[0];
    let parts;
    try {
        parts = path.split('/').filter(Boolean).map(decodeURIComponent);
    } catch (e) {
        return { page: 'home' };
    }
    const [page = 'home', a = null, b = null] = parts;
    if ((page === 'server' || page === 'setup') && a) return { page, serverId: a };
    if (page === 'me') return { page, serverId: a };
    if (page === 'module' && a && b) return { page, serverId: a, moduleName: b };
    if (ROUTED_PAGES.includes(page)) return { page };
    return { page: 'home' };
}

window.addEventListener('popstate', () => {
    if (!state.user || state.publicView) return;
    const route = parseHash();
    navigate(route.page, route.serverId, route.moduleName, { fromHistory: true });
});

// ── Navigation ────────────────────────────────────
export async function navigate(page, serverId = null, moduleName = null, { fromHistory = false } = {}) {
    if (!(await confirmDiscard())) {
        // Back/forward already moved the URL: put the current page's back.
        if (fromHistory) {
            history.pushState(null, '', routeHash(state.currentPage, state.currentServerId, state.currentModule));
        }
        return;
    }
    // A member who manages no server lands on their own space (C6).
    if (page === 'home' && !isAdmin()) page = 'me';
    // Restrict admin-only pages for non-admin users
    const adminPages = ['server', 'module', 'setup'];
    const developerPages = ['global', 'extensions', 'logs'];
    if (adminPages.includes(page) && !isAdmin()) {
        page = 'me';
        serverId = null;
    }
    if (developerPages.includes(page) && !isDeveloper()) {
        page = isAdmin() ? 'home' : 'me';
        serverId = null;
        moduleName = null;
    }
    state.currentPage = page;
    state.currentServerId = serverId;
    state.currentModule = moduleName;
    const hash = routeHash(page, serverId, moduleName);
    if (location.hash !== hash) {
        // A first visit or a back/forward only fixes the URL; a click adds a step.
        if (fromHistory) history.replaceState(null, '', hash);
        else history.pushState(null, '', hash);
    }
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
        loadTwitchAuth().then(() => render());
    }

    // Liens courts: the server's own Shlink, on its module page (P37).
    if (page === 'module' && moduleName === 'moduleShlink') {
        Object.assign(state.shlink, { serverId, editing: null, page: 1, search: '', status: null, items: [] });
        loadShortLinks().then(() => render());
    }

    render();
}

registerActions({
    navigate,
});
