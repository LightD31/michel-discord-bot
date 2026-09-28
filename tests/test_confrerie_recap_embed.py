"""The recap embed stays within Discord's limits and renders identically for the same data."""

from confrerie_fixtures import DEFI_OPTIONS, oeuvre

from extensions.confrerie.stats import build_statistics_embed
from features.confrerie.stats import aggregate


def _many_pages(n: int):
    return [
        oeuvre(
            str(i),
            f"Un titre assez long pour remplir le champ numéro {i}",
            auteurs=(f"Auteur·ice avec un nom interminable {i % 40}",),
            defi=f"Défi {i % 30:02d} : Consigne au long cours" if i % 2 else f"SBL{i % 7}",
            avancement="En cours" if i % 3 == 0 else "Terminé",
        )
        for i in range(n)
    ]


def test_every_field_fits_discord_limits():
    embed = build_statistics_embed(aggregate(_many_pages(400), DEFI_OPTIONS))
    for field in embed.fields:
        assert 0 < len(field.value) <= 1024
    total = len(embed.title or "") + len(embed.description or "")
    total += sum(len(f.name) + len(f.value) for f in embed.fields)
    assert total <= 6000


def test_same_data_renders_the_same_payload():
    pages = _many_pages(50)
    first = build_statistics_embed(aggregate(pages)).to_dict()
    second = build_statistics_embed(aggregate(list(reversed(pages)))).to_dict()
    first.pop("timestamp", None)
    second.pop("timestamp", None)
    assert first == second


def test_description_totals():
    embed = build_statistics_embed(aggregate([oeuvre("1", "Seul", auteurs=("Mahé",))]))
    assert embed.description == "**1 œuvre** · **0 participation** aux défis · **1 auteur·ice**"
