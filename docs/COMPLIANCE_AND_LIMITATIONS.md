# Compliance and Limitations

Reviewed: 2026-08-16

This software is a research and human-review aid, not legal advice. A Massachusetts lawyer must independently review the proposed recipient, content, channel, timing, and circumstances before any communication. The software does not decide that outreach is permissible.

## Massachusetts professional-conduct context

The Massachusetts Supreme Judicial Court's current Rule 7.3 defines solicitation as a communication directed to a specific person whom the lawyer knows or reasonably should know needs legal services in a particular matter. It restricts live person-to-person solicitation motivated significantly by pecuniary gain, subject to stated exceptions, and separately prohibits solicitation after a do-not-solicit request or through coercion, duress, harassment, or certain impaired-judgment circumstances.

Rule 7.1 prohibits false or misleading communications about a lawyer or the lawyer's services. Rule 7.2 governs communications and compensation for recommendations, among other requirements. These rules must be reviewed together and against the facts of an actual proposed activity.

Official text:

- `https://www.mass.gov/supreme-judicial-court-rules/rules-of-professional-conduct-rule-71-communications-concerning-a-lawyers-services`
- `https://www.mass.gov/supreme-judicial-court-rules/rules-of-professional-conduct-rule-72-communications-concerning-a-lawyers-services-specific-rules`
- `https://www.mass.gov/supreme-judicial-court-rules/rules-of-professional-conduct-rule-73-solicitation-of-clients`

## Enforced product boundaries

- No automated email, messaging, calling, or outreach transport exists.
- Every approval requires a human ethics-review acknowledgement.
- Suppression sets `do_not_contact`, blocks approval, and excludes the record from approved export.
- Strength 1 information is quarantined and never scored.
- Strength 2 discovery information is not sufficient for scoring.
- Public contact information must be connected to an organizational role.
- Named leaders require a role and role-observation date or filing year.
- Stale leadership remains visible as stale rather than being silently presented as current.
- Conflicting facts remain as separate assertions and force review.
- The OSHA adapter accepts establishment summary fields only. It has no case-detail or narrative adapter.
- OSHA injury totals and rates add no score points; the presence of organization-level workforce context is capped at two points.
- USAspending award values and descriptions are not collected. The presence or size of an award adds no score points.
- Age is accepted only as an aggregate Census geography statistic and is never assigned to a person.
- Approved CSV export checks both a completed human review and active suppression state.

## Prohibited collection and use

Do not import, derive, or use:

- Medical information, health conditions, or disability information.
- Individual accident, injury, workers' compensation, or claim histories.
- Police crash reports for claimant targeting.
- Employee identities or case narratives from OSHA data.
- Individual age or inferred age.
- Race, ethnicity, religion, disability, immigration status, political affiliation, or other sensitive traits.
- Personal social-media profiles, follower counts, or inferred private relationships.
- Private phone numbers, home addresses, or personal emails from brokers or private databases.
- Recommendations to contact a person because that person was recently injured.
- Claims that an employer is unsafe based only on OSHA information.
- Compensation proposals for directing clients to the firm.
- Confidential client facts or matter-level outcomes.

The CSV validator rejects common prohibited individual-level column names. That is a guardrail, not a substitute for reviewing the actual contents of every source and import.

## Contact validation

Important contact information must have one current Strength 5 source, one current official Strength 4 source, or two independent Strength 3-or-better sources. The MVP automatically treats a current official Strength 4 or Strength 5 contact assertion as verified. A lone Strength 3 contact remains `needs_corroboration`.

Role-based channels such as `info@`, `office@`, or `organizer@` are preferred. A named professional email is allowed only when an official source publishes it in connection with that role.

## Known technical limitations

- Geographic tiers use straight-line distance multiplied by a disclosed configurable factor. They are estimates, not actual route distances.
- The bootstrap contains only a few official-page examples; production volume comes from the explicit OSHA and USAspending bulk-import commands.
- OLMS's current disclosure application generates exports dynamically. The MVP supports mapping those official exports through CSV templates but does not automate the web application.
- The ACS Data API currently requires a key. The credential-free Census geocoder remains usable without it.
- OSHA coverage is partial, self-reported, and not representative of every establishment or worker.
- USAspending covers federal award recipients rather than all businesses. Broad searches can reach the API result ceiling and must then be partitioned by date or city.
- Official pages can change after retrieval. Freshness flags reduce, but do not eliminate, that risk.
- The application has no authentication because it is designed for a single local machine. Do not expose Streamlit to a public network without adding access controls.

## Human review checklist

Before outreach, a reviewer should confirm the organization and role are current, inspect every material source and conflict, honor all suppression signals, determine whether permission is needed for an event or materials, review Rules 7.1-7.3 and other applicable law, ensure the communication is truthful and not coercive, and document the decision. Approval in this database records review; it is not a legal conclusion.

## Data handling

Keep the database local, limit access, and back it up only to an approved encrypted location. Do not commit `data/processed/`, bulk source files, `.env`, or secrets. When a fact is no longer necessary, follow the firm's retention policy while preserving any required audit history. Outcome reporting should remain aggregate and contain no confidential case details.
