import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "taskconsole" / "static"
NODE = os.environ.get("NODE") or shutil.which("node")


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise RuntimeError("Node.js is required for frontend contract tests; set NODE or add node to PATH")

    def _node(self, source: str) -> str:
        return subprocess.run(
            [NODE, "--input-type=module", "-e", source],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def test_english_and_chinese_catalogs_have_identical_keys(self):
        result = self._node(
            "import('./taskconsole/static/i18n.js').then(m => {"
            "const flatten=(o,p='')=>Object.entries(o).flatMap(([k,v])=>"
            "v&&typeof v==='object'?flatten(v,p+k+'.'):[p+k]);"
            "console.log(JSON.stringify(Object.fromEntries(Object.entries(m.catalogs)"
            ".map(([locale,value])=>[locale,flatten(value).sort()]))))})"
        )
        catalogs = json.loads(result)
        self.assertEqual(catalogs["en"], catalogs["zh-CN"])
        self.assertGreater(len(catalogs["en"]), 100)

    def test_locale_is_english_by_default_and_only_explicit_chinese_is_accepted(self):
        result = self._node(
            "import('./taskconsole/static/i18n.js').then(m => console.log(JSON.stringify(["
            "m.normalizeLocale(),m.normalizeLocale('fr'),m.normalizeLocale('zh-CN')"
            "])))"
        )
        self.assertEqual(json.loads(result), ["en", "en", "zh-CN"])

    def test_translation_never_translates_unknown_or_user_content(self):
        result = self._node(
            "import('./taskconsole/static/i18n.js').then(m => console.log(JSON.stringify(["
            "m.translate('en','nav.tasks'),m.translate('zh-CN','nav.tasks'),"
            "m.translate('zh-CN','CUSTOMER_JOB_42')"
            "])))"
        )
        self.assertEqual(json.loads(result), ["Tasks", "任务", "CUSTOMER_JOB_42"])

    def test_every_literal_translation_reference_exists(self):
        source = (STATIC / "app.js").read_text()
        referenced = set(re.findall(r"\bt\(['\"]([^'\"]+)['\"]\)", source))
        result = self._node(
            "import('./taskconsole/static/i18n.js').then(m => {"
            "const flatten=(o,p='')=>Object.entries(o).flatMap(([k,v])=>"
            "v&&typeof v==='object'?flatten(v,p+k+'.'):[p+k]);"
            "console.log(JSON.stringify(flatten(m.catalogs.en)))})"
        )
        missing = referenced - set(json.loads(result))
        self.assertEqual(missing, set())

    def test_shell_has_accessible_landmarks_and_module_entrypoint(self):
        html = (STATIC / "index.html").read_text()
        self.assertIn('id="app"', html)
        self.assertIn('type="module"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('meta name="viewport"', html)

    def test_browser_entrypoint_parses_as_an_es_module(self):
        result = subprocess.run(
            [NODE, "--input-type=module", "--check"],
            input=(STATIC / "app.js").read_text(),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_api_client_uses_same_origin_credentials_and_csrf(self):
        source = (STATIC / "api.js").read_text()
        self.assertIn("credentials: 'same-origin'", source)
        self.assertIn("X-CSRF-Token", source)
        self.assertNotIn("innerHTML", (STATIC / "app.js").read_text())

    def test_frontend_adapts_manifest_and_timestamp_shapes_from_api(self):
        result = self._node(
            "import('./taskconsole/static/model.js').then(m => console.log(JSON.stringify({"
            "rows:m.manifestRows({parameters:[{key:'who',default:'world'}]}),"
            "payload:m.manifestPayload([{key:'who',default:'world'}]),"
            "last:m.timestampOf({created_at:'2026-09-17T12:00:00Z'}),"
            "plain:m.timestampOf('2026-09-17T13:00:00Z'),"
            "params:m.parameterRows({parameters:[{key:'who',default:'world',required:true}]},{extra:'kept'})"
            "})))"
        )
        value = json.loads(result)
        self.assertEqual(value["rows"][0]["key"], "who")
        self.assertEqual(value["payload"], {"parameters": [{"key": "who", "default": "world"}]})
        self.assertEqual(value["last"], "2026-09-17T12:00:00Z")
        self.assertEqual(value["plain"], "2026-09-17T13:00:00Z")
        self.assertEqual(value["params"], [
            {"key": "who", "value": "world", "required": True},
            {"key": "extra", "value": "kept", "required": False},
        ])

    def test_run_and_parameter_helpers_cover_live_states_and_invalid_rows(self):
        result = self._node(
            "import('./taskconsole/static/model.js').then(m => {"
            "let duplicate='';let incomplete='';"
            "try{m.paramsFromRows([{key:'x',value:'1'},{key:'x',value:'2'}])}catch(e){duplicate=e.message}"
            "try{m.paramsFromRows([{key:'',value:'orphan'}])}catch(e){incomplete=e.message}"
            "console.log(JSON.stringify({"
            "live:['queued','running','cancelling','succeeded'].map(m.executionIsLive),"
            "cancel:['queued','running','cancelling','failed'].map(m.canCancelExecution),"
            "end:m.endOfDay('2026-09-17'),duplicate,incomplete"
            "}))})"
        )
        value = json.loads(result)
        self.assertEqual(value["live"], [True, True, True, False])
        self.assertEqual(value["cancel"], [True, True, True, False])
        self.assertEqual(value["end"], "2026-09-17T23:59:59.999Z")
        self.assertTrue(value["duplicate"])
        self.assertTrue(value["incomplete"])


if __name__ == "__main__":
    unittest.main()
