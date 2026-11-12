namespace OperationalEfficiency.Import;

internal enum ValueKind { Text, Guid, Date, Timestamp, Money, Integer, Float }

internal sealed record Column(string Name, ValueKind Kind, bool Required = false, int Length = 256)
{
    public string SqlType => Kind switch
    {
        ValueKind.Guid => "uniqueidentifier",
        ValueKind.Date => "date",
        ValueKind.Timestamp => "datetime2(0)",
        ValueKind.Money => "decimal(28,8)",
        ValueKind.Integer => "bigint",
        ValueKind.Float => "float(53)",
        _ => $"nvarchar({Length})"
    };

    public Type ClrType => Kind switch
    {
        ValueKind.Guid => typeof(Guid),
        ValueKind.Date or ValueKind.Timestamp => typeof(DateTime),
        ValueKind.Money => typeof(decimal),
        ValueKind.Integer => typeof(long),
        ValueKind.Float => typeof(double),
        _ => typeof(string)
    };
}

internal sealed record Table(string File, string Name, Column[] Columns, string[]? Metrics = null)
{
    public string LoadName => Metrics is null ? Name : Name + "Source";
    public IEnumerable<Column> Attributes => Columns.Where(c => !(Metrics ?? []).Contains(c.Name));
    public IEnumerable<Column> MetricColumns => Columns.Where(c => (Metrics ?? []).Contains(c.Name));
}

internal static class Catalog
{
    private static Column Text(string name, int length = 256, bool required = false) =>
        new(name, ValueKind.Text, required, length);
    private static Column Id(string name = "Id", bool required = true) => new(name, ValueKind.Guid, required);
    private static Column Date(string name, bool required = false) => new(name, ValueKind.Date, required);
    private static Column Time(string name, bool required = false) => new(name, ValueKind.Timestamp, required);
    private static Column Money(string name) => new(name, ValueKind.Money);
    private static Column Number(string name) => new(name, ValueKind.Integer);
    private static Column Float(string name) => new(name, ValueKind.Float);
    private static Column[] Event(Column[] dates, Column[]? extra = null) =>
        [.. dates, Id("PATIENT"), Id("ENCOUNTER", false), Text("CODE", 64), Text("DESCRIPTION", 1024), .. extra ?? []];

    public static readonly Table[] Tables =
    [
        new("patients.csv", "Patients",
        [
            Id(), Date("BIRTHDATE", true), Date("DEATHDATE"), Text("SSN", 32), Text("DRIVERS", 64),
            Text("PASSPORT", 64), Text("PREFIX", 32), Text("FIRST", 128), Text("LAST", 128),
            Text("SUFFIX", 32), Text("MAIDEN", 128), Text("MARITAL", 16), Text("RACE", 128),
            Text("ETHNICITY", 128), Text("GENDER", 32), Text("BIRTHPLACE", 256), Text("ADDRESS", 512),
            Text("CITY", 128), Text("STATE", 128), Text("COUNTY", 128), Text("ZIP", 32),
            Float("LAT"), Float("LON"), Money("HEALTHCARE_EXPENSES"), Money("HEALTHCARE_COVERAGE")
        ]),
        new("organizations.csv", "Organizations",
        [
            Id(), Text("NAME"), Text("ADDRESS", 512), Text("CITY", 128), Text("STATE", 128),
            Text("ZIP", 32), Float("LAT"), Float("LON"), Text("PHONE", 128),
            Money("REVENUE"), Number("UTILIZATION")
        ], ["REVENUE", "UTILIZATION"]),
        new("providers.csv", "Providers",
        [
            Id(), Id("ORGANIZATION"), Text("NAME"), Text("GENDER", 32), Text("SPECIALITY", 128),
            Text("ADDRESS", 512), Text("CITY", 128), Text("STATE", 128), Text("ZIP", 32),
            Float("LAT"), Float("LON"), Number("UTILIZATION")
        ]),
        new("payers.csv", "Payers",
        [
            Id(), Text("NAME"), Text("ADDRESS", 512), Text("CITY", 128), Text("STATE_HEADQUARTERED", 128),
            Text("ZIP", 32), Text("PHONE", 128), Money("AMOUNT_COVERED"), Money("AMOUNT_UNCOVERED"),
            Money("REVENUE"), Number("COVERED_ENCOUNTERS"), Number("UNCOVERED_ENCOUNTERS"),
            Number("COVERED_MEDICATIONS"), Number("UNCOVERED_MEDICATIONS"), Number("COVERED_PROCEDURES"),
            Number("UNCOVERED_PROCEDURES"), Number("COVERED_IMMUNIZATIONS"), Number("UNCOVERED_IMMUNIZATIONS"),
            Number("UNIQUE_CUSTOMERS"), Float("QOLS_AVG"), Number("MEMBER_MONTHS")
        ],
        [
            "AMOUNT_COVERED", "AMOUNT_UNCOVERED", "REVENUE", "COVERED_ENCOUNTERS", "UNCOVERED_ENCOUNTERS",
            "COVERED_MEDICATIONS", "UNCOVERED_MEDICATIONS", "COVERED_PROCEDURES", "UNCOVERED_PROCEDURES",
            "COVERED_IMMUNIZATIONS", "UNCOVERED_IMMUNIZATIONS", "UNIQUE_CUSTOMERS", "QOLS_AVG", "MEMBER_MONTHS"
        ]),
        new("encounters.csv", "Encounters",
        [
            Id(), Time("START", true), Time("STOP"), Id("PATIENT"), Id("ORGANIZATION", false),
            Id("PROVIDER", false), Id("PAYER", false), Text("ENCOUNTERCLASS", 64),
            Text("CODE", 64), Text("DESCRIPTION", 1024), Money("BASE_ENCOUNTER_COST"),
            Money("TOTAL_CLAIM_COST"), Money("PAYER_COVERAGE"), Text("REASONCODE", 64),
            Text("REASONDESCRIPTION", 1024)
        ]),
        new("allergies.csv", "Allergies", Event([Date("START", true), Date("STOP")])),
        new("careplans.csv", "CarePlans",
        [
            Id(), .. Event([Date("START", true), Date("STOP")],
                [Text("REASONCODE", 64), Text("REASONDESCRIPTION", 1024)])
        ]),
        new("conditions.csv", "Conditions", Event([Date("START", true), Date("STOP")])),
        new("devices.csv", "Devices", Event([Time("START", true), Time("STOP")], [Text("UDI", 512)])),
        new("imaging_studies.csv", "ImagingStudies",
        [
            Id(), Time("DATE", true), Id("PATIENT"), Id("ENCOUNTER", false),
            Text("BODYSITE_CODE", 64), Text("BODYSITE_DESCRIPTION", 1024), Text("MODALITY_CODE", 64),
            Text("MODALITY_DESCRIPTION", 1024), Text("SOP_CODE", 128), Text("SOP_DESCRIPTION", 1024)
        ]),
        new("immunizations.csv", "Immunizations", Event([Time("DATE", true)], [Money("BASE_COST")])),
        new("medications.csv", "Medications",
        [
            Time("START", true), Time("STOP"), Id("PATIENT"), Id("PAYER", false), Id("ENCOUNTER", false),
            Text("CODE", 64), Text("DESCRIPTION", 1024), Money("BASE_COST"), Money("PAYER_COVERAGE"),
            Number("DISPENSES"), Money("TOTALCOST"), Text("REASONCODE", 64), Text("REASONDESCRIPTION", 1024)
        ]),
        new("payer_transitions.csv", "PayerTransitions",
        [
            Id("PATIENT"), Number("START_YEAR"), Number("END_YEAR"), Id("PAYER", false), Text("OWNERSHIP", 128)
        ]),
        new("procedures.csv", "Procedures", Event([Time("DATE", true)],
            [Money("BASE_COST"), Text("REASONCODE", 64), Text("REASONDESCRIPTION", 1024)])),
        new("supplies.csv", "Supplies", Event([Date("DATE", true)], [Number("QUANTITY")]))
    ];

    public static readonly IReadOnlyDictionary<string, string> Parents = new Dictionary<string, string>
    {
        ["PATIENT"] = "Patients", ["ENCOUNTER"] = "Encounters", ["ORGANIZATION"] = "Organizations",
        ["PROVIDER"] = "Providers", ["PAYER"] = "Payers"
    };
}
