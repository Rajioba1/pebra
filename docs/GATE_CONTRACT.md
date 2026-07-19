# Gate decision contract

The universal gate-check envelope has schema version `1` and these JSON fields:
`schema_version`, `permission`, `tier`, nullable `reason`, nullable `warn`, and nullable
`risk_summary`. Trusted host integrations may additionally request `matched_assessment_id`; the default
model-facing envelope omits it. A non-null risk summary contains `decision`, `expected_loss`, `benefit`,
and `rau` from one exact matched persisted assessment.

Permissions have stable wire values: `CONTINUE` is `allow`, `RETURN_CANDIDATE` is `deny`, and
`REQUEST_HUMAN` is `ask`. A tier diagnoses why a disposition was reached; it is not an independent host
command. Hosts act on permission through their documented capability projection. Within dispositions
emitted by PEBRA, precedence is `RETURN_CANDIDATE > REQUEST_HUMAN > CONTINUE`.

## Allowed permission and tier pairs

| Permission | Tier | Meaning |
| --- | --- | --- |
| `allow` | `pass` | No consultation is required. |
| `allow` | `fail_open` | Infrastructure evidence was unavailable; preserve fail-open behavior. |
| `allow` | `consulted` | The exact candidate has a persisted `proceed` assessment. |
| `ask` | `consulted_review` | The exact candidate requires trusted human review. |
| `deny` | `must_consult` | Assess the attempted candidate before editing. |
| `deny` | `candidate_unverifiable` | The host event cannot be materialized as a complete candidate. |
| `deny` | `candidate_unbound` | The persisted assessment predates exact candidate binding. |
| `deny` | `candidate_mismatch` | The attempted candidate differs from the assessed candidate. |
| `deny` | `candidate_incomplete` | The host event omits part of the assessed atomic candidate. |
| `deny` | `consulted_revise` | Revise the exact candidate and reassess. |
| `deny` | `consulted_prerequisite` | Complete the named inspection or test prerequisite and reassess. |
| `deny` | `consulted_review` | Choose a different candidate or route after persisted `reject`. |
| `deny` | `consulted_review_unavailable` | Bound human review is unavailable; reassess or choose another route. |

An exact candidate hold or review request overrides an earlier advisory proceed only for that exact
attempted candidate. It never cancels or rejects the user's goal.

## Risk-summary matrix

Risk summaries are exact-only and all-or-none. All three numbers must be finite, non-boolean numbers from
the matched assessment; missing, partial, malformed, stale, unbound, unverifiable, mismatched, or
incomplete evidence yields `risk_summary: null`. Non-consulted tiers cannot carry scores.
Accepted integers and floats are normalized to finite JSON numbers. Oversized integers that cannot be
represented as floats also yield `risk_summary: null`; the exact candidate keeps its original disposition
and the reason states `risk summary unavailable`.

| Permission / tier | Allowed persisted decision |
| --- | --- |
| `allow` / `consulted` | `proceed` |
| `deny` / `consulted_revise` | `revise_safer` |
| `deny` / `consulted_prerequisite` | `inspect_first`, `test_first` |
| `ask` / `consulted_review` | `ask_human` |
| `deny` / `consulted_review` | `reject` |
| `deny` / `consulted_review_unavailable` | `ask_human` |

Only an explicitly parsed persisted `proceed` decision can produce `allow/consulted`. A null, unknown, or
corrupt persisted decision produces `allow/fail_open` with a visible data-integrity warning, no risk
summary, and no assessment attribution. This preserves the infrastructure fail-open policy while ensuring
the bound application controller refuses the result because it is not `consulted`.

An interactive `ask_human` result names `pebra accept-risk --apply` only when persisted replay metadata is
structurally valid: `status` is `available`, `algorithm` is exactly `sha256-candidate-replay-v1`, and
`digest` is 64 lowercase hexadecimal characters. The gate validates metadata only; it does not read the
cached payload. Missing or malformed metadata produces `deny/consulted_review_unavailable` without
promising the approval command.

## Host projection and threat boundary

The universal wire contract retains `ask` for a future trusted adapter with an exact PEBRA approval
callback. Both currently installed shims project `ask` to a blocking candidate hold (`deny`), because
neither implements that callback: a Claude native prompt bypasses the bound sanction and reassessment,
while Codex fails open on unsupported `ask`. Existing `allow` and `deny` dispositions are unchanged.

`allow/fail_open` remains the infrastructure policy for unavailable graph, Git, or store evidence. This
gate is a workflow control, not a security boundary: a process running under the same operating-system
identity can alter local instructions, hooks, code, or storage and can invoke tools outside the managed
host path.
