"""Test-only persistent ledgers for offline Converse doubles."""
from pathlib import Path
import tempfile

from spray_schema.bedrock_budget import BedrockBudget


def make_test_budget(test_case, model_id, region):
    folder = tempfile.TemporaryDirectory(prefix="spray-bedrock-test-")
    test_case.addCleanup(folder.cleanup)
    return BedrockBudget(
        model_id=model_id,
        region=region,
        ledger_path=str(Path(folder.name) / "budget.json"),
        input_usd_per_million="1",
        output_usd_per_million="1",
        pricing_source="https://aws.amazon.com/bedrock/pricing/",
        ceiling_usd="1.00",
    )
