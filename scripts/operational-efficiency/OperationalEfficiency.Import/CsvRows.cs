using System.Data.Common;
using System.Data;
using System.Globalization;
using CsvHelper;
using CsvHelper.Configuration;

namespace OperationalEfficiency.Import;

internal sealed class CsvRows : DbDataReader
{
    private readonly Table table;
    private readonly CsvParser parser;
    private readonly StreamReader stream;
    private readonly bool allowCityRepair;
    private readonly long skip;
    private readonly object[] values;
    private bool closed;
    private static readonly HashSet<Guid> VerifiedCityRepairs =
    [
        new("9ea8eeb1-8bd5-3bf4-a7fb-67333c847793"), new("0a99a4bc-c8b2-367c-923a-db5bdab15714"),
        new("73385428-ddc4-3d32-bd27-47e9212459cf"), new("4c4dac6b-f082-3494-a3ad-e76dd712c7fc"),
        new("d9b0851f-9150-3fb1-ad79-ce49b23b5527"), new("5326ce03-18bb-3e7a-81fb-c971d5e45a88"),
        new("f9291e10-f72b-35c6-9836-50f9427815f3"), new("aaa0b221-2bad-34e7-9eec-6ae53efbc137"),
        new("3f947830-0a10-33b0-aec7-781b390dd0ec")
    ];

    public long RecordNumber { get; private set; }
    public long RepairedRows { get; private set; }

    public CsvRows(Table table, string path, bool allowCityRepair, long skip = 0)
    {
        this.table = table;
        this.allowCityRepair = allowCityRepair;
        this.skip = skip;
        values = new object[table.Columns.Length + 1];
        stream = new StreamReader(path, detectEncodingFromByteOrderMarks: true);
        parser = new CsvParser(stream, new CsvConfiguration(CultureInfo.InvariantCulture)
        {
            HasHeaderRecord = true,
            IgnoreBlankLines = false,
            ExceptionMessagesContainRawData = false
        });
        if (!parser.Read() || !parser.Record!.SequenceEqual(table.Columns.Select(c => c.Name)))
        {
            Dispose();
            throw new InvalidDataException($"{table.File}: header does not match the expected schema.");
        }
    }

    public override bool Read()
    {
        while (parser.Read())
        {
            RecordNumber++;
            var row = parser.Record!;
            if (row.Length != table.Columns.Length)
            {
                if (allowCityRepair && table.File == "organizations.csv" && row.Length == 12 &&
                    Guid.TryParse(row[0], out var organization) && VerifiedCityRepairs.Contains(organization) &&
                    row[5].Length == 2 &&
                    row[5].All(char.IsAsciiLetter) &&
                    double.TryParse(row[7], NumberStyles.Float, CultureInfo.InvariantCulture, out _) &&
                    double.TryParse(row[8], NumberStyles.Float, CultureInfo.InvariantCulture, out _))
                {
                    row = [.. row.Take(3), row[3] + "," + row[4], .. row.Skip(5)];
                    RepairedRows++;
                    Console.WriteLine($"CITY correction: {table.File}, source record {RecordNumber}.");
                }
                else
                {
                    throw new InvalidDataException(
                        $"{table.File}: record {RecordNumber} has {row.Length} fields; expected {table.Columns.Length}.");
                }
            }
            if (RecordNumber <= skip)
                continue;
            values[0] = RecordNumber;
            for (var index = 0; index < table.Columns.Length; index++)
                values[index + 1] = ConvertValue(table.Columns[index], row[index], table.File, RecordNumber);
            return true;
        }
        return false;
    }

    internal static object ConvertValue(Column column, string value, string file, long record)
    {
        if (value.Length == 0)
        {
            if (column.Required)
                throw new InvalidDataException($"{file}: record {record}, {column.Name} is required.");
            return DBNull.Value;
        }
        var culture = CultureInfo.InvariantCulture;
        switch (column.Kind)
        {
            case ValueKind.Text when value.Length <= column.Length:
                return value;
            case ValueKind.Guid when Guid.TryParseExact(value, "D", out var guid):
                return guid;
            case ValueKind.Date when DateTime.TryParseExact(value, "yyyy-MM-dd", culture,
                DateTimeStyles.None, out var date):
                return date;
            case ValueKind.Timestamp when DateTime.TryParseExact(value, "yyyy-MM-dd'T'HH:mm:ss'Z'", culture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var timestamp):
                return timestamp;
            case ValueKind.Money when decimal.TryParse(value, NumberStyles.Float, culture, out var amount) &&
                amount > -100000000000000000000m && amount < 100000000000000000000m:
                var rounded = decimal.Round(amount, 8, MidpointRounding.AwayFromZero);
                if (rounded > -100000000000000000000m && rounded < 100000000000000000000m)
                    return rounded;
                break;
            case ValueKind.Integer when long.TryParse(value, NumberStyles.Integer, culture, out var integer):
                return integer;
            case ValueKind.Float when double.TryParse(value, NumberStyles.Float, culture, out var number) &&
                double.IsFinite(number):
                return number;
        }
        throw new InvalidDataException(
            $"{file}: record {record}, {column.Name} is invalid for {column.SqlType}.");
    }

    public override int FieldCount => values.Length;
    public override bool HasRows => true;
    public override bool IsClosed => closed;
    public override int RecordsAffected => -1;
    public override int Depth => 0;
    public override object this[int ordinal] => GetValue(ordinal);
    public override object this[string name] => GetValue(GetOrdinal(name));
    public override string GetName(int ordinal) => ordinal == 0 ? "SourceRow" : table.Columns[ordinal - 1].Name;
    public override int GetOrdinal(string name) =>
        name == "SourceRow" ? 0 : Array.FindIndex(table.Columns, c => c.Name == name) is var index && index >= 0
            ? index + 1 : throw new IndexOutOfRangeException(name);
    public override Type GetFieldType(int ordinal) => ordinal == 0 ? typeof(long) : table.Columns[ordinal - 1].ClrType;
    public override string GetDataTypeName(int ordinal) => GetFieldType(ordinal).Name;
    public override object GetValue(int ordinal) => values[ordinal];
    public override int GetValues(object[] destination)
    {
        var count = Math.Min(destination.Length, values.Length);
        Array.Copy(values, destination, count);
        return count;
    }
    public override DataTable GetSchemaTable()
    {
        var schema = new DataTable("SchemaTable") { Locale = CultureInfo.InvariantCulture };
        schema.Columns.Add(SchemaTableColumn.ColumnName, typeof(string));
        schema.Columns.Add(SchemaTableColumn.ColumnOrdinal, typeof(int));
        schema.Columns.Add(SchemaTableColumn.DataType, typeof(Type));
        schema.Columns.Add(SchemaTableColumn.ColumnSize, typeof(int));
        schema.Columns.Add(SchemaTableColumn.AllowDBNull, typeof(bool));
        for (var index = 0; index < FieldCount; index++)
            schema.Rows.Add(GetName(index), index, GetFieldType(index),
                index == 0 ? 8 : table.Columns[index - 1].Length,
                index != 0 && !table.Columns[index - 1].Required);
        return schema;
    }
    public override bool IsDBNull(int ordinal) => values[ordinal] is DBNull;
    public override bool NextResult() => false;
    public override Guid GetGuid(int ordinal) => (Guid)GetValue(ordinal);
    public override long GetInt64(int ordinal) => (long)GetValue(ordinal);
    public override decimal GetDecimal(int ordinal) => (decimal)GetValue(ordinal);
    public override double GetDouble(int ordinal) => (double)GetValue(ordinal);
    public override DateTime GetDateTime(int ordinal) => (DateTime)GetValue(ordinal);
    public override string GetString(int ordinal) => (string)GetValue(ordinal);
    public override bool GetBoolean(int ordinal) => (bool)GetValue(ordinal);
    public override byte GetByte(int ordinal) => (byte)GetValue(ordinal);
    public override char GetChar(int ordinal) => (char)GetValue(ordinal);
    public override short GetInt16(int ordinal) => (short)GetValue(ordinal);
    public override int GetInt32(int ordinal) => (int)GetValue(ordinal);
    public override float GetFloat(int ordinal) => (float)GetValue(ordinal);
    public override long GetBytes(int ordinal, long offset, byte[]? buffer, int bufferOffset, int length) =>
        throw new NotSupportedException();
    public override long GetChars(int ordinal, long offset, char[]? buffer, int bufferOffset, int length) =>
        throw new NotSupportedException();
    public override System.Collections.IEnumerator GetEnumerator() => new DbEnumerator(this, false);
    public override void Close()
    {
        if (closed) return;
        closed = true;
        parser.Dispose();
        stream.Dispose();
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) Close();
        base.Dispose(disposing);
    }
}
