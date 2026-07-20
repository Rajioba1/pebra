# Security Policy

## Supported Versions

PEBRA's latest published release is `0.1.1`; `0.2.x` is the current development line on `main`.
Security fixes are made against the published release and the current development branch.

| Version | Supported |
| --- | --- |
| Latest published: `0.1.1` | Yes |
| Development: `0.2.x` / `main` | Yes |
| Older versions | No |

Update this table only after the corresponding PyPI release is verified; a version bump on `main`
does not make that version a published release.

## Reporting A Vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/Rajioba1/pebra/security/advisories/new)
to report a suspected vulnerability. Include the affected version or commit, reproduction steps,
impact, and any proposed mitigation. **Do not open a public issue** for an undisclosed vulnerability.

If private vulnerability reporting is temporarily unavailable, do not publish exploit details or
open a public issue. Retry the private reporting channel after GitHub service is restored.

## Response Targets

These are response targets, not resolution guarantees:

- acknowledgement within 3 business days;
- initial triage within 7 business days;
- status updates when the assessment or remediation plan materially changes;
- coordinated disclosure after a fix or mitigation is available, when practical.

Reports are evaluated for impact on PEBRA's decision integrity, candidate binding, approval flow,
local dashboard, data stores, external-tool boundaries, and release supply chain. A model choosing not
to follow advisory guidance is not by itself a vulnerability; a deterministic bypass of an advertised
control may be.

## Disclosure

Please allow time for investigation and remediation before public disclosure. Valid reports may be
credited in release notes or advisories unless the reporter requests anonymity.
