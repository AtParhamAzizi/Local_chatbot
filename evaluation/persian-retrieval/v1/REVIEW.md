# Source-grounding review — Persian retrieval 1.0.0

Date: 2026-11-12. Author and reviewer: Codex (agent review).
Independent human/domain review: pending; no human approval is implied.
All corpus text and questions are original synthetic material covered by the
repository's [MIT license](../../../LICENSE). The fictional سپهر service has no real policies.

## Inventory and method

The frozen corpus has 30 logical source units: 12 Q&A, 8 PDF-page excerpts,
and 10 Markdown-line excerpts. Filenames/coordinates identify these authored
units, not physical files or extraction results. Each of 12 intent families
has a primary source and a confusable contrasting source; six also have a
supporting source. All 30 units remain candidates for every query.

Each family has five authored questions: direct, orthographic, terminology,
near-match and missing-information. Each split has 24 answerable and six
unanswerable questions. Related variants stay in one split. Development and
heldout have disjoint intent families, not merely shuffled paraphrases.

For every question the review checked the expected answer against all units,
quoted the complete positive source evidence, assigned query-specific grades,
checked the plausible wrong-scope source, and recorded the reason information
is missing for unanswerable cases. Unlisted source units are grade zero.
Automated validation checks exact evidence membership, inventory hashes,
noncontradictory judgments, coverage and normalized question duplication.
It cannot certify semantic judgment correctness.

## Reviewed scope contrasts

| Split | Intent family | Relevant distinction | Missing information |
| --- | --- | --- | --- |
| Development | signup | Personal phone/OTP signup versus organization identity/representative | Accounts per phone per day |
| Development | password | Forgotten-password email reset versus changing a known password | Reset-link expiry |
| Development | ticket | Traceable support ticket versus anonymous issue report | Response-time guarantee |
| Development | upload | PDF ticket attachments versus PNG/JPEG profile pictures | Malware scanning |
| Development | search | Exact-phrase help search versus ticket code/subject search | Typo correction |
| Development | export | Filtered UTF-8 CSV list versus one-ticket PDF receipt | Scheduled export |
| Heldout | notification | Request-status email versus login verification SMS | Notification delivery delay |
| Heldout | sharing | Expiring read-only public link versus editor invitation | Concurrent readers |
| Heldout | archive | Hiding closed tickets versus irreversible draft deletion | Archive retention period |
| Heldout | api | Developer Bearer token versus login OTP | Rate limit |
| Heldout | language | Interface labels versus separately authored article translations | Future Kurdish-language roadmap |
| Heldout | maintenance | Friday maintenance versus weekday human support hours | In-flight submission durability |

## Judgment corrections during review

- A question about present Kurdish support was replaced with a future-roadmap
  question: the closed list of current interface languages already answers the
  former, so it was not a sound unanswerable example.
- Supporting evidence is graded per question. For example, the ticket support
  unit fully answers where to look, but only partially answers a question that
  also asks the tracking-code length. The same principle applies to exact-phrase
  search, notification settings and interface translation scope.
- Questions whose correct answer is “no” remain answerable when a source
  explicitly supports that answer. They are not counted as abstention cases.

## Release and heldout discipline

This is a small diagnostic seed, not a representative customer benchmark.
Review with Persian-speaking domain owners and consented representative sources
is required before using it for deployment acceptance or broad quality claims.
Public heldout questions are an operational holdout, not a secret test set.
Tune on development; run heldout only for a frozen candidate and record exposure.
If heldout results influence tuning, retire that split and author new intent
families in a new dataset version. Do not relabel a baseline to improve scores.

The checked-in lexical reports are reference measurements, not a production
model qualification. CI can verify their arithmetic/reproducibility without
loading model weights or tuning a retriever against heldout labels.
