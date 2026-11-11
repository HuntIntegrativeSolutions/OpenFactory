# Scene migration corpus (PLAN 4.7)

One folder per format version that a migration starts from (`tests/migrate/<version>/`), holding
every shipped scene as it was in that version, under the shipped file's own path relative to
`scenes/` (for example `0.1/sorting-by-height.yaml`, `0.1/_library/accumulation_conveyor.yaml`).
`tests/unit/test_migrate.py` migrates each to the current format and requires the shipped file
byte for byte, and fails if `MIGRATIONS` (`src/openfactory/core/migrate.py`) has a source version
without a folder here, or a folder here has no migration.

The format is still `0.1` and has never broken, so there is no migration and no folder yet. The
first breaking change copies every shipped scene here under the old version *before* rewriting
them, then adds its migration.
