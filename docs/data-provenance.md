# TAWOS v1.1 data provenance

This repository uses the official TAWOS v1.1 dataset only as a local, read-only
source for the benchmark foundation. The archive is not a repository artifact and
must not be committed or copied into another project path.

## Source artifact

| Field | Value |
|---|---|
| Dataset | The TAWOS dataset |
| Dataset content release | TAWOS v1.1 |
| Official record | <https://rdr.ucl.ac.uk/articles/dataset/The_TAWOS_dataset/21308124> |
| DOI | <https://doi.org/10.5522/04/21308124.v1> |
| Official schema | <https://github.com/SOLAR-group/TAWOS/blob/main/TAWOS_Database_Schema_Creation_Script.sql> |
| Archive | `TAWOS.sql.zip` |
| Local path | `data/raw/TAWOS.sql.zip` (local only) |
| Download date | 2026-07-13 |
| Size | 637,550,449 bytes |
| Published MD5 | `e9c5ecc7649d55f0cf2fb4efb5664494` |
| Verified local MD5 | `e9c5ecc7649d55f0cf2fb4efb5664494` |
| Verified local SHA-256 | `278984f788008c58d338e1f4aa195eae8e5b15b4153e51c247659ef8465917f7` |
| License | Apache License 2.0 |

The UCL record's article metadata reports article version 1; “TAWOS v1.1” is
the dataset content release described by that record. Local byte size and both
checksums were verified after download.

## Release facts used by this PoC

TAWOS v1.1 contains 458,232 issues from 39 projects in 12 Jira repositories.
Older figures of 508,963 issues, 44 projects, and 13 repositories describe
TAWOS v1.0 and must not be used for this benchmark.

## Identity and schema limitations

The v1.1 `User` table contains only `ID` and `Project_ID`. It does not provide
person names or a reliable identity that can be joined across projects. This
repository therefore treats a user as project-local:

- `person_id` is `<project_key>:<user_id>`;
- `person_name` is the explicit pseudonym `Person <project_key>-<user_id>`;
- no cross-project identity resolution is attempted; and
- historical assignee is benchmark ground truth for assignee prediction, not
  evidence that the assignee was the uniquely or optimally qualified person.

The official schema has no labels table. Stage 0 emits `labels: []` rather than
inventing label values. Components and comments may be joined from their real
schema tables, subject to the benchmark's as-of-time leakage rules.

## License, ethics, and handling

The dataset is distributed under the Apache License 2.0. The official record
also includes research and ethics terms discouraging harmful use and attempts
to re-identify people. This PoC preserves opaque, project-qualified identifiers,
uses pseudonymous display values, and does not claim that records across projects
belong to the same person.

The large archive stays under `data/raw/` and is excluded from version control.
Derived reports and benchmark manifests should record their generation settings,
schema/manifest version, cutoff, and deterministic seed so they can be traced to
this exact source artifact.
