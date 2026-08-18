# Curated public contact data

This directory contains small, reviewable imports that are intentionally tracked in Git.
Each asserted contact field includes its source URL, publisher, retrieval date, source
strength, and validation status. The files contain public organizational channels and
role-based professional contacts only; they do not contain private or individual-level
contact data.

The 2026-08-17 verification passes prioritize existing high-ranked prospects with missing
contact details. The second pass adds public contact channels for 100 additional exact-match
organizations, including verified facility variants and store locations. The third pass adds
69 more exact-match organizations using official company and store pages, government records,
and current trade-verification sources. The fourth pass adds 111 exact-match enrichments plus
one newly verified Fall River manufacturer, drawing primarily from current official contact and
location pages and current government registries. Official organization pages are assigned source
strength 4 and official government records are assigned source strength 5. The fifth pass adds
48 more exact-match prospects: 45 from current first-party contact or facility pages, two from
current FMCSA company snapshots, and one from a current regional chamber directory that remains
marked as needing corroboration. The sixth pass adds 50 more exact-match prospects from current
official organization and facility pages, two current Massachusetts government facility records,
and one current trade-association facility listing that remains marked as needing corroboration.
Older official documents retain their publication dates so the application's freshness checks can
flag them appropriately.

The seventh pass expands local coverage to Hanover and Pembroke. It imports 41 workplaces
from OSHA's 2025 ITA summary (17 in Hanover and 24 in Pembroke), adds five current community
organizations from their official pages, and supplies verified organizational contact details
for 34 of the OSHA workplaces. The accompanying role file contains 38 public, role-based
channels. Generic OSHA labels were linked only when an official page showed the same local
facility address; unresolved records remain available as prospects without speculative contact
details.

The eighth pass broadens the Bridgewater-centered service area while deepening Hanover and
Pembroke coverage. It adds 29 verified municipal, education, nonprofit, public-safety, and
workplace prospects; enriches 34 existing employers and facilities; and supplies 61 public,
role-based contact channels. Current municipal directories, Massachusetts facility and school
registries, FMCSA snapshots, and official organization pages provide the asserted fields. School
and college reach is recorded only when a current enrollment source documents it, and ambiguous
local business labels remain unenriched unless an exact facility match is available.

The ninth pass fills three major organization-type gaps. It adds 36 labor councils, statewide
labor organizations, and union locals; nine MassHire career centers and workforce boards; and
12 public libraries across the Bridgewater, South Shore, Bristol County, Greater New Bedford,
and nearby Rhode Island service area. All 57 organizations have a current official or government
source and a public organizational or role-based contact channel. No member lists, protected
traits, personal contact details, or inferred demographic attributes are collected. When an
official page published a person-shaped email address without enough context to verify a public
professional role, the pass retained only the organization's public main line.

The tenth pass addresses business-network, trade-pipeline, and vocational-training gaps. It adds
19 local and regional business associations, three trade associations, ten vocational or
agricultural technical schools, and ten worker-serving economic-development, planning, fishing,
and civic organizations. The business networks include organizations centered in Hanover,
Pembroke, women in business, and Hispanic and minority-owned businesses, but the records remain
strictly organization-level: no member lists, student records, individual demographic attributes,
or inferred traits are collected. All 42 prospects have a current official, government, or
official umbrella source and at least one public organizational or role-based contact channel.

To reproduce the database update from a database that already contains the base prospect
imports:

```bash
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round2.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round2.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round3.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round3.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round4.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round4.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round5.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round5.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round6.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round6.csv
python -m src.cli import-osha \
  --url https://www.osha.gov/sites/default/files/ITA_300A_Summary_Data_2025_through_03-15-2026_v2.csv \
  --city Hanover \
  --city Pembroke
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round7.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round7.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round8.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round8.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round9.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round9.csv
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17_round10.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17_round10.csv
python -m src.cli score
python -m src.cli build-deployment-seed
```

The generated deployment seed is `data/deployment/prospects.db.gz`. It is tracked so a
normal `git push` updates the Streamlit deployment without requiring an import command in
the hosted runtime.
