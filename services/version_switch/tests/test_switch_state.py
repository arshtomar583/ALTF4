import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from version_switch.api import app


class SwitchStateTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "PHOENIX_START_SLOT": "a",
                    "PHOENIX_SWITCH_STATE_PATH": str(Path(directory.name) / "switch-state.json"),
                },
            )
        )

    def test_switch_starts_on_last_persisted_slot_after_restart(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/state").json(), {"active_slot": "a"})
            self.assertEqual(client.put("/state", json={"active_slot": "b"}).status_code, 200)

        with TestClient(app) as restarted:
            self.assertEqual(restarted.get("/state").json(), {"active_slot": "b"})

    def test_failed_persistence_leaves_traffic_on_the_previous_slot(self):
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch("version_switch.api.save_state", side_effect=OSError("storage unavailable")):
                self.assertEqual(client.put("/state", json={"active_slot": "b"}).status_code, 500)
            self.assertEqual(client.get("/state").json(), {"active_slot": "a"})
