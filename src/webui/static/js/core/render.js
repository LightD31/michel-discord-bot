// The layout: login page, sidebar, and the current page.
import { loadGazette } from '../features/gazette.js';
import { loadInfractions, loadReports } from '../features/moderation.js';
import { loadRoleMenus } from '../features/rolemenus.js';
import { loadSecretSanta } from '../features/secretsanta.js';
import { destroySpotifyCharts, loadSpotifyStats } from '../features/spotify.js';
import { destroyXpCharts, loadPublicSeason, loadXpStats } from '../features/xp.js';
import { populateChannelPickers } from '../forms/pickers/channels.js';
import { populateMultiPickers, populateRolePickers } from '../forms/pickers/lists.js';
import { populateMemberPickers } from '../forms/pickers/members.js';
import { refreshAiModelSelects } from '../forms/widgets/models.js';
import { loadAudit } from '../pages/audit.js';
import { loadBotMessages } from '../pages/bot-messages.js';
import { renderExtensionsPage } from '../pages/extensions.js';
import { renderGlobalConfig } from '../pages/global.js';
import { loadHealth } from '../pages/health.js';
import { renderHomePage } from '../pages/home.js';
import { renderShortLinksPage } from '../pages/links.js';
import { handleLogScroll, renderLogsPage } from '../pages/logs.js';
import { loadMe, renderMePage } from '../pages/me.js';
import { renderModulePage } from '../pages/module.js';
import { loadOnboarding } from '../pages/onboarding.js';
import { loadOverview } from '../pages/overview.js';
import { renderPublicPage } from '../pages/public.js';
import { renderServerPage } from '../pages/server.js';
import { renderSetupPage } from '../pages/setup.js';
import { refreshPreviews } from '../forms/template-test.js';
import { buildPageIndex } from '../ui/page-index.js';
import { actionArgs } from './actions.js';
import { isAdmin, isDeveloper } from './data.js';
import { routeHash } from './router.js';
import { state } from './state.js';
import { escapeHtml } from './util.js';

// ── Render ────────────────────────────────────────
export function render() {
    const app = document.getElementById('app');
    // The page is rebuilt from scratch below: release Chart.js instances
    // bound to the canvases about to be detached.
    destroyXpCharts();
    destroySpotifyCharts();
    if (state.publicView) {
        app.innerHTML = renderPublicPage();
        const { meta, kind } = state.publicView;
        if (meta && kind === 'xp') {
            loadPublicSeason(state.publicView.token);
            loadXpStats(meta.id);
        }
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
    buildPageIndex();
    refreshPreviews(document);
    // Populate any channel / member pickers on the current page
    if (state.currentServerId) {
        populateChannelPickers(document);
        populateMemberPickers(document);
        populateRolePickers(document);
        populateMultiPickers(document);
        refreshAiModelSelects(document);
    }
    // Server page: the messages the bot keeps up to date there.
    if (state.currentPage === 'server' && state.currentServerId) {
        loadOverview(state.currentServerId);
        loadOnboarding(state.currentServerId);
        loadBotMessages(state.currentServerId);
        loadHealth(state.currentServerId);
        loadAudit(state.currentServerId);
    }
    // The member space: the servers, then the chosen one's data.
    if (state.currentPage === 'me') {
        loadMe(state.currentServerId);
    }
    // Global settings: the bot's own audit log (developers only).
    if (state.currentPage === 'global') {
        loadAudit(null);
    }
    // Reaction-roles builder: load existing menus when on its module page.
    if (state.currentPage === 'module' && state.currentModule === 'moduleReactionRoles' && state.currentServerId) {
        loadRoleMenus(state.currentServerId);
    }
    // Secret Santa: the session and its draw button.
    if (state.currentPage === 'module' && state.currentModule === 'moduleSecretSanta' && state.currentServerId) {
        loadSecretSanta(state.currentServerId);
    }
    // The Gazette: this week's edition so far, and "Publier maintenant".
    if (state.currentPage === 'module' && state.currentModule === 'moduleGazette' && state.currentServerId) {
        loadGazette(state.currentServerId);
    }
    // Moderation: load infraction history when on its module page.
    if (state.currentPage === 'module' && state.currentModule === 'moduleModeration' && state.currentServerId) {
        loadInfractions(state.currentServerId);
        loadReports(state.currentServerId);
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

export function renderLogin() {
    return `
            <div class="login-page">
                <div class="login-card">
                    <h1>🤖 Michel Bot</h1>
                    <p class="login-lead">Le bot de tes serveurs Discord : niveaux et classement, anniversaires,
                        modération, sondages, rappels, Père Noël Secret, flux RSS, Spotify et bien d'autres modules.</p>
                    <ul class="login-points">
                        <li>⚙️ Les administrateurs d'un serveur (droit « Gérer le serveur ») y règlent les modules de Michel.</li>
                        <li>📊 Ils y suivent aussi l'activité, les sanctions et la santé du bot sur leur serveur.</li>
                        <li>🙂 Chaque membre y a son espace : ses rappels, son anniversaire, ses comptes Tricount, sa liste pour le Père Noël secret et ses données.</li>
                        <li>🔒 La connexion passe par Discord : le dashboard ne lit que ton profil et la liste de tes serveurs.</li>
                    </ul>
                    <a href="/auth/login" class="login-btn">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z"/>
                        </svg>
                        Se connecter avec Discord
                    </a>
                    <p class="login-foot">Pour tout le reste, ça se passe dans Discord : tape <code>/aide</code>.</p>
                </div>
            </div>
        `;
}

export function renderSidebar() {
    const serverItems = state.servers
        ? Object.entries(state.servers).map(([id, s]) => {
            const isActive = ['server', 'module', 'setup'].includes(state.currentPage) && state.currentServerId === id;
            const iconUrl = s.icon
                ? `https://cdn.discordapp.com/icons/${id}/${s.icon}.png?size=64`
                : null;
            return `
                    <a class="nav-item ${isActive ? 'active' : ''}" href="${routeHash('server', id)}" ${isActive ? 'aria-current="page"' : ''}
                       data-click="navigate" data-click-args="${actionArgs("server", id)}" data-prevent>
                        <div class="server-icon">
                            ${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="">` : escapeHtml(s.name?.charAt(0) || '?')}
                        </div>
                        <div class="nav-item-label">
                            <span class="nav-item-name">${escapeHtml(s.name || id)}</span>
                        </div>
                    </a>
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
                        ${isAdmin() ? `
                        <a class="nav-item ${state.currentPage === 'home' ? 'active' : ''}" href="${routeHash('home')}" ${state.currentPage === 'home' ? 'aria-current="page"' : ''} data-click="navigate" data-click-args="[&quot;home&quot;]" data-prevent>
                            <span class="icon">🏠</span> Accueil
                        </a>` : ''}
                        <a class="nav-item ${state.currentPage === 'me' ? 'active' : ''}" href="${routeHash('me')}" ${state.currentPage === 'me' ? 'aria-current="page"' : ''} data-click="navigate" data-click-args="[&quot;me&quot;]" data-prevent>
                            <span class="icon">🙂</span> Mon espace
                        </a>
                        ${isDeveloper() ? `
                        <a class="nav-item ${state.currentPage === 'global' ? 'active' : ''}" href="${routeHash('global')}" ${state.currentPage === 'global' ? 'aria-current="page"' : ''} data-click="navigate" data-click-args="[&quot;global&quot;]" data-prevent>
                            <span class="icon">⚙️</span> Config globale
                        </a>
                        <a class="nav-item ${state.currentPage === 'extensions' ? 'active' : ''}" href="${routeHash('extensions')}" ${state.currentPage === 'extensions' ? 'aria-current="page"' : ''} data-click="navigate" data-click-args="[&quot;extensions&quot;]" data-prevent>
                            <span class="icon">🧩</span> Extensions
                        </a>
                        <a class="nav-item ${state.currentPage === 'links' ? 'active' : ''}" href="${routeHash('links')}" ${state.currentPage === 'links' ? 'aria-current="page"' : ''} data-click="navigate" data-click-args="[&quot;links&quot;]" data-prevent>
                            <span class="icon">🔗</span> Liens courts
                        </a>
                        <a class="nav-item ${state.currentPage === 'logs' ? 'active' : ''}" href="${routeHash('logs')}" ${state.currentPage === 'logs' ? 'aria-current="page"' : ''} data-click="navigate" data-click-args="[&quot;logs&quot;]" data-prevent>
                            <span class="icon">📋</span> Logs
                        </a>
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
                        <button class="logout-btn" data-click="logout" title="Déconnexion" aria-label="Déconnexion">🚪</button>
                    </div>
                </div>
            </div>
        `;
}

export function renderPage() {
    switch (state.currentPage) {
        case 'home': return renderHomePage();
        case 'global': return renderGlobalConfig();
        case 'extensions': return renderExtensionsPage();
        case 'server': return renderServerPage();
        case 'setup': return renderSetupPage();
        case 'module': return renderModulePage();
        case 'links': return renderShortLinksPage();
        case 'logs': return renderLogsPage();
        case 'me': return renderMePage();
        default: return renderHomePage();
    }
}
