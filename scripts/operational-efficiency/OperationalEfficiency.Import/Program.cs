using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using Azure.Core;
using Azure.Identity;
using Microsoft.Data.SqlClient;

namespace OperationalEfficiency.Import;

internal sealed record FileProfile(string File, string Sha256, long Bytes, long Rows, long RepairedRows);

internal static class Program
{
    private const string Server = "hackmedicine2025.database.windows.net";
    private const string Database = "OperationalEfficiencyData";

    public static async Task<int> Main(string[] args)
    {
        try
        {
            return await Run(args);
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"IMPORT FAILED ({exception.GetType().Name}): {exception.Message}");
            return 1;
        }
    }

    private static async Task<int> Run(string[] args)
    {
        if (args.Length == 0)
            throw new ArgumentException("Supply scripts, validate, check, import, finalize, status, or diagnostics.");
        var mode = args[0];
        var options = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 1; index < args.Length; index += 2)
        {
            if (index + 1 >= args.Length || !args[index].StartsWith("--", StringComparison.Ordinal))
                throw new ArgumentException("Options must be --name value pairs.");
            if (!options.TryAdd(args[index], args[index + 1]))
                throw new ArgumentException($"Duplicate option: {args[index]}");
        }
        var allowed = new[] { "--data", "--output", "--manifest", "--allow-city-repair", "--batch-size", "--user", "--authentication" };
        if (options.Keys.Any(key => !allowed.Contains(key)))
            throw new ArgumentException("Unrecognized option.");
        var folder = options.GetValueOrDefault("--data",
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, @"..\..\..\..\..\..\va-data\OperationalEfficiency-Relational")));
        var allowRepair = bool.Parse(options.GetValueOrDefault("--allow-city-repair", "false"));
        var manifest = options.GetValueOrDefault("--manifest", Path.Combine(folder, "import-manifest.json"));
        var batchSize = int.Parse(options.GetValueOrDefault("--batch-size", "25000"), CultureInfo.InvariantCulture);
        if (batchSize < 1 || batchSize > 100000)
            throw new ArgumentException("Batch size must be between 1 and 100000.");

        if (mode == "scripts")
        {
            var output = options.GetValueOrDefault("--output", Directory.GetCurrentDirectory());
            Directory.CreateDirectory(output);
            await File.WriteAllTextAsync(Path.Combine(output, "schema.sql"), SqlScripts.Schema());
            await File.WriteAllTextAsync(Path.Combine(output, "finalize.sql"), SqlScripts.FinalizeImport());
            Console.WriteLine($"Generated SQL scripts in {output}.");
            return 0;
        }
        if (mode == "validate")
        {
            var profiles = new List<FileProfile>();
            foreach (var table in Catalog.Tables)
            {
                var path = Path.Combine(folder, table.File);
                using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                var hash = Convert.ToHexString(await SHA256.HashDataAsync(input));
                using var csv = new CsvRows(table, path, allowRepair);
                while (csv.Read()) { }
                if (csv.RecordNumber == 0)
                    throw new InvalidDataException($"{table.File}: no records.");
                profiles.Add(new(table.File, hash, input.Length, csv.RecordNumber, csv.RepairedRows));
                Console.WriteLine($"VALID: {table.File}: {csv.RecordNumber:N0} rows; {csv.RepairedRows} corrections.");
            }
            await File.WriteAllTextAsync(manifest, JsonSerializer.Serialize(profiles,
                new JsonSerializerOptions { WriteIndented = true }));
            Console.WriteLine($"Preflight complete. Manifest: {manifest}");
            return 0;
        }
        if (mode is not ("check" or "import" or "finalize" or "status" or "diagnostics"))
            throw new ArgumentException($"Unknown mode: {mode}");
        var authentication = options.GetValueOrDefault("--authentication", "Browser");
        if (authentication is not ("Browser" or "AzureCli"))
            throw new ArgumentException("Authentication must be Browser or AzureCli.");

        var builder = new SqlConnectionStringBuilder
        {
            DataSource = $"tcp:{Server},1433",
            InitialCatalog = Database,
            Encrypt = SqlConnectionEncryptOption.Mandatory,
            TrustServerCertificate = false,
            ConnectTimeout = 60,
            ApplicationName = "OperationalEfficiency.Import"
        };
        await using var connection = new SqlConnection(builder.ConnectionString);
        TokenCredential? credential = null;
        connection.AccessTokenCallback = async (parameters, cancellationToken) =>
        {
            var tenant = new Uri(parameters.Authority).AbsolutePath.Trim('/');
            credential ??= authentication == "AzureCli"
                ? new AzureCliCredential(new AzureCliCredentialOptions { TenantId = tenant })
                : new InteractiveBrowserCredential(new InteractiveBrowserCredentialOptions
                {
                    TenantId = tenant,
                    LoginHint = options.GetValueOrDefault("--user"),
                    RedirectUri = new Uri("http://localhost"),
                    TokenCachePersistenceOptions = new TokenCachePersistenceOptions
                    {
                        Name = "OperationalEfficiency.Import"
                    }
                });
            var token = await credential.GetTokenAsync(new TokenRequestContext(
                [parameters.Resource.TrimEnd('/') + "/.default"]), cancellationToken);
            return new SqlAuthenticationToken(token.Token, token.ExpiresOn);
        };
        await connection.OpenAsync();
        Console.WriteLine($"Connected to {Server} / {Database}.");
        if (mode == "check")
        {
            await Report(connection, """
                SELECT DB_NAME() AS DatabaseName,
                       (SELECT CASE WHEN MIN(max_size)<0 THEN NULL
                           ELSE SUM(CONVERT(bigint,max_size))*8192 END
                        FROM sys.database_files WHERE type=0) AS MaxDataFileBytes,
                       (SELECT SUM(CONVERT(bigint, FILEPROPERTY(name,'SpaceUsed')))*8192
                        FROM sys.database_files WHERE type=0) AS UsedDataBytes,
                       HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','CREATE TABLE') AS CanCreateTables,
                       HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','CREATE SCHEMA') AS CanCreateSchema;
                SELECT edition, service_objective, elastic_pool_name
                FROM sys.database_service_objectives WHERE database_id=DB_ID();
                SELECT name, type_desc FROM sys.objects
                WHERE schema_id=SCHEMA_ID('oe') AND is_ms_shipped=0;
                """);
            return 0;
        }
        if (mode == "status")
        {
            await Report(connection, """
                SELECT * FROM oe.ImportFiles ORDER BY FileName;
                SELECT OBJECT_NAME(parent_object_id) AS TableName, name, is_disabled, is_not_trusted
                FROM sys.foreign_keys WHERE schema_id=SCHEMA_ID('oe');
                """);
            return 0;
        }
        if (mode == "diagnostics")
        {
            await Report(connection, """
                SELECT SYSUTCDATETIME() AS CurrentUtc, r.session_id, r.status, r.command,
                       r.wait_type, r.blocking_session_id, r.total_elapsed_time, r.cpu_time,
                       r.logical_reads, r.writes, r.percent_complete,
                       SUBSTRING(t.text, r.statement_start_offset/2+1,
                           CASE WHEN r.statement_end_offset=-1 THEN 800
                                ELSE (r.statement_end_offset-r.statement_start_offset)/2+1 END) AS StatementText
                FROM sys.dm_exec_requests r
                CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
                WHERE r.database_id=DB_ID() AND r.session_id<>@@SPID;
                SELECT OBJECT_NAME(i.object_id) AS TableName, i.name, i.type_desc, i.is_unique
                FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id
                WHERE t.schema_id=SCHEMA_ID('oe') AND i.index_id>0
                ORDER BY TableName, i.name;
                """);
            return 0;
        }
        await Execute(connection, """
            DECLARE @result int;
            EXEC @result=sys.sp_getapplock @Resource=N'OperationalEfficiency.Import',
                @LockMode=N'Exclusive', @LockOwner=N'Session', @LockTimeout=0;
            IF @result<0 THROW 51003, 'Another operational-efficiency import is running.', 1;
            """);
        if (mode == "finalize")
        {
            await EnsureSafeSchema(connection);
            await ExecuteBatches(connection, SqlScripts.FinalizeImport());
            await Verify(connection);
            return 0;
        }

        var loadedProfiles = JsonSerializer.Deserialize<List<FileProfile>>(await File.ReadAllTextAsync(manifest))
            ?? throw new InvalidDataException("Invalid preflight manifest.");
        if (loadedProfiles.Count != Catalog.Tables.Length ||
            !loadedProfiles.Select(p => p.File).Order().SequenceEqual(Catalog.Tables.Select(t => t.File).Order()) ||
            loadedProfiles.Any(p => p.Rows < 1 || p.Bytes < 1 || p.Sha256.Length != 64 ||
                !p.Sha256.All(char.IsAsciiHexDigit) || p.RepairedRows < 0 || p.RepairedRows > p.Rows))
            throw new InvalidDataException("Manifest must contain exactly the 15 expected CSV files.");
        var sourceLocks = new List<FileStream>();
        try
        {
            // Keep read locks until completion so a source cannot change after its hash is checked.
            foreach (var profile in loadedProfiles)
            {
                var input = new FileStream(Path.Combine(folder, profile.File), FileMode.Open, FileAccess.Read, FileShare.Read);
                sourceLocks.Add(input);
                var hash = Convert.ToHexString(await SHA256.HashDataAsync(input));
                if (input.Length != profile.Bytes || hash != profile.Sha256)
                    throw new InvalidDataException($"{profile.File}: file differs from its preflight manifest.");
                if (profile.RepairedRows > 0 && !allowRepair)
                    throw new InvalidDataException("This manifest requires --allow-city-repair true.");
            }
            await EnsureSafeSchema(connection);
            await ExecuteBatches(connection, SqlScripts.Schema());
            foreach (var table in Catalog.Tables)
            {
                var profile = loadedProfiles.Single(p => p.File == table.File);
                await Load(connection, table, profile, folder, allowRepair, batchSize);
            }
            await ExecuteBatches(connection, SqlScripts.FinalizeImport());
            await Verify(connection);
        }
        finally
        {
            foreach (var source in sourceLocks)
                await source.DisposeAsync();
        }
        return 0;
    }

    private static async Task EnsureSafeSchema(SqlConnection connection)
    {
        var count = Convert.ToInt32(await Scalar(connection,
            "SELECT COUNT(*) FROM sys.objects WHERE schema_id=SCHEMA_ID('oe') AND is_ms_shipped=0;"));
        if (count == 0) return;
        if (await Scalar(connection, "SELECT OBJECT_ID(N'oe.ImportVersion', N'U');") is DBNull)
            throw new InvalidOperationException("The oe schema contains existing objects not owned by this importer.");
        if (Convert.ToInt32(await Scalar(connection, "SELECT MAX(Version) FROM oe.ImportVersion;")) != 1)
            throw new InvalidOperationException("Unsupported oe importer schema version.");
    }

    private static async Task Load(SqlConnection connection, Table table, FileProfile profile,
        string folder, bool allowRepair, int batchSize)
    {
        await using (var command = new SqlCommand("""
            IF EXISTS (SELECT 1 FROM oe.ImportFiles WHERE FileName=@file AND
                (Sha256<>@hash OR FileBytes<>@bytes OR ExpectedRows<>@rows))
                THROW 51004, 'Source file changed since the original import. Use a new database for a different dataset.', 1;
            IF NOT EXISTS (SELECT 1 FROM oe.ImportFiles WHERE FileName=@file)
                INSERT oe.ImportFiles(FileName,Sha256,FileBytes,ExpectedRows,RepairedRows)
                VALUES(@file,@hash,@bytes,@rows,@repairs);
            SELECT Completed FROM oe.ImportFiles WHERE FileName=@file;
            """, connection))
        {
            command.Parameters.AddWithValue("@file", profile.File);
            command.Parameters.AddWithValue("@hash", profile.Sha256);
            command.Parameters.AddWithValue("@bytes", profile.Bytes);
            command.Parameters.AddWithValue("@rows", profile.Rows);
            command.Parameters.AddWithValue("@repairs", profile.RepairedRows);
            if (Convert.ToBoolean(await command.ExecuteScalarAsync()))
            {
                Console.WriteLine($"SKIP complete: {profile.File}.");
                return;
            }
        }
        var last = Convert.ToInt64(await Scalar(connection,
            $"SELECT COALESCE(MAX(SourceRow),0) FROM oe.[{table.LoadName}];"));
        var count = Convert.ToInt64(await Scalar(connection, $"SELECT COUNT_BIG(*) FROM oe.[{table.LoadName}];"));
        if (last != count || last > profile.Rows)
            throw new InvalidDataException($"{table.Name}: existing source rows are not a contiguous import prefix.");
        using var csv = new CsvRows(table, Path.Combine(folder, table.File), allowRepair, last);
        Console.WriteLine($"LOAD {table.File}: resuming after {last:N0} of {profile.Rows:N0} rows.");
        using var bulk = new SqlBulkCopy(connection,
            SqlBulkCopyOptions.TableLock | SqlBulkCopyOptions.UseInternalTransaction |
            SqlBulkCopyOptions.KeepNulls | SqlBulkCopyOptions.CheckConstraints, null)
        {
            DestinationTableName = $"oe.[{table.LoadName}]",
            BatchSize = batchSize,
            BulkCopyTimeout = 0,
            EnableStreaming = true,
            NotifyAfter = 250000
        };
        for (var ordinal = 0; ordinal < csv.FieldCount; ordinal++)
            bulk.ColumnMappings.Add(ordinal, csv.GetName(ordinal));
        bulk.SqlRowsCopied += (_, progress) =>
            Console.WriteLine($"  {table.File}: {last + progress.RowsCopied:N0}/{profile.Rows:N0} rows sent.");
        await bulk.WriteToServerAsync(csv);
        if (csv.RecordNumber != profile.Rows || csv.RepairedRows != profile.RepairedRows ||
            Convert.ToInt64(await Scalar(connection, $"SELECT COUNT_BIG(*) FROM oe.[{table.LoadName}];")) != profile.Rows)
            throw new InvalidDataException($"{table.File}: loaded row count or correction count does not match preflight.");
        await using var complete = new SqlCommand("""
            UPDATE oe.ImportFiles SET Completed=1, CompletedUtc=SYSUTCDATETIME() WHERE FileName=@file;
            """, connection);
        complete.Parameters.AddWithValue("@file", profile.File);
        await complete.ExecuteNonQueryAsync();
        Console.WriteLine($"COMPLETE: {table.File}.");
    }

    private static async Task Verify(SqlConnection connection)
    {
        foreach (var table in Catalog.Tables)
        {
            var actualTable = table.Metrics is null ? table.Name : table.Name[..^1] + "Statistics";
            await using var command = new SqlCommand($"""
                IF (SELECT COUNT_BIG(*) FROM oe.[{actualTable}]) <>
                    (SELECT ExpectedRows FROM oe.ImportFiles WHERE FileName=@file)
                    THROW 51005, 'Final row count does not match source CSV.', 1;
                """, connection);
            command.CommandTimeout = 0;
            command.Parameters.AddWithValue("@file", table.File);
            await command.ExecuteNonQueryAsync();
        }
        await Execute(connection, """
            IF EXISTS (SELECT 1 FROM sys.foreign_keys
                WHERE schema_id=SCHEMA_ID('oe') AND (is_disabled=1 OR is_not_trusted=1))
                THROW 51006, 'An oe foreign key is disabled or untrusted.', 1;
            """);
        await Report(connection, """
            SELECT FileName, ExpectedRows, RepairedRows, Completed, CompletedUtc FROM oe.ImportFiles ORDER BY FileName;
            SELECT 'Payers' AS TableName, COUNT_BIG(*) AS LookupRows FROM oe.Payers
            UNION ALL SELECT 'PayerStatistics', COUNT_BIG(*) FROM oe.PayerStatistics;
            """);
        Console.WriteLine("VERIFIED: all CSV row counts preserved; all foreign keys enabled and trusted.");
    }

    private static async Task ExecuteBatches(SqlConnection connection, string script)
    {
        var batches = Regex.Split(script, @"^\s*GO\s*$", RegexOptions.Multiline | RegexOptions.IgnoreCase)
            .Where(batch => !string.IsNullOrWhiteSpace(batch)).ToArray();
        for (var index = 0; index < batches.Length; index++)
        {
            Console.WriteLine($"SQL batch {index + 1}/{batches.Length}.");
            await Execute(connection, batches[index]);
        }
    }

    private static async Task Execute(SqlConnection connection, string text)
    {
        await using var command = new SqlCommand(text, connection) { CommandTimeout = 0 };
        await command.ExecuteNonQueryAsync();
    }

    private static async Task<object> Scalar(SqlConnection connection, string text)
    {
        await using var command = new SqlCommand(text, connection) { CommandTimeout = 0 };
        return await command.ExecuteScalarAsync() ?? DBNull.Value;
    }

    private static async Task Report(SqlConnection connection, string text)
    {
        await using var command = new SqlCommand(text, connection) { CommandTimeout = 0 };
        await using var reader = await command.ExecuteReaderAsync();
        do
        {
            Console.WriteLine(string.Join(" | ", Enumerable.Range(0, reader.FieldCount).Select(reader.GetName)));
            while (await reader.ReadAsync())
                Console.WriteLine(string.Join(" | ", Enumerable.Range(0, reader.FieldCount)
                    .Select(index => Convert.ToString(reader.GetValue(index), CultureInfo.InvariantCulture))));
        } while (await reader.NextResultAsync());
    }
}
