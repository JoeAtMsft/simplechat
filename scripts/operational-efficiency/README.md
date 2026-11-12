# Operational-efficiency CSV import

Streams the 15 connected CSVs from `va-data/OperationalEfficiency-Relational`
into the existing Azure SQL database:

- Server: `hackmedicine2025.database.windows.net`
- Database: `OperationalEfficiencyData`
- Schema: `oe`

The loader never creates a database, changes its service tier, opens firewall
rules, drops tables, or deletes source records. It refuses to adopt a populated
`oe` schema without its own version marker. SQL scripts are generated from
[Catalog.cs](OperationalEfficiency.Import/Catalog.cs) so the loader and DDL
use the same column definitions.

## Requirements

- .NET 10 SDK and Windows PowerShell 5.1 or PowerShell 7.
- Network/firewall access to the SQL server on port 1433.
- A Microsoft Entra identity with access to the database and permissions to
  create the schema, tables, indexes, and foreign keys, insert/update the import
  tables, and read the imported data. `db_owner` is sufficient but not required.
- For `-Authentication AzureCli`, Azure CLI with an existing `az login` session
  in the server's tenant. The default `Browser` authentication opens an
  interactive sign-in. Tokens are never printed or written to this repository;
  browser tokens use the operating system's protected identity cache.
- Sufficient database storage, transaction-log throughput, and time for roughly
  65 million source rows plus secondary indexes. CSV byte size is not a reliable
  estimate of database/index storage. `Check` reports the existing service
  objective and data-file limits; it does not resize anything.

## Run from the repository root

```powershell
# Check authentication, capacity, permissions, and existing oe objects.
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 `
    -Mode Check -Authentication AzureCli

# Read and type-check EVERY record, calculate SHA-256 hashes, and write a manifest.
# City repair is limited to the nine reviewed organization IDs.
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 `
    -Mode Validate -AllowOrganizationCityRepair

# Load/resume all 15 files, then build indexes and trusted foreign keys.
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 `
    -Mode Import -Authentication AzureCli -AllowOrganizationCityRepair

# Inspect completion records and foreign-key trust.
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 `
    -Mode Status -Authentication AzureCli

# Optional live request/index diagnostics (requires VIEW DATABASE STATE or equivalent).
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 `
    -Mode Diagnostics -Authentication AzureCli
```

Use `-DataDirectory 'C:\path\to\csvs'` for a different source folder and
`-ManifestPath 'C:\path\to\import-manifest.json'` for a different manifest.
The default manifest is saved beside the CSVs, outside tracked source files.
`-BatchSize` defaults to 25,000 rows and accepts 1 through 100,000.
For browser authentication, `-UserId` optionally supplies a sign-in hint.

Validation does not connect to SQL. It rejects missing/extra/reordered columns,
unexpected row widths, invalid GUIDs/dates/numbers, nonfinite floats, required
empty values, and overlength text. Error messages identify the file, source
record, and column without printing patient values. Blank optional fields become
SQL `NULL`; leading-zero ZIPs and clinical codes remain text.

The nine reviewed unquoted commas in organization CITY fields require explicit
`-AllowOrganizationCityRepair`. The loader reconstructs the city without removing
the comma, logs each correction, and records the count in the manifest/database.
Other malformed records fail. The CSVs themselves are never rewritten.

## Relational model

The imported query tables are:

| Tables | Relationships |
|---|---|
| `oe.Patients` | Unique original patient `Id` |
| `oe.Organizations`, `oe.Providers`, `oe.Payers` | Unique original `Id`; provider `ORGANIZATION` references organizations |
| `oe.Encounters` | `PATIENT`, `ORGANIZATION`, `PROVIDER`, and `PAYER` reference their lookup tables |
| `oe.Allergies`, `oe.CarePlans`, `oe.Conditions`, `oe.Devices`, `oe.ImagingStudies`, `oe.Immunizations`, `oe.Medications`, `oe.Procedures`, `oe.Supplies` | `PATIENT` and optional `ENCOUNTER`; medications also reference `PAYER` |
| `oe.PayerTransitions` | `PATIENT` and optional `PAYER` |
| `oe.PayerStatistics`, `oe.OrganizationStatistics` | Original per-source-record metrics referencing the unique parent `Id` |

Original GUIDs are retained as `uniqueidentifier`, with **unique nonclustered
indexes** for joins. They are not regenerated. Each table has a compact,
increasing `bigint SourceRow` clustered primary key, derived from the CSV record
ordinal (excluding its header), so random GUIDs do not drive clustered inserts.
Tables without an original ID still receive a stable unique row key.

The payer CSV contains 510 rows for 10 payer IDs; every payer has 51 different
metric rows. Organizations also contain a repeated ID with consistent identity
attributes. The loader therefore preserves these complete source tables in
`oe.PayersSource` and `oe.OrganizationsSource`, materializes unique lookup rows,
and preserves **every metric row** in the corresponding statistics table.
Conflicting identity attributes fail finalization rather than choosing an
arbitrary value.

`SourceRow` is a provenance/resume key, **not** a join between different CSVs.
The statistics' original geographic/run/time grain is not established by these
CSVs. Do not infer it from payer headquarters or automatically sum/average these
rows. In particular, join encounters to `oe.Payers`, not `oe.PayersSource` or
unaggregated `oe.PayerStatistics`, to avoid multiplying matches 51 times.

Money uses `decimal(28,8)` with explicit rounding to eight fractional digits,
removing finer binary-floating-point artifacts. Counts use `bigint`, dates use
`date`, and UTC event timestamps use `datetime2(0)` (interpret them as UTC).
Coordinates and quality averages use `float(53)`. Source files and their hashes
remain available for exact original textual representations.

## Indexes, integrity, and recovery

- Append-friendly clustered primary keys are present while streaming.
- Unique GUID indexes, patient/date indexes, encounter/reference indexes, and an
  encounter start/class index are built after the data load.
- Every foreign key uses `WITH CHECK`; all must be enabled and trusted.
- Finalization also checks that each referenced encounter belongs to the event's
  patient and verifies that all 15 source row counts were preserved.
- Secondary indexes are baseline access paths, not a substitute for tuning
  measured workloads. The loader refreshes index statistics after finalization.
- One database application lock prevents concurrent importer instances.
- Each bulk-copy batch commits atomically. A failed file resumes after its last
  committed `SourceRow`; completed files are skipped. The database row count
  must equal the last source ordinal before resuming.
- SHA-256 hashes must match the preflight manifest and original import metadata.
  Files are held read-only during loading. Changed datasets require a new
  database or a separately designed migration; there is no destructive reset.
- If index/constraint creation fails after loading, source data stays available
  for diagnosis. Fix the data/model issue and rerun `-Mode Finalize` (or `Import`)
  against the same immutable dataset. Do not treat the database as ready until
  the final `VERIFIED` message and trusted-constraint checks pass.
- Do not manually edit loader-owned tables during or between import attempts.

The generated [schema.sql](schema.sql) and [finalize.sql](finalize.sql) can also
be inspected/run in a SQL editor connected **directly** to the target database.
SQL alone cannot open a workstation's `C:` drive: the PowerShell/.NET loader
performs the CSV transfer and maintains the import manifest/completion metadata.
Do not run finalization until all source files are complete.

Regenerate both SQL files after catalog/model changes:

```powershell
.\scripts\operational-efficiency\Import-OperationalEfficiency.ps1 -Mode Scripts
```

## Example queries

```sql
-- Safe unique-payer join; no join to the repeated statistics rows.
SELECT p.NAME, COUNT_BIG(*) AS EncounterCount,
       SUM(e.TOTAL_CLAIM_COST) AS TotalClaimCost
FROM oe.Encounters AS e
LEFT JOIN oe.Payers AS p ON p.Id = e.PAYER
GROUP BY p.NAME;

-- A patient's encounters; parameterize @PatientId as uniqueidentifier.
SELECT e.Id, e.[START], e.[STOP], e.ENCOUNTERCLASS, e.TOTAL_CLAIM_COST
FROM oe.Encounters AS e
WHERE e.PATIENT = @PatientId
ORDER BY e.[START];
```

## Tests

```powershell
dotnet test .\scripts\operational-efficiency\OperationalEfficiency.Import.Tests\OperationalEfficiency.Import.Tests.csproj --configuration Release
```

Tests cover CSV quoting/newlines, strict headers, typed values, nulls, numeric
rounding, safe error messages, opt-in/restricted city correction, resume row
ordinals, bulk-copy metadata, normalized metric preservation, and parsing the
generated T-SQL in Azure SQL mode. They do not authenticate to Azure or import
production data. Live `Check`, full `Validate`, `Import`, and `Status` are separate
end-to-end checks.
