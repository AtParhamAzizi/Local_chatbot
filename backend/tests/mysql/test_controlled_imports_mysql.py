"""Repeat controlled-format validation, evidence and atomic replacement against disposable MySQL."""

import test_controlled_imports as import_scenarios
from test_index_migration_mysql import MySQLIndexDatabase


class ControlledImportMySQLTests(import_scenarios.ControlledImportTests):
    def make_database(self, directory):
        database = MySQLIndexDatabase()
        self.addCleanup(database.clear)
        return database
