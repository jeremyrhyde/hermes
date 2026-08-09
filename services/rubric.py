"""The frozen scoring rubric.

The rubric is the scoring *instructions*; the profile is the *taste content*.
They are separated deliberately: the profile lives in the database and evolves
as the reader's taste does, while the rubric lives here and changes almost
never. Every score records both versions, so a profile edit is never mistaken
for a rubric change.

``RUBRIC`` is a module constant rather than configuration on purpose. Per
research finding R5, a cosmetic edit to a scoring prompt flips 16-24% of scores
even at temperature 0 — so an edit has to surface as a git diff and force a
``RUBRIC_VERSION`` bump. Runtime editability would be a defect, not a missing
feature.

The rubric carries exactly one exemplar, and it is full-mark (research finding
R9): a mid-range exemplar pulls model outputs toward its own score. The exemplar
describes the *shape* of a 95-100 article and never quotes a real one, or the
rubric would leak into the corpus it scores.
"""

from __future__ import annotations

RUBRIC_VERSION = "rubric-v1"

RUBRIC = """\
You score articles from 0 to 100 against a reader's taste profile. The profile \
is supplied separately and is the authority on what this reader values; the \
rubric below is how to apply it.

PRIMARY CRITERION — lens interaction.
An article earns its score chiefly by treating a subject through more than one \
lens, where the lenses interact. Interaction means the second domain changes \
the conclusion about the first: it constrains it, reverses it, explains a \
mechanism behind it, or bounds when it holds. An article that explains how \
export controls compress a manufacturer's margins is doing this. An article \
that discusses AI and separately discusses markets is not, however competent \
each part is. Touching many domains with no bearing of one on another earns \
nothing here — breadth is not interaction, and an article that merely covers \
ground scores low no matter how much ground it covers.

SECONDARY CRITERION — evidence.
Credit numbers, named specifics, and stated mechanisms. Argued opinion counts: \
a strong claim carried by reasoning the reader can check is evidence-backed. \
Unsupported assertion does not count, no matter how confidently framed.

PENALTIES — subtract from the score you have reached.
- Person-centric framing: the piece is organized around a personality rather \
than a mechanism.
- Single perspective: one lens only, however well executed.
- Breadth by aggregation: link roundups and grab-bags that touch many domains \
without any of them bearing on another. This penalty applies on top of earning \
nothing under the primary criterion; such pieces belong near the bottom.

EXEMPLAR — the shape of a 95-100 article.
It opens on a concrete development in one domain. It then brings in a second \
domain not as background but as a constraint, and the constraint changes what \
the opening development means — the obvious reading turns out to be wrong, or \
right only under conditions the second domain sets. The mechanism connecting \
them is stated explicitly, not gestured at. Specific quantities, named actors, \
and dated events carry the argument, and the conclusion follows from them \
rather than from the author's standing. No individual is the subject; the \
subject is how the two domains bear on each other.

OUTPUT.
Give a score from 0 to 100 and a rationale. The rationale must name each lens \
you found and state the specific interaction between them — what the second \
domain changes about the conclusion drawn from the first. If you cannot name \
the interaction concretely, there is not one, and the score must reflect that. \
Where you applied a penalty, say which. Keep the rationale to a few sentences.
"""
