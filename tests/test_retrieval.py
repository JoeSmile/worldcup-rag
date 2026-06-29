import unittest
from unittest.mock import patch

from tools import semantic_search


class SemanticSearchSimilarityFilterTests(unittest.TestCase):
    @patch("tools.execute_query")
    @patch("tools.embed_query", return_value=[0.1, 0.2])
    def test_sql_filters_below_min_similarity(self, _embed, mock_execute):
        mock_execute.return_value = []
        semantic_search("梅西进球", limit=5)

        sql, params = mock_execute.call_args[0]
        self.assertIn("> %s", sql)
        self.assertEqual(params[3], 0.7)


if __name__ == "__main__":
    unittest.main()
