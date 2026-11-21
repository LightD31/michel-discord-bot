// Small helpers every module uses.

// ── Utility ───────────────────────────────────────
export function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
