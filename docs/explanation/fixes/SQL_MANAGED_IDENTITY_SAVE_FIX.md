# SQL Managed Identity Action Save Fix

Fixed in version: **0.261.028**

## Issue and root cause

A SQL action using **Managed Identity (Azure)** could pass **Test Connection**
but fail wizard validation with `'identity' is a required property`.

The connection test sends SQL connection parameters separately. The save
serializer instead builds an action manifest with `auth.type: "identity"` but
omitted `auth.identity`, which the shared action schema requires.

## Changes and impact

- `application/single_app/static/js/plugin_modal_stepper.js` now supplies the
  existing `managed_identity` marker for direct managed-identity authentication.
  This is an internal marker, not a user-assigned client ID or a workspace
  identity reference.
- Both SQL Query and SQL Schema share the corrected serializer.
- SQL username/password, service-principal, and reusable-identity serialization
  are unchanged. Shared schema requirements are not relaxed.
- `application/single_app/config.py` advances the application version from
  `0.261.027` to `0.261.028`.

No database grants or authentication runtime changes are needed for this fix.
Deploy the updated app, restart the affected slot, refresh the browser to load
the updated static asset, and retry saving the action.

## Validation

Before the fix, eight functional cases reproduced the exact missing-property
error across both SQL action types, SQL Server/Azure SQL, and both connection
methods. Seven compatibility and strict-validation cases already passed.

- `functional_tests/test_sql_managed_identity_manifest.py` executes the real
  JavaScript serializer in Node and passes its manifests through the server
  validator. It covers managed identity, credential modes, reusable identity
  references, and retention of strict shared-schema validation.
- `ui_tests/test_sql_managed_identity_save.py` exercises SQL form input, the real
  serializer, and server validation in a browser fixture. It also checks that
  invalid names still produce a visible validation failure.

These are offline regression tests. They do not prove Azure database
connectivity or deployment to a particular App Service slot.

The focused regression and compatibility run passed **27 tests**, including
both browser cases. JavaScript syntax, whitespace, and documentation site
quality checks also passed. Documentation surface coverage passed six of seven
checks; the inventory-sync check found an unrelated existing upload-label
change for XSD files. This fix does not change application surface inventory.
