# test_sql_managed_identity_manifest.py
"""
Functional regression tests for SQL managed-identity action manifests.
Version: 0.261.028
Implemented in: 0.261.028

Execute the real wizard serializer in Node and validate its output with the
server validator, without connecting to Azure or a database.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "application" / "single_app"
sys.path.insert(0, str(APP_ROOT))

from json_schema_validation import validate_plugin


NODE_PROBE = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const options = JSON.parse(process.argv[1]);
const values = {
    'sql-auth-type': options.authType,
    'sql-server': 'example.database.windows.net',
    'sql-database': 'synthetic_reporting',
    'sql-port': '',
    'sql-driver': 'ODBC Driver 18 for SQL Server',
    'sql-connection-string': 'Driver={ODBC Driver 18 for SQL Server};Server=example.database.windows.net;Database=synthetic_reporting;Authentication=ActiveDirectoryMsi;',
    'sql-username': 'readonly_test',
    'sql-password': 'test-only-password',
    'sql-client-id': 'test-client-id',
    'sql-client-secret': 'test-only-secret',
    'sql-tenant-id': 'test-tenant-id',
    'sql-read-only': 'true',
    'sql-max-rows': '50',
    'sql-timeout': '30',
    'sql-include-system-tables': 'false',
    'sql-table-filter': 'reporting_*',
    'plugin-metadata': '{}',
    'plugin-name': 'synthetic_sql',
    'plugin-display-name': 'Synthetic SQL',
    'plugin-description': 'Read synthetic reporting data'
};
const document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'plugin-modal') return null;
        return {
            value: values[id] || '',
            classList: { contains() { return id !== 'sql-config-section'; } }
        };
    },
    querySelector(selector) {
        if (selector.includes('sql-database-type')) return { value: options.databaseType };
        if (selector.includes('sql-plugin-type')) return { value: options.pluginType };
        if (selector.includes('sql-connection-method')) return { value: options.connectionMethod };
        throw new Error(`Unexpected selector: ${selector}`);
    }
};
const source = fs.readFileSync(process.argv[2], 'utf8')
    .replace(/^import .*;$/gm, '')
    .replace('export class PluginModalStepper', 'class PluginModalStepper');
const context = { document, console, window: {} };
vm.createContext(context);
vm.runInContext(source + '\nwindow.Stepper = PluginModalStepper;', context);
const stepper = Object.create(context.window.Stepper.prototype);
stepper.getSelectedActionIdentity = () => options.reusable
    ? { id: 'stored-identity-id', credentials: { auth_type: 'managed_identity' } }
    : null;
stepper.getIdentityAuthType = identity => identity?.credentials?.auth_type || '';
stepper.isStructuredConfigType = () => true;
stepper.applyKeyVaultReminderMetadata = metadata => metadata;
process.stdout.write(JSON.stringify(stepper.getFormData()));
"""


def build_manifest(plugin_type, auth_type="managed_identity",
                   database_type="azure_sql", connection_method="parameters",
                   reusable=False):
    """Run the actual browser serializer with a minimal DOM adapter."""
    options = {
        "pluginType": plugin_type,
        "authType": auth_type,
        "databaseType": database_type,
        "connectionMethod": connection_method,
        "reusable": reusable,
    }
    result = subprocess.run(
        [
            "node", "-e", NODE_PROBE, json.dumps(options),
            str(APP_ROOT / "static" / "js" / "plugin_modal_stepper.js"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("plugin_type", ["query", "schema"])
@pytest.mark.parametrize("database_type", ["azure_sql", "sqlserver"])
@pytest.mark.parametrize("connection_method", ["parameters", "connection_string"])
def test_system_assigned_identity_manifest(plugin_type, database_type, connection_method):
    manifest = build_manifest(
        plugin_type, database_type=database_type,
        connection_method=connection_method,
    )
    validation_error = validate_plugin(manifest)
    assert validation_error is None, validation_error
    assert manifest["auth"] == {"type": "identity", "identity": "managed_identity"}
    assert manifest["additionalFields"]["auth_type"] == "managed_identity"
    assert "identity_id" not in manifest
    assert "username" not in manifest["additionalFields"]
    assert "password" not in manifest["additionalFields"]
    if plugin_type == "query":
        assert manifest["additionalFields"]["read_only"] is True
        assert manifest["additionalFields"]["max_rows"] == 50


@pytest.mark.parametrize("plugin_type", ["query", "schema"])
@pytest.mark.parametrize("auth_type", ["username_password", "service_principal"])
def test_existing_credential_modes(plugin_type, auth_type):
    manifest = build_manifest(plugin_type, auth_type=auth_type)
    validation_error = validate_plugin(manifest)
    assert validation_error is None, validation_error
    if auth_type == "username_password":
        assert manifest["auth"] == {"type": "user"}
        assert manifest["additionalFields"]["username"] == "readonly_test"
    else:
        assert manifest["auth"]["type"] == "servicePrincipal"
        assert manifest["auth"]["identity"] == "test-client-id"


@pytest.mark.parametrize("plugin_type", ["query", "schema"])
def test_reusable_identity_reference_is_preserved(plugin_type):
    manifest = build_manifest(plugin_type, reusable=True)
    validation_error = validate_plugin(manifest)
    assert validation_error is None, validation_error
    assert manifest["identity_id"] == "stored-identity-id"
    assert manifest["auth"]["identity"] == "stored-identity-id"


def test_shared_identity_validation_remains_strict():
    manifest = build_manifest("query")
    manifest["auth"].pop("identity", None)
    validation_error = validate_plugin(manifest)
    assert validation_error is not None
    assert "'identity' is a required property" in validation_error
