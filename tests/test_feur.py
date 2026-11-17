"""When Feur answers: a message, or a question within it, that ends with the keyword."""

import pytest

from extensions.feur import _should_respond


@pytest.mark.parametrize(
    ("content", "keyword", "expected"),
    [
        ("quoi", "quoi", True),
        ("tu fais quoi", "quoi", True),
        ("tu fais quoi ?", "quoi", True),
        ("tu fais quoi?!", "quoi", True),
        # A question ending with the keyword, then more text: this rule never
        # matched, since sentences were split on "?" before it looked for one (Q7).
        ("quoi ? t'es sérieux", "quoi", True),
        ("t'as dit quoi ?! j'ai rien compris", "quoi", True),
        ("bon. et après, quoi ? on rentre", "quoi", True),
        ("pourquoi ? je sais pas", "pourquoi", True),
        # The keyword must end the message or the question.
        ("c'est quoi ce truc ?", "quoi", False),
        ("quoi de neuf", "quoi", False),
        ("quoique", "quoi", False),
        ("pourquoi ? je sais pas", "quoi", False),
        ("rien à voir", "quoi", False),
    ],
)
def test_feur_answers_a_message_or_question_ending_with_the_keyword(content, keyword, expected):
    assert _should_respond(content, keyword) is expected
