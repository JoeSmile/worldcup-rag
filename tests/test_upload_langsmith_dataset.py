"""Upload LangSmith dataset script tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.upload_langsmith_dataset import _find_dataset_by_name


class UploadLangsmithDatasetTests(unittest.TestCase):
    def test_find_dataset_by_name(self) -> None:
        client = MagicMock()
        match = SimpleNamespace(name="worldcup-rag-studio", id="ds-1")
        other = SimpleNamespace(name="other", id="ds-2")
        client.list_datasets.return_value = [other, match]
        found = _find_dataset_by_name(client, "worldcup-rag-studio")
        self.assertEqual(found.id, "ds-1")

    @patch("langsmith.Client")
    def test_main_skips_duplicate_inputs(self, mock_client_cls) -> None:
        root = Path(__file__).resolve().parents[1]
        dataset_path = root / "benchmark" / "langsmith_dataset.json"
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        first = payload["examples"][0]

        client = MagicMock()
        mock_client_cls.return_value = client
        existing_ds = SimpleNamespace(
            id="ds-1",
            name=payload["dataset_name"],
        )
        client.list_datasets.return_value = [existing_ds]
        existing_ex = SimpleNamespace(inputs=first["inputs"])
        client.list_examples.return_value = [existing_ex]

        with patch("sys.argv", ["upload_langsmith_dataset.py", "--path", str(dataset_path)]):
            from scripts.upload_langsmith_dataset import main

            code = main()
        self.assertEqual(code, 0)
        create_calls = client.create_example.call_args_list
        self.assertEqual(len(create_calls), len(payload["examples"]) - 1)

    @patch("langsmith.Client")
    def test_main_replace_deletes_existing_dataset(self, mock_client_cls) -> None:
        root = Path(__file__).resolve().parents[1]
        dataset_path = root / "benchmark" / "langsmith_dataset.json"

        client = MagicMock()
        mock_client_cls.return_value = client
        existing_ds = SimpleNamespace(id="ds-old", name="worldcup-rag-studio")
        client.list_datasets.return_value = [existing_ds]
        new_ds = SimpleNamespace(id="ds-new", name="worldcup-rag-studio")
        client.create_dataset.return_value = new_ds
        client.list_examples.return_value = []

        with patch(
            "sys.argv",
            ["upload_langsmith_dataset.py", "--path", str(dataset_path), "--replace"],
        ):
            from scripts.upload_langsmith_dataset import main

            code = main()
        self.assertEqual(code, 0)
        client.delete_dataset.assert_called_once_with(dataset_id="ds-old")
        client.create_dataset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
