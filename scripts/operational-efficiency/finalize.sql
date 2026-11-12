-- Run after all 15 CSVs are complete. All relationships are validated, not NOCHECK.
SET XACT_ABORT ON;
IF (SELECT COUNT(*) FROM oe.ImportFiles WHERE Completed=1) <> 15
    THROW 51000, 'All 15 CSV files must be loaded before finalization.', 1;
GO
IF EXISTS (
    SELECT Id FROM (
        SELECT DISTINCT [Id], [NAME], [ADDRESS], [CITY], [STATE], [ZIP], [LAT], [LON], [PHONE] FROM oe.[OrganizationsSource]
    ) identities
    GROUP BY Id HAVING COUNT_BIG(*)>1
) THROW 51001, 'Conflicting identity attributes in organizations.csv; source rows retained.', 1;
BEGIN TRANSACTION;
INSERT oe.[Organizations] ([SourceRow], [Id], [NAME], [ADDRESS], [CITY], [STATE], [ZIP], [LAT], [LON], [PHONE])
SELECT [SourceRow], [Id], [NAME], [ADDRESS], [CITY], [STATE], [ZIP], [LAT], [LON], [PHONE] FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY Id ORDER BY SourceRow) AS DuplicateRank
    FROM oe.[OrganizationsSource]
) ranked
WHERE DuplicateRank=1 AND NOT EXISTS (
    SELECT 1 FROM oe.[Organizations] existing WHERE existing.Id=ranked.Id
);
INSERT oe.[OrganizationStatistics] ([SourceRow], [Id], [REVENUE], [UTILIZATION])
SELECT [SourceRow], [Id], [REVENUE], [UTILIZATION] FROM oe.[OrganizationsSource] source
WHERE NOT EXISTS (SELECT 1 FROM oe.[OrganizationStatistics] existing WHERE existing.SourceRow=source.SourceRow);
COMMIT;
GO
IF EXISTS (
    SELECT Id FROM (
        SELECT DISTINCT [Id], [NAME], [ADDRESS], [CITY], [STATE_HEADQUARTERED], [ZIP], [PHONE] FROM oe.[PayersSource]
    ) identities
    GROUP BY Id HAVING COUNT_BIG(*)>1
) THROW 51001, 'Conflicting identity attributes in payers.csv; source rows retained.', 1;
BEGIN TRANSACTION;
INSERT oe.[Payers] ([SourceRow], [Id], [NAME], [ADDRESS], [CITY], [STATE_HEADQUARTERED], [ZIP], [PHONE])
SELECT [SourceRow], [Id], [NAME], [ADDRESS], [CITY], [STATE_HEADQUARTERED], [ZIP], [PHONE] FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY Id ORDER BY SourceRow) AS DuplicateRank
    FROM oe.[PayersSource]
) ranked
WHERE DuplicateRank=1 AND NOT EXISTS (
    SELECT 1 FROM oe.[Payers] existing WHERE existing.Id=ranked.Id
);
INSERT oe.[PayerStatistics] ([SourceRow], [Id], [AMOUNT_COVERED], [AMOUNT_UNCOVERED], [REVENUE], [COVERED_ENCOUNTERS], [UNCOVERED_ENCOUNTERS], [COVERED_MEDICATIONS], [UNCOVERED_MEDICATIONS], [COVERED_PROCEDURES], [UNCOVERED_PROCEDURES], [COVERED_IMMUNIZATIONS], [UNCOVERED_IMMUNIZATIONS], [UNIQUE_CUSTOMERS], [QOLS_AVG], [MEMBER_MONTHS])
SELECT [SourceRow], [Id], [AMOUNT_COVERED], [AMOUNT_UNCOVERED], [REVENUE], [COVERED_ENCOUNTERS], [UNCOVERED_ENCOUNTERS], [COVERED_MEDICATIONS], [UNCOVERED_MEDICATIONS], [COVERED_PROCEDURES], [UNCOVERED_PROCEDURES], [COVERED_IMMUNIZATIONS], [UNCOVERED_IMMUNIZATIONS], [UNIQUE_CUSTOMERS], [QOLS_AVG], [MEMBER_MONTHS] FROM oe.[PayersSource] source
WHERE NOT EXISTS (SELECT 1 FROM oe.[PayerStatistics] existing WHERE existing.SourceRow=source.SourceRow);
COMMIT;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Patients]') AND name=N'UX_Patients_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_Patients_Id] ON oe.[Patients] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Organizations]') AND name=N'UX_Organizations_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_Organizations_Id] ON oe.[Organizations] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Providers]') AND name=N'UX_Providers_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_Providers_Id] ON oe.[Providers] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Payers]') AND name=N'UX_Payers_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_Payers_Id] ON oe.[Payers] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'UX_Encounters_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_Encounters_Id] ON oe.[Encounters] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[CarePlans]') AND name=N'UX_CarePlans_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_CarePlans_Id] ON oe.[CarePlans] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[ImagingStudies]') AND name=N'UX_ImagingStudies_Id')
    CREATE UNIQUE NONCLUSTERED INDEX [UX_ImagingStudies_Id] ON oe.[ImagingStudies] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[OrganizationStatistics]') AND name=N'FK_OrganizationStatistics_Id_Organizations')
    ALTER TABLE oe.[OrganizationStatistics] WITH CHECK ADD CONSTRAINT [FK_OrganizationStatistics_Id_Organizations]
        FOREIGN KEY ([Id]) REFERENCES oe.[Organizations] ([Id]);
ALTER TABLE oe.[OrganizationStatistics] WITH CHECK CHECK CONSTRAINT [FK_OrganizationStatistics_Id_Organizations];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[OrganizationStatistics]') AND name=N'IX_OrganizationStatistics_Id')
    CREATE NONCLUSTERED INDEX [IX_OrganizationStatistics_Id] ON oe.[OrganizationStatistics] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Providers]') AND name=N'FK_Providers_ORGANIZATION_Organizations')
    ALTER TABLE oe.[Providers] WITH CHECK ADD CONSTRAINT [FK_Providers_ORGANIZATION_Organizations]
        FOREIGN KEY ([ORGANIZATION]) REFERENCES oe.[Organizations] ([Id]);
ALTER TABLE oe.[Providers] WITH CHECK CHECK CONSTRAINT [FK_Providers_ORGANIZATION_Organizations];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Providers]') AND name=N'IX_Providers_ORGANIZATION')
    CREATE NONCLUSTERED INDEX [IX_Providers_ORGANIZATION] ON oe.[Providers] ([ORGANIZATION]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[PayerStatistics]') AND name=N'FK_PayerStatistics_Id_Payers')
    ALTER TABLE oe.[PayerStatistics] WITH CHECK ADD CONSTRAINT [FK_PayerStatistics_Id_Payers]
        FOREIGN KEY ([Id]) REFERENCES oe.[Payers] ([Id]);
ALTER TABLE oe.[PayerStatistics] WITH CHECK CHECK CONSTRAINT [FK_PayerStatistics_Id_Payers];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[PayerStatistics]') AND name=N'IX_PayerStatistics_Id')
    CREATE NONCLUSTERED INDEX [IX_PayerStatistics_Id] ON oe.[PayerStatistics] ([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'FK_Encounters_PATIENT_Patients')
    ALTER TABLE oe.[Encounters] WITH CHECK ADD CONSTRAINT [FK_Encounters_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Encounters] WITH CHECK CHECK CONSTRAINT [FK_Encounters_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'IX_Encounters_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Encounters_PatientDate] ON oe.[Encounters] ([PATIENT], [START]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'FK_Encounters_ORGANIZATION_Organizations')
    ALTER TABLE oe.[Encounters] WITH CHECK ADD CONSTRAINT [FK_Encounters_ORGANIZATION_Organizations]
        FOREIGN KEY ([ORGANIZATION]) REFERENCES oe.[Organizations] ([Id]);
ALTER TABLE oe.[Encounters] WITH CHECK CHECK CONSTRAINT [FK_Encounters_ORGANIZATION_Organizations];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'IX_Encounters_ORGANIZATION')
    CREATE NONCLUSTERED INDEX [IX_Encounters_ORGANIZATION] ON oe.[Encounters] ([ORGANIZATION]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'FK_Encounters_PROVIDER_Providers')
    ALTER TABLE oe.[Encounters] WITH CHECK ADD CONSTRAINT [FK_Encounters_PROVIDER_Providers]
        FOREIGN KEY ([PROVIDER]) REFERENCES oe.[Providers] ([Id]);
ALTER TABLE oe.[Encounters] WITH CHECK CHECK CONSTRAINT [FK_Encounters_PROVIDER_Providers];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'IX_Encounters_PROVIDER')
    CREATE NONCLUSTERED INDEX [IX_Encounters_PROVIDER] ON oe.[Encounters] ([PROVIDER]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'FK_Encounters_PAYER_Payers')
    ALTER TABLE oe.[Encounters] WITH CHECK ADD CONSTRAINT [FK_Encounters_PAYER_Payers]
        FOREIGN KEY ([PAYER]) REFERENCES oe.[Payers] ([Id]);
ALTER TABLE oe.[Encounters] WITH CHECK CHECK CONSTRAINT [FK_Encounters_PAYER_Payers];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'IX_Encounters_PAYER')
    CREATE NONCLUSTERED INDEX [IX_Encounters_PAYER] ON oe.[Encounters] ([PAYER]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Allergies]') AND name=N'FK_Allergies_PATIENT_Patients')
    ALTER TABLE oe.[Allergies] WITH CHECK ADD CONSTRAINT [FK_Allergies_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Allergies] WITH CHECK CHECK CONSTRAINT [FK_Allergies_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Allergies]') AND name=N'IX_Allergies_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Allergies_PatientDate] ON oe.[Allergies] ([PATIENT], [START]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Allergies]') AND name=N'FK_Allergies_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Allergies] WITH CHECK ADD CONSTRAINT [FK_Allergies_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Allergies] WITH CHECK CHECK CONSTRAINT [FK_Allergies_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Allergies]') AND name=N'IX_Allergies_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Allergies_ENCOUNTER] ON oe.[Allergies] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Allergies] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Allergies.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[CarePlans]') AND name=N'FK_CarePlans_PATIENT_Patients')
    ALTER TABLE oe.[CarePlans] WITH CHECK ADD CONSTRAINT [FK_CarePlans_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[CarePlans] WITH CHECK CHECK CONSTRAINT [FK_CarePlans_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[CarePlans]') AND name=N'IX_CarePlans_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_CarePlans_PatientDate] ON oe.[CarePlans] ([PATIENT], [START]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[CarePlans]') AND name=N'FK_CarePlans_ENCOUNTER_Encounters')
    ALTER TABLE oe.[CarePlans] WITH CHECK ADD CONSTRAINT [FK_CarePlans_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[CarePlans] WITH CHECK CHECK CONSTRAINT [FK_CarePlans_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[CarePlans]') AND name=N'IX_CarePlans_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_CarePlans_ENCOUNTER] ON oe.[CarePlans] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[CarePlans] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in CarePlans.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Conditions]') AND name=N'FK_Conditions_PATIENT_Patients')
    ALTER TABLE oe.[Conditions] WITH CHECK ADD CONSTRAINT [FK_Conditions_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Conditions] WITH CHECK CHECK CONSTRAINT [FK_Conditions_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Conditions]') AND name=N'IX_Conditions_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Conditions_PatientDate] ON oe.[Conditions] ([PATIENT], [START]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Conditions]') AND name=N'FK_Conditions_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Conditions] WITH CHECK ADD CONSTRAINT [FK_Conditions_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Conditions] WITH CHECK CHECK CONSTRAINT [FK_Conditions_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Conditions]') AND name=N'IX_Conditions_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Conditions_ENCOUNTER] ON oe.[Conditions] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Conditions] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Conditions.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Devices]') AND name=N'FK_Devices_PATIENT_Patients')
    ALTER TABLE oe.[Devices] WITH CHECK ADD CONSTRAINT [FK_Devices_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Devices] WITH CHECK CHECK CONSTRAINT [FK_Devices_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Devices]') AND name=N'IX_Devices_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Devices_PatientDate] ON oe.[Devices] ([PATIENT], [START]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Devices]') AND name=N'FK_Devices_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Devices] WITH CHECK ADD CONSTRAINT [FK_Devices_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Devices] WITH CHECK CHECK CONSTRAINT [FK_Devices_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Devices]') AND name=N'IX_Devices_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Devices_ENCOUNTER] ON oe.[Devices] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Devices] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Devices.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[ImagingStudies]') AND name=N'FK_ImagingStudies_PATIENT_Patients')
    ALTER TABLE oe.[ImagingStudies] WITH CHECK ADD CONSTRAINT [FK_ImagingStudies_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[ImagingStudies] WITH CHECK CHECK CONSTRAINT [FK_ImagingStudies_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[ImagingStudies]') AND name=N'IX_ImagingStudies_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_ImagingStudies_PatientDate] ON oe.[ImagingStudies] ([PATIENT], [DATE]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[ImagingStudies]') AND name=N'FK_ImagingStudies_ENCOUNTER_Encounters')
    ALTER TABLE oe.[ImagingStudies] WITH CHECK ADD CONSTRAINT [FK_ImagingStudies_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[ImagingStudies] WITH CHECK CHECK CONSTRAINT [FK_ImagingStudies_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[ImagingStudies]') AND name=N'IX_ImagingStudies_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_ImagingStudies_ENCOUNTER] ON oe.[ImagingStudies] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[ImagingStudies] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in ImagingStudies.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Immunizations]') AND name=N'FK_Immunizations_PATIENT_Patients')
    ALTER TABLE oe.[Immunizations] WITH CHECK ADD CONSTRAINT [FK_Immunizations_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Immunizations] WITH CHECK CHECK CONSTRAINT [FK_Immunizations_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Immunizations]') AND name=N'IX_Immunizations_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Immunizations_PatientDate] ON oe.[Immunizations] ([PATIENT], [DATE]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Immunizations]') AND name=N'FK_Immunizations_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Immunizations] WITH CHECK ADD CONSTRAINT [FK_Immunizations_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Immunizations] WITH CHECK CHECK CONSTRAINT [FK_Immunizations_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Immunizations]') AND name=N'IX_Immunizations_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Immunizations_ENCOUNTER] ON oe.[Immunizations] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Immunizations] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Immunizations.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Medications]') AND name=N'FK_Medications_PATIENT_Patients')
    ALTER TABLE oe.[Medications] WITH CHECK ADD CONSTRAINT [FK_Medications_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Medications] WITH CHECK CHECK CONSTRAINT [FK_Medications_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Medications]') AND name=N'IX_Medications_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Medications_PatientDate] ON oe.[Medications] ([PATIENT], [START]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Medications]') AND name=N'FK_Medications_PAYER_Payers')
    ALTER TABLE oe.[Medications] WITH CHECK ADD CONSTRAINT [FK_Medications_PAYER_Payers]
        FOREIGN KEY ([PAYER]) REFERENCES oe.[Payers] ([Id]);
ALTER TABLE oe.[Medications] WITH CHECK CHECK CONSTRAINT [FK_Medications_PAYER_Payers];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Medications]') AND name=N'IX_Medications_PAYER')
    CREATE NONCLUSTERED INDEX [IX_Medications_PAYER] ON oe.[Medications] ([PAYER]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Medications]') AND name=N'FK_Medications_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Medications] WITH CHECK ADD CONSTRAINT [FK_Medications_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Medications] WITH CHECK CHECK CONSTRAINT [FK_Medications_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Medications]') AND name=N'IX_Medications_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Medications_ENCOUNTER] ON oe.[Medications] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Medications] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Medications.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[PayerTransitions]') AND name=N'FK_PayerTransitions_PATIENT_Patients')
    ALTER TABLE oe.[PayerTransitions] WITH CHECK ADD CONSTRAINT [FK_PayerTransitions_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[PayerTransitions] WITH CHECK CHECK CONSTRAINT [FK_PayerTransitions_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[PayerTransitions]') AND name=N'IX_PayerTransitions_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_PayerTransitions_PatientDate] ON oe.[PayerTransitions] ([PATIENT], [START_YEAR]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[PayerTransitions]') AND name=N'FK_PayerTransitions_PAYER_Payers')
    ALTER TABLE oe.[PayerTransitions] WITH CHECK ADD CONSTRAINT [FK_PayerTransitions_PAYER_Payers]
        FOREIGN KEY ([PAYER]) REFERENCES oe.[Payers] ([Id]);
ALTER TABLE oe.[PayerTransitions] WITH CHECK CHECK CONSTRAINT [FK_PayerTransitions_PAYER_Payers];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[PayerTransitions]') AND name=N'IX_PayerTransitions_PAYER')
    CREATE NONCLUSTERED INDEX [IX_PayerTransitions_PAYER] ON oe.[PayerTransitions] ([PAYER]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Procedures]') AND name=N'FK_Procedures_PATIENT_Patients')
    ALTER TABLE oe.[Procedures] WITH CHECK ADD CONSTRAINT [FK_Procedures_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Procedures] WITH CHECK CHECK CONSTRAINT [FK_Procedures_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Procedures]') AND name=N'IX_Procedures_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Procedures_PatientDate] ON oe.[Procedures] ([PATIENT], [DATE]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Procedures]') AND name=N'FK_Procedures_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Procedures] WITH CHECK ADD CONSTRAINT [FK_Procedures_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Procedures] WITH CHECK CHECK CONSTRAINT [FK_Procedures_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Procedures]') AND name=N'IX_Procedures_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Procedures_ENCOUNTER] ON oe.[Procedures] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Procedures] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Procedures.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Supplies]') AND name=N'FK_Supplies_PATIENT_Patients')
    ALTER TABLE oe.[Supplies] WITH CHECK ADD CONSTRAINT [FK_Supplies_PATIENT_Patients]
        FOREIGN KEY ([PATIENT]) REFERENCES oe.[Patients] ([Id]);
ALTER TABLE oe.[Supplies] WITH CHECK CHECK CONSTRAINT [FK_Supplies_PATIENT_Patients];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Supplies]') AND name=N'IX_Supplies_PatientDate')
    CREATE NONCLUSTERED INDEX [IX_Supplies_PatientDate] ON oe.[Supplies] ([PATIENT], [DATE]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=OBJECT_ID(N'oe.[Supplies]') AND name=N'FK_Supplies_ENCOUNTER_Encounters')
    ALTER TABLE oe.[Supplies] WITH CHECK ADD CONSTRAINT [FK_Supplies_ENCOUNTER_Encounters]
        FOREIGN KEY ([ENCOUNTER]) REFERENCES oe.[Encounters] ([Id]);
ALTER TABLE oe.[Supplies] WITH CHECK CHECK CONSTRAINT [FK_Supplies_ENCOUNTER_Encounters];
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Supplies]') AND name=N'IX_Supplies_ENCOUNTER')
    CREATE NONCLUSTERED INDEX [IX_Supplies_ENCOUNTER] ON oe.[Supplies] ([ENCOUNTER]);
GO
IF EXISTS (
    SELECT 1 FROM oe.[Supplies] child
    JOIN oe.Encounters encounter ON encounter.Id=child.ENCOUNTER
    WHERE child.PATIENT<>encounter.PATIENT
) THROW 51002, 'Encounter/patient mismatch in Supplies.', 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'oe.[Encounters]') AND name=N'IX_Encounters_StartClass')
    CREATE NONCLUSTERED INDEX [IX_Encounters_StartClass] ON oe.[Encounters] ([START], [ENCOUNTERCLASS]);
GO
UPDATE STATISTICS oe.[Patients] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Organizations] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[OrganizationStatistics] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Providers] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Payers] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[PayerStatistics] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Encounters] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Allergies] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[CarePlans] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Conditions] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Devices] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[ImagingStudies] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Immunizations] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Medications] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[PayerTransitions] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Procedures] WITH RESAMPLE;
GO
UPDATE STATISTICS oe.[Supplies] WITH RESAMPLE;
GO
