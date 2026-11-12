using Microsoft.SqlServer.TransactSql.ScriptDom;

namespace OperationalEfficiency.Import.Tests;

public sealed class ImportTests
{
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void GeneratedSqlParses(bool finalize)
    {
        var parser = new TSql160Parser(true, SqlEngineType.SqlAzure);
        parser.Parse(new StringReader(finalize ? SqlScripts.FinalizeImport() : SqlScripts.Schema()), out var errors);
        Assert.True(errors.Count == 0,
            string.Join(Environment.NewLine, errors.Select(error => $"{error.Line}:{error.Column}: {error.Message}")));
    }

    [Fact]
    public void CatalogContainsExactlyTheConnectedFiles()
    {
        Assert.Equal(15, Catalog.Tables.Length);
        Assert.Equal(15, Catalog.Tables.Select(t => t.File).Distinct().Count());
        Assert.DoesNotContain(Catalog.Tables, t => t.File.StartsWith("Veteran_"));
        Assert.All(Catalog.Tables, table =>
            Assert.Equal(table.Columns.Length, table.Columns.Select(c => c.Name).Distinct().Count()));
    }

    [Fact]
    public void GuidAndNullConversionsAreTyped()
    {
        var id = Guid.NewGuid();
        Assert.Equal(id, CsvRows.ConvertValue(new("Id", ValueKind.Guid, true), id.ToString(), "test.csv", 1));
        Assert.Same(DBNull.Value, CsvRows.ConvertValue(new("STOP", ValueKind.Date), "", "test.csv", 1));
        Assert.Throws<InvalidDataException>(() =>
            CsvRows.ConvertValue(new("PATIENT", ValueKind.Guid, true), "", "test.csv", 4));
    }

    [Theory]
    [InlineData((int)ValueKind.Guid, "bad-guid")]
    [InlineData((int)ValueKind.Date, "2025-02-30")]
    [InlineData((int)ValueKind.Timestamp, "2025-01-01T00:00:00+05:00")]
    [InlineData((int)ValueKind.Integer, "1.5")]
    [InlineData((int)ValueKind.Float, "NaN")]
    [InlineData((int)ValueKind.Float, "Infinity")]
    [InlineData((int)ValueKind.Money, "100000000000000000000")]
    public void InvalidValuesFailWithoutPrintingTheValue(int kind, string value)
    {
        var exception = Assert.Throws<InvalidDataException>(() =>
            CsvRows.ConvertValue(new("TEST", (ValueKind)kind), value, "sample.csv", 42));
        Assert.Contains("record 42", exception.Message);
        Assert.DoesNotContain(value, exception.Message);
    }

    [Fact]
    public void TextOverflowFailsInsteadOfTruncating()
    {
        Assert.Throws<InvalidDataException>(() =>
            CsvRows.ConvertValue(new("NAME", ValueKind.Text, Length: 3), "long", "sample.csv", 1));
    }

    [Fact]
    public void MoneyRoundingIsExplicitAndCodesRemainText()
    {
        Assert.Equal(12.12345679m,
            CsvRows.ConvertValue(new("COST", ValueKind.Money), "12.123456789", "sample.csv", 1));
        Assert.Equal("00123", CsvRows.ConvertValue(new("CODE", ValueKind.Text), "00123", "sample.csv", 1));
        var date = Assert.IsType<DateTime>(
            CsvRows.ConvertValue(new("DATE", ValueKind.Timestamp), "2025-01-02T03:04:05Z", "sample.csv", 1));
        Assert.Equal(DateTimeKind.Utc, date.Kind);
    }

    [Fact]
    public void CsvReaderHandlesQuotesNewlinesEmptyFieldsAndResume()
    {
        var table = new Table("sample.csv", "Sample",
            [new("Id", ValueKind.Guid, true), new("DESCRIPTION", ValueKind.Text), new("STOP", ValueKind.Date)]);
        var id = Guid.NewGuid();
        WithFile($"Id,DESCRIPTION,STOP\r\n{id},\"first, quoted\",\r\n{id},\"second\r\nline\",\r\n", path =>
        {
            using var reader = new CsvRows(table, path, false, skip: 1);
            Assert.True(reader.Read());
            Assert.Equal(2L, reader.GetInt64(0));
            Assert.Equal(id, reader.GetGuid(1));
            Assert.Equal("second\r\nline", reader.GetString(2));
            Assert.True(reader.IsDBNull(3));
            Assert.False(reader.Read());
            Assert.Equal(2, reader.RecordNumber);
        });
    }

    [Fact]
    public void HeaderMismatchAndMalformedWidthsFail()
    {
        var table = new Table("sample.csv", "Sample", [new("NAME", ValueKind.Text)]);
        WithFile("OTHER\nvalue\n", path => Assert.Throws<InvalidDataException>(() => new CsvRows(table, path, false)));
        WithFile("NAME\none,two\n", path =>
        {
            using var reader = new CsvRows(table, path, false);
            Assert.Throws<InvalidDataException>(() => reader.Read());
        });
    }

    [Fact]
    public void OrganizationCityRepairIsOptInAndPreservesTheComma()
    {
        var table = Catalog.Tables.Single(t => t.Name == "Organizations");
        const string record = "9ea8eeb1-8bd5-3bf4-a7fb-67333c847793,Clinic,1 Main St,ROCKFORD, AL,AL,35136,33.032235,-86.323775,,12.25,140\n";
        WithFile(string.Join(",", table.Columns.Select(c => c.Name)) + "\n" + record, path =>
        {
            using (var strict = new CsvRows(table, path, false))
                Assert.Throws<InvalidDataException>(() => strict.Read());
            using var repaired = new CsvRows(table, path, true);
            Assert.True(repaired.Read());
            Assert.Equal("ROCKFORD, AL", repaired.GetString(repaired.GetOrdinal("CITY")));
            Assert.Equal("AL", repaired.GetString(repaired.GetOrdinal("STATE")));
            Assert.Equal(12.25m, repaired.GetDecimal(repaired.GetOrdinal("REVENUE")));
            Assert.Equal(1, repaired.RepairedRows);
        });
    }

    [Fact]
    public void OrganizationRepairRejectsUnreviewedMalformedRecords()
    {
        var table = Catalog.Tables.Single(t => t.Name == "Organizations");
        var record = $"{Guid.NewGuid()},Clinic,1 Main St,ROCKFORD, AL,AL,35136,33.032235,-86.323775,,12.25,140\n";
        WithFile(string.Join(",", table.Columns.Select(c => c.Name)) + "\n" + record, path =>
        {
            using var reader = new CsvRows(table, path, true);
            Assert.Throws<InvalidDataException>(() => reader.Read());
        });
    }

    [Fact]
    public void ReaderExposesBulkCopyColumnMetadata()
    {
        var table = new Table("sample.csv", "Sample", [new("Id", ValueKind.Guid, true)]);
        WithFile($"Id\n{Guid.NewGuid()}\n", path =>
        {
            using var reader = new CsvRows(table, path, false);
            using var schema = reader.GetSchemaTable();
            Assert.Equal(2, schema.Rows.Count);
            Assert.Equal("SourceRow", schema.Rows[0]["ColumnName"]);
            Assert.Equal(typeof(Guid), schema.Rows[1]["DataType"]);
            Assert.Equal(false, schema.Rows[1]["AllowDBNull"]);
        });
    }

    [Fact]
    public void AllPayerMetricRowsRemainSeparateFromTheUniqueIdentity()
    {
        var payer = Catalog.Tables.Single(t => t.Name == "Payers");
        Assert.DoesNotContain(payer.Attributes, column => column.Name == "AMOUNT_COVERED");
        Assert.Contains(payer.MetricColumns, column => column.Name == "AMOUNT_COVERED");
        var sql = SqlScripts.FinalizeImport();
        Assert.Contains("INSERT oe.[PayerStatistics]", sql);
        Assert.Contains("PARTITION BY Id ORDER BY SourceRow", sql);
        Assert.Contains("Conflicting identity attributes", sql);
        Assert.Contains("GROUP BY Id HAVING COUNT_BIG(*)>1", sql);
        Assert.DoesNotContain("JOIN oe.[OrganizationsSource] b", sql);
        Assert.Contains("WITH CHECK CHECK CONSTRAINT", sql);
        Assert.DoesNotContain("NOCHECK", sql.Replace("not NOCHECK", ""));
    }

    private static void WithFile(string content, Action<string> action)
    {
        var path = Path.GetTempFileName();
        try
        {
            File.WriteAllText(path, content);
            action(path);
        }
        finally
        {
            File.Delete(path);
        }
    }
}
