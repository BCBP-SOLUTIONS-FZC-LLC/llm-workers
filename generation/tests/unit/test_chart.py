"""The Helm chart gives a pod everything ``Settings`` requires, and no pointless disruption budget."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parents[3] / "deploy" / "helm" / "llm-workers"
BASE = ["--set", "temporal.address=t:7233", "--set", "temporal.namespace=llm-generation-dev"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def render(*extra: str) -> str:
    cmd = ["helm", "template", "x", str(CHART), *BASE, "--set", "image.tag=t", *extra]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_pod_gets_the_codec_key_settings_requires_outside_dev() -> None:
    doc = render("--set", "codecKey.keyId=k1")
    assert '{ name: PAYLOAD_CODEC_KEY_ID, value: "k1" }' in doc
    assert '{ name: PAYLOAD_CODEC_KEY_PATH, value: "/var/run/codec/k1" }' in doc
    assert "mountPath: /var/run/codec" in doc and "secretName: llm-workers-codec-key" in doc


def test_the_chart_refuses_to_render_without_a_codec_key_id() -> None:
    out = subprocess.run(  # noqa: S603
        ["helm", "template", "x", str(CHART), *BASE, "--set", "image.tag=t"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode != 0 and "codecKey.keyId is required" in out.stderr


def test_a_single_replica_queue_has_no_disruption_budget() -> None:
    doc = render("--set", "codecKey.keyId=k1")
    budgets = [d for d in doc.split("\n---\n") if "kind: PodDisruptionBudget" in d]
    names = {m for d in budgets for m in re.findall(r"name: (llm-workers-[a-z-]+),", d)}
    assert names == {"llm-workers-llm-generation", "llm-workers-llm-extraction"}, names
