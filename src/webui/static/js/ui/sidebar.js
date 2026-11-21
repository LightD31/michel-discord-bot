// The sidebar on a phone.
import { registerActions } from '../core/actions.js';
import { state } from '../core/state.js';

// ── Sidebar toggle (mobile) ───────────────────────
export function toggleSidebar() {
    state.sidebarOpen = !state.sidebarOpen;
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    if (sidebar) sidebar.classList.toggle('open', state.sidebarOpen);
    if (overlay) overlay.classList.toggle('active', state.sidebarOpen);
}

export function closeSidebar() {
    state.sidebarOpen = false;
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('active');
}

registerActions({
    closeSidebar,
    toggleSidebar,
});
