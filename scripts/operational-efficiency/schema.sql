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
IF OBJECT_ID(N'oe.[Patients]', N'U') IS NULL
CREATE TABLE oe.[Patients] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Patients] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [BIRTHDATE] date NOT NULL,
    [DEATHDATE] date NULL,
    [SSN] nvarchar(32) NULL,
    [DRIVERS] nvarchar(64) NULL,
    [PASSPORT] nvarchar(64) NULL,
    [PREFIX] nvarchar(32) NULL,
    [FIRST] nvarchar(128) NULL,
    [LAST] nvarchar(128) NULL,
    [SUFFIX] nvarchar(32) NULL,
    [MAIDEN] nvarchar(128) NULL,
    [MARITAL] nvarchar(16) NULL,
    [RACE] nvarchar(128) NULL,
    [ETHNICITY] nvarchar(128) NULL,
    [GENDER] nvarchar(32) NULL,
    [BIRTHPLACE] nvarchar(256) NULL,
    [ADDRESS] nvarchar(512) NULL,
    [CITY] nvarchar(128) NULL,
    [STATE] nvarchar(128) NULL,
    [COUNTY] nvarchar(128) NULL,
    [ZIP] nvarchar(32) NULL,
    [LAT] float(53) NULL,
    [LON] float(53) NULL,
    [HEALTHCARE_EXPENSES] decimal(28,8) NULL,
    [HEALTHCARE_COVERAGE] decimal(28,8) NULL
);
GO
IF OBJECT_ID(N'oe.[OrganizationsSource]', N'U') IS NULL
CREATE TABLE oe.[OrganizationsSource] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_OrganizationsSource] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [NAME] nvarchar(256) NULL,
    [ADDRESS] nvarchar(512) NULL,
    [CITY] nvarchar(128) NULL,
    [STATE] nvarchar(128) NULL,
    [ZIP] nvarchar(32) NULL,
    [LAT] float(53) NULL,
    [LON] float(53) NULL,
    [PHONE] nvarchar(128) NULL,
    [REVENUE] decimal(28,8) NULL,
    [UTILIZATION] bigint NULL
);
GO
IF OBJECT_ID(N'oe.[Organizations]', N'U') IS NULL
CREATE TABLE oe.[Organizations] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Organizations] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [NAME] nvarchar(256) NULL,
    [ADDRESS] nvarchar(512) NULL,
    [CITY] nvarchar(128) NULL,
    [STATE] nvarchar(128) NULL,
    [ZIP] nvarchar(32) NULL,
    [LAT] float(53) NULL,
    [LON] float(53) NULL,
    [PHONE] nvarchar(128) NULL
);
GO
IF OBJECT_ID(N'oe.[OrganizationStatistics]', N'U') IS NULL
CREATE TABLE oe.[OrganizationStatistics] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_OrganizationStatistics] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [REVENUE] decimal(28,8) NULL,
    [UTILIZATION] bigint NULL
);
GO
IF OBJECT_ID(N'oe.[Providers]', N'U') IS NULL
CREATE TABLE oe.[Providers] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Providers] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [ORGANIZATION] uniqueidentifier NOT NULL,
    [NAME] nvarchar(256) NULL,
    [GENDER] nvarchar(32) NULL,
    [SPECIALITY] nvarchar(128) NULL,
    [ADDRESS] nvarchar(512) NULL,
    [CITY] nvarchar(128) NULL,
    [STATE] nvarchar(128) NULL,
    [ZIP] nvarchar(32) NULL,
    [LAT] float(53) NULL,
    [LON] float(53) NULL,
    [UTILIZATION] bigint NULL
);
GO
IF OBJECT_ID(N'oe.[PayersSource]', N'U') IS NULL
CREATE TABLE oe.[PayersSource] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_PayersSource] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [NAME] nvarchar(256) NULL,
    [ADDRESS] nvarchar(512) NULL,
    [CITY] nvarchar(128) NULL,
    [STATE_HEADQUARTERED] nvarchar(128) NULL,
    [ZIP] nvarchar(32) NULL,
    [PHONE] nvarchar(128) NULL,
    [AMOUNT_COVERED] decimal(28,8) NULL,
    [AMOUNT_UNCOVERED] decimal(28,8) NULL,
    [REVENUE] decimal(28,8) NULL,
    [COVERED_ENCOUNTERS] bigint NULL,
    [UNCOVERED_ENCOUNTERS] bigint NULL,
    [COVERED_MEDICATIONS] bigint NULL,
    [UNCOVERED_MEDICATIONS] bigint NULL,
    [COVERED_PROCEDURES] bigint NULL,
    [UNCOVERED_PROCEDURES] bigint NULL,
    [COVERED_IMMUNIZATIONS] bigint NULL,
    [UNCOVERED_IMMUNIZATIONS] bigint NULL,
    [UNIQUE_CUSTOMERS] bigint NULL,
    [QOLS_AVG] float(53) NULL,
    [MEMBER_MONTHS] bigint NULL
);
GO
IF OBJECT_ID(N'oe.[Payers]', N'U') IS NULL
CREATE TABLE oe.[Payers] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Payers] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [NAME] nvarchar(256) NULL,
    [ADDRESS] nvarchar(512) NULL,
    [CITY] nvarchar(128) NULL,
    [STATE_HEADQUARTERED] nvarchar(128) NULL,
    [ZIP] nvarchar(32) NULL,
    [PHONE] nvarchar(128) NULL
);
GO
IF OBJECT_ID(N'oe.[PayerStatistics]', N'U') IS NULL
CREATE TABLE oe.[PayerStatistics] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_PayerStatistics] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [AMOUNT_COVERED] decimal(28,8) NULL,
    [AMOUNT_UNCOVERED] decimal(28,8) NULL,
    [REVENUE] decimal(28,8) NULL,
    [COVERED_ENCOUNTERS] bigint NULL,
    [UNCOVERED_ENCOUNTERS] bigint NULL,
    [COVERED_MEDICATIONS] bigint NULL,
    [UNCOVERED_MEDICATIONS] bigint NULL,
    [COVERED_PROCEDURES] bigint NULL,
    [UNCOVERED_PROCEDURES] bigint NULL,
    [COVERED_IMMUNIZATIONS] bigint NULL,
    [UNCOVERED_IMMUNIZATIONS] bigint NULL,
    [UNIQUE_CUSTOMERS] bigint NULL,
    [QOLS_AVG] float(53) NULL,
    [MEMBER_MONTHS] bigint NULL
);
GO
IF OBJECT_ID(N'oe.[Encounters]', N'U') IS NULL
CREATE TABLE oe.[Encounters] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Encounters] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [START] datetime2(0) NOT NULL,
    [STOP] datetime2(0) NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ORGANIZATION] uniqueidentifier NULL,
    [PROVIDER] uniqueidentifier NULL,
    [PAYER] uniqueidentifier NULL,
    [ENCOUNTERCLASS] nvarchar(64) NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [BASE_ENCOUNTER_COST] decimal(28,8) NULL,
    [TOTAL_CLAIM_COST] decimal(28,8) NULL,
    [PAYER_COVERAGE] decimal(28,8) NULL,
    [REASONCODE] nvarchar(64) NULL,
    [REASONDESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[Allergies]', N'U') IS NULL
CREATE TABLE oe.[Allergies] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Allergies] PRIMARY KEY CLUSTERED,
    [START] date NOT NULL,
    [STOP] date NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[CarePlans]', N'U') IS NULL
CREATE TABLE oe.[CarePlans] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_CarePlans] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [START] date NOT NULL,
    [STOP] date NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [REASONCODE] nvarchar(64) NULL,
    [REASONDESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[Conditions]', N'U') IS NULL
CREATE TABLE oe.[Conditions] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Conditions] PRIMARY KEY CLUSTERED,
    [START] date NOT NULL,
    [STOP] date NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[Devices]', N'U') IS NULL
CREATE TABLE oe.[Devices] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Devices] PRIMARY KEY CLUSTERED,
    [START] datetime2(0) NOT NULL,
    [STOP] datetime2(0) NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [UDI] nvarchar(512) NULL
);
GO
IF OBJECT_ID(N'oe.[ImagingStudies]', N'U') IS NULL
CREATE TABLE oe.[ImagingStudies] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_ImagingStudies] PRIMARY KEY CLUSTERED,
    [Id] uniqueidentifier NOT NULL,
    [DATE] datetime2(0) NOT NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [BODYSITE_CODE] nvarchar(64) NULL,
    [BODYSITE_DESCRIPTION] nvarchar(1024) NULL,
    [MODALITY_CODE] nvarchar(64) NULL,
    [MODALITY_DESCRIPTION] nvarchar(1024) NULL,
    [SOP_CODE] nvarchar(128) NULL,
    [SOP_DESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[Immunizations]', N'U') IS NULL
CREATE TABLE oe.[Immunizations] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Immunizations] PRIMARY KEY CLUSTERED,
    [DATE] datetime2(0) NOT NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [BASE_COST] decimal(28,8) NULL
);
GO
IF OBJECT_ID(N'oe.[Medications]', N'U') IS NULL
CREATE TABLE oe.[Medications] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Medications] PRIMARY KEY CLUSTERED,
    [START] datetime2(0) NOT NULL,
    [STOP] datetime2(0) NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [PAYER] uniqueidentifier NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [BASE_COST] decimal(28,8) NULL,
    [PAYER_COVERAGE] decimal(28,8) NULL,
    [DISPENSES] bigint NULL,
    [TOTALCOST] decimal(28,8) NULL,
    [REASONCODE] nvarchar(64) NULL,
    [REASONDESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[PayerTransitions]', N'U') IS NULL
CREATE TABLE oe.[PayerTransitions] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_PayerTransitions] PRIMARY KEY CLUSTERED,
    [PATIENT] uniqueidentifier NOT NULL,
    [START_YEAR] bigint NULL,
    [END_YEAR] bigint NULL,
    [PAYER] uniqueidentifier NULL,
    [OWNERSHIP] nvarchar(128) NULL
);
GO
IF OBJECT_ID(N'oe.[Procedures]', N'U') IS NULL
CREATE TABLE oe.[Procedures] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Procedures] PRIMARY KEY CLUSTERED,
    [DATE] datetime2(0) NOT NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [BASE_COST] decimal(28,8) NULL,
    [REASONCODE] nvarchar(64) NULL,
    [REASONDESCRIPTION] nvarchar(1024) NULL
);
GO
IF OBJECT_ID(N'oe.[Supplies]', N'U') IS NULL
CREATE TABLE oe.[Supplies] (
    [SourceRow] bigint NOT NULL CONSTRAINT [PK_Supplies] PRIMARY KEY CLUSTERED,
    [DATE] date NOT NULL,
    [PATIENT] uniqueidentifier NOT NULL,
    [ENCOUNTER] uniqueidentifier NULL,
    [CODE] nvarchar(64) NULL,
    [DESCRIPTION] nvarchar(1024) NULL,
    [QUANTITY] bigint NULL
);
GO
