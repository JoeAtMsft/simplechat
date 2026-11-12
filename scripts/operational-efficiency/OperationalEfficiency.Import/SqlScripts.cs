using System.Text;

namespace OperationalEfficiency.Import;

internal static class SqlScripts
{
    private static string Fields(IEnumerable<Column> columns) =>
        string.Join(", ", columns.Select(c => $"[{c.Name}]"));

    private static string CreateTable(string name, IEnumerable<Column> columns)
    {
        var definitions = columns.Select(c =>
            $"    [{c.Name}] {c.SqlType} {(c.Required ? "NOT NULL" : "NULL")}");
        return $"""
            IF OBJECT_ID(N'oe.[{name}]', N'U') IS NULL
            CREATE TABLE oe.[{name}] (
                [SourceRow] bigint NOT NULL CONSTRAINT [PK_{name}] PRIMARY KEY CLUSTERED,
            {string.Join(",\n", definitions)}
            );
            GO

            """;
    }

    public static string Schema()
    {
        var sql = new StringBuilder("""
            -- Connect directly to OperationalEfficiencyData on hackmedicine2025.database.windows.net.
            -- Generated from Catalog.cs. No database or existing table is dropped.
            SET XACT_ABORT ON;
            IF SCHEMA_ID(N'oe') IS NULL EXEC(N'CREATE SCHEMA oe AUTHORIZATION dbo;');
            GO
            IF OBJECT_ID(N'oe.ImportFiles', N'U') IS NULL
            CREATE TABLE oe.ImportFiles (
                FileName nvarchar(128) NOT NULL CONSTRAINT PK_ImportFiles PRIMARY KEY,
                Sha256 char(64) NOT NULL,
                FileBytes bigint NOT NULL,
                ExpectedRows bigint NOT NULL,
                RepairedRows bigint NOT NULL,
                Completed bit NOT NULL DEFAULT 0,
                CompletedUtc datetime2(0) NULL
            );
            GO
            IF OBJECT_ID(N'oe.ImportVersion', N'U') IS NULL
            BEGIN
                CREATE TABLE oe.ImportVersion (Version int NOT NULL PRIMARY KEY);
                INSERT oe.ImportVersion VALUES (1);
            END;
            GO

            """);
        foreach (var table in Catalog.Tables)
        {
            sql.Append(CreateTable(table.LoadName, table.Columns));
            if (table.Metrics is null) continue;
            sql.Append(CreateTable(table.Name, table.Attributes));
            sql.Append(CreateTable(table.Name[..^1] + "Statistics",
                [new("Id", ValueKind.Guid, true), .. table.MetricColumns]));
        }
        return sql.ToString();
    }

    private static string UniqueId(string table) => $"""
        IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[{table}]') AND name=N'UX_{table}_Id')
            CREATE UNIQUE NONCLUSTERED INDEX [UX_{table}_Id] ON oe.[{table}] ([Id]);
        GO

        """;

    private static string ForeignKey(string child, string column, string parent)
    {
        var name = $"FK_{child}_{column}_{parent}";
        return $"""
            IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[{child}]') AND name=N'{name}')
                ALTER TABLE oe.[{child}] WITH CHECK ADD CONSTRAINT [{name}]
                    FOREIGN KEY ([{column}]) REFERENCES oe.[{parent}] ([Id]);
            ALTER TABLE oe.[{child}] WITH CHECK CHECK CONSTRAINT [{name}];
            GO

            """;
    }

    private static string Index(string table, string suffix, string columns) => $"""
        IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[{table}]') AND name=N'IX_{table}_{suffix}')
            CREATE NONCLUSTERED INDEX [IX_{table}_{suffix}] ON oe.[{table}] ({columns});
        GO

        """;

    public static string FinalizeImport()
    {
        var sql = new StringBuilder("""
            -- Run after all 15 CSVs are complete. All relationships are validated, not NOCHECK.
            SET XACT_ABORT ON;
            IF (SELECT COUNT(*) FROM oe.ImportFiles WHERE Completed=1) <> 15
                THROW 51000, 'All 15 CSV files must be loaded before finalization.', 1;
            GO

            """);
        foreach (var table in Catalog.Tables.Where(t => t.Metrics is not null))
        {
            var fields = Fields(table.Attributes);
            var stats = table.Name[..^1] + "Statistics";
            sql.Append($"""
                IF EXISTS (
                    SELECT Id FROM (
                        SELECT DISTINCT {fields} FROM oe.[{table.LoadName}]
                    ) identities
                    GROUP BY Id HAVING COUNT_BIG(*)>1
                ) THROW 51001, 'Conflicting identity attributes in {table.File}; source rows retained.', 1;
                BEGIN TRANSACTION;
                INSERT oe.[{table.Name}] ([SourceRow], {fields})
                SELECT [SourceRow], {fields} FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY Id ORDER BY SourceRow) AS DuplicateRank
                    FROM oe.[{table.LoadName}]
                ) ranked
                WHERE DuplicateRank=1 AND NOT EXISTS (
                    SELECT 1 FROM oe.[{table.Name}] existing WHERE existing.Id=ranked.Id
                );
                INSERT oe.[{stats}] ([SourceRow], [Id], {Fields(table.MetricColumns)})
                SELECT [SourceRow], [Id], {Fields(table.MetricColumns)} FROM oe.[{table.LoadName}] source
                WHERE NOT EXISTS (SELECT 1 FROM oe.[{stats}] existing WHERE existing.SourceRow=source.SourceRow);
                COMMIT;
                GO

                """);
        }
        foreach (var table in Catalog.Tables.Where(t => t.Columns.Any(c => c.Name == "Id")))
            sql.Append(UniqueId(table.Name));

        foreach (var table in Catalog.Tables)
        {
            foreach (var column in table.Attributes.Where(c => Catalog.Parents.ContainsKey(c.Name)))
            {
                sql.Append(ForeignKey(table.Name, column.Name, Catalog.Parents[column.Name]));
                if (column.Name == "PATIENT")
                {
                    var date = table.Columns.FirstOrDefault(c => c.Name is "START" or "DATE" or "START_YEAR");
                    sql.Append(Index(table.Name, "PatientDate",
                        $"[PATIENT]{(date is null ? "" : $", [{date.Name}]")}"));
                }
                else
                    sql.Append(Index(table.Name, column.Name, $"[{column.Name}]"));
            }
            if (table.Columns.Any(c => c.Name == "ENCOUNTER"))
            {
                sql.Append($"""
                    IF EXISTS (
                        SELECT 1 FROM oe.[{table.Name}] child
                        JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
                        WHERE child.PATIENT<>encounter.PATIENT
                    ) THROW 51002, 'Encounter/patient mismatch in {table.Name}.', 1;
                    GO

                    """);
            }
            if (table.Metrics is not null)
            {
                var stats = table.Name[..^1] + "Statistics";
                sql.Append(ForeignKey(stats, "Id", table.Name));
                sql.Append(Index(stats, "Id", "[Id]"));
            }
        }
        sql.Append(Index("Encounters", "StartClass", "[START], [ENCOUNTERCLASS]"));
        foreach (var table in Catalog.Tables)
        {
            sql.Append($"UPDATE STATISTICS oe.[{table.Name}] WITH RESAMPLE;\nGO\n");
            if (table.Metrics is not null)
                sql.Append($"UPDATE STATISTICS oe.[{table.Name[..^1]}Statistics] WITH RESAMPLE;\nGO\n");
        }
        return sql.ToString();
    }
}
