"""Request body for the runtime knobs.

Its own module rather than a member of :mod:`schemas.article`: a preference is
a property of the *view*, not of any article, and the two evolve for unrelated
reasons.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PreferenceIn(BaseModel):
    """Request body for ``PUT /preferences/{key}``.

    ``value`` is a plain int rather than a constrained type for the same reason
    :class:`~schemas.article.RatingIn` is: the constraint would make FastAPI
    answer an out-of-range value with a 422 validation dump, and every rejection
    in this API is a 400 naming what would have been accepted. The per-key range
    lives beside the key table in :mod:`core.api`, which is the only place that
    knows a cutoff is 0-100 and a page size is 1-200.
    """

    model_config = ConfigDict(extra="ignore")

    value: int
