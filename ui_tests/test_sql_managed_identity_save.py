# test_sql_managed_identity_save.py
"""
Browser regression for SQL managed-identity save validation.
Version: 0.261.028
Implemented in: 0.261.028

Exercise the real wizard serializer and server manifest validator using a local
browser fixture. No database connection, Azure resource, or credentials required.
"""

import json
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "application" / "single_app"
sys.path.insert(0, str(APP_ROOT))

from json_schema_validation import validate_plugin


@pytest.mark.ui
@pytest.mark.parametrize("plugin_type", ["query", "schema"])
def test_managed_identity_save_validation(plugin_type):
    """Submitting managed-identity SQL settings must pass server validation."""
    source = (APP_ROOT / "static" / "js" / "plugin_modal_stepper.js").read_text(
        encoding="utf-8"
    )
    source = "\n".join(
        line for line in source.splitlines() if not line.startswith("import ")
    ).replace("export class PluginModalStepper", "class PluginModalStepper")
    sections = [
        "openapi", "cosmos", "rocksdb", "document-search", "blob-storage",
        "databricks", "snowflake", "tableau", "yamcs", "mcp", "azure-maps",
        "log-analytics",
    ]
    hidden_sections = "".join(
        f'<section id="{name}-config-section" class="d-none"></section>'
        for name in sections
    )
    submissions = []

    def validate_request(route):
        manifest = route.request.post_data_json
        submissions.append(manifest)
        validation_error = validate_plugin(manifest)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"error": validation_error}),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("https://simplechat.test/api/plugins/validate", validate_request)
            page.set_content(
                f"""
                <!doctype html>
                <html lang="en"><head>
                <style>.d-none {{ display: none !important; }}</style>
                </head><body>
                {hidden_sections}
                <section id="sql-config-section">
                    <input type="radio" name="sql-database-type" value="azure_sql" checked>
                    <input type="radio" name="sql-plugin-type" value="{plugin_type}" checked>
                    <input type="radio" name="sql-connection-method" value="parameters" checked>
                    <label for="sql-auth-type">Authentication</label>
                    <select id="sql-auth-type">
                        <option value="managed_identity">Managed Identity (Azure)</option>
                    </select>
                    <label for="sql-server">Server</label>
                    <input id="sql-server">
                    <label for="sql-database">Database</label>
                    <input id="sql-database">
                    <input id="sql-port" value="">
                    <input id="sql-driver" value="ODBC Driver 18 for SQL Server">
                    <input id="sql-read-only" value="true">
                    <input id="sql-max-rows" value="50">
                    <input id="sql-timeout" value="30">
                    <input id="sql-include-system-tables" value="false">
                    <input id="sql-table-filter" value="">
                </section>
                <input id="plugin-name" value="synthetic_sql">
                <input id="plugin-display-name" value="Synthetic SQL">
                <input id="plugin-description" value="Read synthetic data">
                <textarea id="plugin-metadata">{{}}</textarea>
                <button id="save-action" type="button">Validate action</button>
                <p id="save-result" role="status"></p>
                </body></html>
                """
            )
            page.add_script_tag(content=source)
            page.evaluate(
                """() => {
                    const stepper = Object.create(PluginModalStepper.prototype);
                    stepper.getSelectedActionIdentity = () => null;
                    stepper.isStructuredConfigType = () => true;
                    stepper.applyKeyVaultReminderMetadata = metadata => metadata;
                    document.getElementById('save-action').addEventListener('click', async () => {
                        const response = await fetch('https://simplechat.test/api/plugins/validate', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(stepper.getFormData())
                        });
                        const result = await response.json();
                        const status = document.getElementById('save-result');
                        status.className = result.error ? 'alert alert-danger' : 'alert alert-success';
                        status.textContent = result.error || 'Action configuration is valid';
                    });
                }"""
            )
            page.get_by_label("Server", exact=True).fill("example.database.windows.net")
            page.get_by_label("Database", exact=True).fill("synthetic_reporting")
            page.get_by_label("Authentication", exact=True).select_option("managed_identity")
            page.get_by_role("button", name="Validate action").click()
            expect(page.get_by_role("status")).to_have_text("Action configuration is valid")
            assert submissions[0]["type"] == f"sql_{plugin_type}"
            assert submissions[0]["auth"] == {
                "type": "identity", "identity": "managed_identity"
            }

            page.locator("#plugin-name").fill("invalid action name!")
            page.get_by_role("button", name="Validate action").click()
            expect(page.get_by_role("status")).to_have_class("alert alert-danger")
            expect(page.get_by_role("status")).not_to_have_text("Action configuration is valid")
        finally:
            browser.close()
