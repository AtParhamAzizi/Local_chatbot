"""Real SQL publication/replacement/deletion and lexical snapshot coherence."""

import test_hybrid_retrieval as scenarios
from test_index_migration_mysql import MySQLIndexDatabase


class HybridRetrievalMySQLTests(scenarios.HybridRetrievalTests):
    def make_database(self, directory):
        database = MySQLIndexDatabase()
        self.addCleanup(database.clear)
        return database
