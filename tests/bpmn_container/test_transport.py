"""Test database admission never opens the fixture to arbitrary/shared databases."""

import unittest
from unittest.mock import patch

from tests.bpmn_container.transport import OWNED_POSTGRES_URL, require_isolation


class IsolationDatabaseTests(unittest.TestCase):
    def check(self, *, role='hub', database=OWNED_POSTGRES_URL, postgres='1', autopilot='1', docker=True):
        environment = {'ROLE': role, 'DATABASE_URL': database, 'BPMN_CONTAINER_ACCEPTANCE': '1',
                       'BPMN_POSTGRES_ENABLED': postgres, 'BPMN_AUTOPILOT_ENABLED': autopilot}
        with patch.dict('os.environ', environment, clear=True), patch('os.path.isfile', return_value=docker):
            require_isolation(role)

    def test_only_explicit_hub_can_use_exact_owned_postgres(self):
        self.check()
        for changed in ({'role': 'worker'}, {'postgres': '0'}, {'autopilot': '0'}, {'docker': False},
                        {'database': OWNED_POSTGRES_URL.replace('@postgres:', '@production:')},
                        {'database': OWNED_POSTGRES_URL + '?options=override'}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.check(**changed)

    def test_private_sqlite_remains_available_for_both_roles(self):
        for role in ('hub', 'worker'):
            self.check(role=role, database='sqlite:////tmp/runtime.sqlite', postgres='0', autopilot='0')
