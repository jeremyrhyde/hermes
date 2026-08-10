"""Request bodies for the profile and review routes.

Its own module beside :mod:`schemas.preferences`, for the same reason that one
is separate: a taste profile is neither a view setting nor a property of any
article, and the three evolve for unrelated reasons.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProfileIn(BaseModel):
    """Request body for ``PUT /profile/``.

    ``body`` is a plain ``str`` rather than a constrained type, matching
    :class:`~schemas.preferences.PreferenceIn`: a constraint here would make
    FastAPI answer a blank profile with a 422 validation dump, and every
    rejection in this API is a 400 that names what would have been accepted.
    """

    model_config = ConfigDict(extra="ignore")

    body: str


class ApprovalIn(BaseModel):
    """Request body for ``POST /profile/review/{version}/approve``.

    ``body`` carries the reader's edit, and its absence is not the same as an
    empty string: ``None`` means "approve what was proposed", while ``""`` is a
    real edit that would wipe the profile. Editing is not a separate route on
    purpose — a saved edit that was never approved would be a body nobody
    agreed to, sitting one query away from being live.

    ``rescore`` defaults to ``False`` so a client that omits it cannot
    accidentally queue the whole corpus for re-scoring.
    """

    model_config = ConfigDict(extra="ignore")

    body: str | None = None
    rescore: bool = False
