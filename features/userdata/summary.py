"""What a deletion did, said to the member: by ``/mes-donnees supprimer`` and the dashboard."""

from __future__ import annotations

from features.userdata.repository import Report

# A collection name, not a secret (detect-secrets reads the keyword in it).
SANTA_DRAWS = "secret_santa_draw_results"  # pragma: allowlist secret
# Why something is kept, in the member's words.
KEPT_REASONS = {
    "infractions": "les sanctions de modération (elles protègent les serveurs)",
    SANTA_DRAWS: "les tirages Secret Santa déjà faits (les cadeaux des autres en dépendent)",
    "tricount": "tes comptes Tricount, tant qu'il te reste un solde à régler",
}
NOT_COVERED = (
    "Hors de portée : ce que les admins d'un serveur ont écrit dans sa "
    "configuration (un prénom, un compte Spotify lié) et un compte LLDAP créé avec /inscription. "
    "Demande-les aux admins du serveur."
)


def kept_reasons(report: Report) -> list[str]:
    """Why each kind of store the deletion left was kept, one sentence each."""
    kept = sorted({where.rsplit(".", 1)[1] for where in report.kept})
    return [KEPT_REASONS.get(k, k) for k in kept]


def summary(report: Report) -> str:
    lines = [
        f"🗑️ Supprimé ou anonymisé : {report.total} élément(s) "
        f"({sum(report.deleted.values())} supprimé(s), {sum(report.anonymised.values())} anonymisé(s))."
    ]
    reasons = kept_reasons(report)
    if reasons:
        lines.append("Conservé : " + " ; ".join(reasons) + ".")
    lines.append(NOT_COVERED)
    return "\n".join(lines)


__all__ = ["KEPT_REASONS", "NOT_COVERED", "kept_reasons", "summary"]
