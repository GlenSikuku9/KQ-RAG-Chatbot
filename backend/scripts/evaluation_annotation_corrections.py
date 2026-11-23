"""Reviewed development evidence corrections used only by evaluation tools."""

from __future__ import annotations


# Each listed parent contains a matching word or number but does not support
# the policy criterion in context. Runtime retrieval never imports this map.
INAPPLICABLE_PARENTS_BY_CRITERION = {
    "dev-baggage-evidence-1": {
        "8cc9a5b8ec30dd8bc41542234ef085070a3be4c57c651546c568f878ec832182",
    },
    "dev-baggage-evidence-3": {
        "cfea193aec0d73f6dde1ada11dcf1a0db832fa824542df513308a4c275d646c2",
    },
    "dev-wheelchair-evidence-3": {
        "5c30e4e5ba0e1fb9a11bd643f20f8841d7ea94b8de2ba1f35ef51c36bb7ecd78",
    },
    "held-special-evidence-2": {
        "5c30e4e5ba0e1fb9a11bd643f20f8841d7ea94b8de2ba1f35ef51c36bb7ecd78",
    },
    "dev-name-evidence-1": {
        "6c0034561a11f784a4311ff7eb1f9a6536da08e6890dce403879a965d82a03e3",
    },
    "dev-refund-evidence-2": {
        "b55f3c38dccb95421d5de04490eb03c158be4d3e8928b344ec095238f05ec86f",
        "64d5b752d09760edc28abe0d07e6605eca5e501655f50be35393a90a3f6dae2a",
    },
    "dev-docs-evidence-2": {
        "eb753145dcaba6db902a56912389affbe5f89b831656e442c25648ce7a440cb9",
    },
}


def corrected_parent_ids(
    criterion_id: str,
    parent_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Remove only parents reviewed as directly inapplicable."""

    excluded = INAPPLICABLE_PARENTS_BY_CRITERION.get(
        criterion_id,
        set(),
    )
    corrected = [
        parent_id
        for parent_id in parent_ids
        if parent_id not in excluded
    ]
    removed = sorted(set(parent_ids) - set(corrected))
    return corrected, removed
