# Curated public contact data

This directory contains small, reviewable imports that are intentionally tracked in Git.
Each asserted contact field includes its source URL, publisher, retrieval date, source
strength, and validation status. The files contain public organizational channels and
role-based professional contacts only; they do not contain private or individual-level
contact data.

The 2026-08-17 verification pass prioritizes existing high-ranked prospects with missing
contact details. Official organization pages are assigned source strength 4 and official
government records are assigned source strength 5. Older official documents retain their
publication dates so the application's freshness checks can flag them appropriately.

To reproduce the database update from a database that already contains the base prospect
imports:

```bash
python -m src.cli import-organizations \
  data/curated/verified_organization_contacts_2026-08-17.csv
python -m src.cli import-contacts \
  data/curated/verified_role_contacts_2026-08-17.csv
python -m src.cli score
python -m src.cli build-deployment-seed
```

The generated deployment seed is `data/deployment/prospects.db.gz`. It is tracked so a
normal `git push` updates the Streamlit deployment without requiring an import command in
the hosted runtime.
