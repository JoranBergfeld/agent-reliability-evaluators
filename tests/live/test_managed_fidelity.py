"""Live Foundry probes of managed-output fidelity. Opt-in, creates billable Azure resources.

Run with `uv run pytest -m live tests/live/test_managed_fidelity.py -s`. It provisions a disposable
Foundry project with nothing in it, and tears it down again. Set ARE_LIVE_PROJECT_GROUP to an
already provisioned resource group to reuse it; that group is then left in place for you to tear down.

The question is whether a value computed inside a Foundry code evaluator is the value that is
received. A test-only evaluator computes a double from text, which reaches the sandbox unchanged,
and returns it on the channel a row asks for:

- the numeric channel: the double itself, as the score. This is how a metric value comes back.
- the encoded channel: the eight bytes of the double as two whole numbers, of six bytes and of
  two. This is how the parts of an encoded result come back.

Each value is asked for on both channels, in rows of its own. What the encoded channel returns is
compared with the bytes of the double this machine computes from the same text, and what the
numeric channel returns is compared with what the encoded channel returned. The values are
synthetic and nothing but numbers is submitted. The evaluator goes when the resource group is deleted.
"""

import json
import math
import os
import random
import struct
import time

import pytest

from live_fixture import teardown
from live_fixture.project_only import connect_project, provision_project
from reliability_evaluators.evaluators import outcome_consistency
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryProject, azure_cli_token, derive_artifact
from reliability_evaluators.managed_scoring.grader import decode_result, result_parts
from reliability_evaluators.scorecard import AcceptanceCriterion
from tests.live.test_managed_outcome_consistency import sweep_on_the_fixture, synthetic_evidence

pytestmark = pytest.mark.live

PROBE_EVALUATOR = "are-test-only-fidelity-probe"
PROBE_CODE = '''import struct


def computed(compute):
    kind, _, argument = compute.partition(":")
    if kind == "hex":
        return float.fromhex(argument)
    if kind == "decimal":
        return float(argument)
    if kind == "ratio":
        numerator, _, denominator = argument.partition("/")
        return int(numerator) / int(denominator)
    if kind == "mean":
        numbers = [float(text) for text in argument.split(",")]
        return sum(numbers) / len(numbers)
    if kind == "whole":
        return int(argument)
    raise ValueError(compute)


def grade(sample, item):
    value = computed(item["compute"])
    channel = item["channel"]
    if channel == "numeric":
        return value
    encoded = struct.pack(">d", value)
    if channel == "encoded_1":
        return int.from_bytes(encoded[:6], "big")
    if channel == "encoded_2":
        return int.from_bytes(encoded[6:], "big")
    raise ValueError(channel)
'''
ROWS_IN_A_RUN = 100
TIMES_ASKED = 3


def computed_here(compute):
    """The double this machine computes from the text a row submits, by the arithmetic the probe evaluator uses."""
    kind, _, argument = compute.partition(":")
    if kind == "hex":
        return float.fromhex(argument)
    if kind == "decimal":
        return float(argument)
    if kind == "ratio":
        numerator, _, denominator = argument.partition("/")
        return int(numerator) / int(denominator)
    numbers = [float(text) for text in argument.split(",")]
    return sum(numbers) / len(numbers)


def precision_cases():
    """What the evaluator is asked to compute: values that stress precision, each named by how it is computed."""
    decimals = [
        # The values of the earlier evidence: 0.83 went in as 0.8300000000000001, and 0.6799999999999999 came back.
        "0.83", "0.8300000000000001", "0.68", "0.6799999999999999", "0.49999999999999994", "0.49999999999999906",
        "0.36", "0.1", "0.2", "0.3", "0.30000000000000004", "0.5", "0.25", "0.7", "0.9", "0.99", "0.999999",
        # Decimals longer than a double holds.
        "0.1234567890123456789", "0.123456789012345678901234567890", "0.99999999999999999", "3.141592653589793",
        "2.718281828459045", "123456.789", "4503599627370496.5",
        # Where a number is written with an exponent.
        "0.001", "0.0001", "0.00001", "0.000001", "0.0000001", "1e-10", "1e15", "1e16", "1e17", "1e21", "1e22",
        "1e100", "1.7976931348623157e308", "2.2250738585072014e-308", "2.225073858507201e-308", "5e-324",
        # Whole numbers, zero with either sign, and numbers below a scale that starts at zero.
        "0.0", "-0.0", "1.0", "2.0", "100.0", "9007199254740992.0", "-1.0", "-0.83", "-0.1",
    ]
    cases = [f"decimal:{text}" for text in decimals]
    # Adjacent distinct values: the doubles directly below and above.
    for text in ("0.83", "0.68", "0.5", "1.0", "0.1", "0.36", "0.3333333333333333"):
        cases += [f"hex:{math.nextafter(float(text), bound).hex()}" for bound in (-math.inf, math.inf)]
    cases += [f"ratio:{numerator}/{denominator}" for denominator in (3, 7, 9, 11, 13) for numerator in range(1, denominator)]
    cases += [
        "mean:0.36,1.0",
        "mean:0.1,0.2,0.3",
        "mean:0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1",
        "mean:0.8300000000000001,0.83",
    ]
    chosen = random.Random(47)
    cases += [f"hex:{chosen.random().hex()}" for _ in range(120)]
    for _ in range(15):
        value = chosen.random()
        cases += [f"hex:{value.hex()}", f"hex:{math.nextafter(value, math.inf).hex()}"]
    while len(cases) < 281:
        (value,) = struct.unpack(">d", chosen.getrandbits(64).to_bytes(8, "big"))
        if math.isfinite(value):
            cases.append(f"hex:{value.hex()}")
    return list(dict.fromkeys(cases))


# Whole numbers of up to six bytes, as the parts of an encoded result are. Asked for as the score itself.
WHOLE_NUMBERS = (
    [0, 1, 255, 256, 2**16, 2**32, 2**47, 2**48 - 1, 2**48 - 2, 123456789012345, 99999999999999, 281474976710655]
    + [10**exponent for exponent in range(1, 15)]
    + [7 * 10**exponent for exponent in (5, 9, 13)]
)


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_PROJECT_GROUP")
    provisioned = connect_project(reused) if reused else provision_project(ticket="47")
    print(json.dumps({"project": provisioned.summary()}))
    try:
        yield provisioned
    finally:
        if not reused:
            teardown(provisioned.resource_group)


@pytest.fixture(scope="module")
def scoring(live):
    """Managed scoring as a customer sets it up: the project endpoint and the Azure CLI's identity."""
    return FoundryManagedScoring(project=FoundryProject(live.project_endpoint, token=azure_cli_token))


def evaluation(live, name, evaluator_name, evaluator_version, fields):
    """Make an evaluation that runs exactly one version of one evaluator on items with the given text fields."""
    return live.project_request(
        "POST",
        "/openai/v1/evals",
        {
            "name": f"Reliability Evaluators live check: {name}",
            "data_source_config": {
                "type": "custom",
                "item_schema": {"type": "object", "properties": {field: {"type": "string"} for field in fields}},
                "include_sample_schema": True,
            },
            "testing_criteria": [
                {
                    "type": "azure_ai_evaluator",
                    "name": "probe",
                    "evaluator_name": evaluator_name,
                    "evaluator_version": evaluator_version,
                    "initialization_parameters": {},
                }
            ],
        },
    )["id"]


@pytest.fixture(scope="module")
def probe_eval(live):
    """Register the probe evaluator, and make an evaluation that runs exactly that version."""
    properties = {"compute": {"type": "string"}, "channel": {"type": "string"}}
    created = live.project_request(
        "POST",
        f"/evaluators/{PROBE_EVALUATOR}/versions?api-version=v1",
        {
            "name": PROBE_EVALUATOR,
            "evaluator_type": "custom",
            "categories": ["quality"],
            "display_name": "Test-only fidelity probe",
            "description": "Registered by a live check to probe what a returned number preserves. Not a reliability Evaluator.",
            "definition": {
                "type": "code",
                "code_text": PROBE_CODE,
                "init_parameters": {"type": "object", "properties": {}, "required": []},
                "data_schema": {
                    "type": "object",
                    "required": ["item"],
                    "properties": {"item": {"type": "object", "properties": properties, "required": list(properties)}},
                },
                "metrics": {
                    "result": {"type": "continuous", "desirable_direction": "neutral", "min_value": 0, "max_value": 1}
                },
            },
        },
        {"Foundry-Features": "Evaluations=V1Preview"},
    )
    return evaluation(live, "fidelity probe", PROBE_EVALUATOR, str(created["version"]), properties)


def returned_scores(live, eval_id, rows):
    """What the project returns as the score of each row, as the JSON of its answer reads, and how many rows it took.

    The rows go in runs that are all started before the first is waited for. A row that the
    project did not grade is asked for again, as the sweep does.
    """
    scores = {}
    asked = list(range(len(rows)))
    submitted = 0
    for _ in range(TIMES_ASKED):
        runs = []
        for start in range(0, len(asked), ROWS_IN_A_RUN):
            batch = asked[start : start + ROWS_IN_A_RUN]
            run = live.project_request(
                "POST",
                f"/openai/v1/evals/{eval_id}/runs",
                {
                    "name": "fidelity probe",
                    "data_source": {
                        "type": "jsonl",
                        "source": {"type": "file_content", "content": [{"item": rows[row]} for row in batch]},
                    },
                },
            )
            runs.append((run["id"], batch))
            submitted += len(batch)
        for run_id, batch in runs:
            path = f"/openai/v1/evals/{eval_id}/runs/{run_id}"
            deadline = time.monotonic() + 3600
            run = live.project_request("GET", path)
            while run["status"] not in ("completed", "failed", "canceled") and time.monotonic() < deadline:
                time.sleep(15)
                run = live.project_request("GET", path)
            assert run["status"] == "completed", (run["status"], run.get("error"))
            items, after = [], ""
            while True:
                page = live.project_request("GET", f"{path}/output_items?limit=100{after}")
                items += page["data"]
                if not page["has_more"]:
                    break
                after = f"&after={page['last_id']}"
            assert sorted(item["datasource_item_id"] for item in items) == list(range(len(batch)))
            for item in items:
                (result,) = item["results"]
                if result["status"] == "completed":
                    scores[batch[item["datasource_item_id"]]] = result["score"]
        asked = [row for row in range(len(rows)) if row not in scores]
        if not asked:
            break
    assert not asked, f"{len(asked)} rows were not graded in {TIMES_ASKED} runs"
    return [scores[row] for row in range(len(rows))], submitted - len(rows)


def as_bytes(double):
    return struct.pack(">d", double)


def test_a_double_computed_in_the_evaluator_is_the_double_received_on_both_channels(live, probe_eval):
    cases = precision_cases()
    channels = ("numeric", "encoded_1", "encoded_2")
    rows = [{"compute": compute, "channel": channel} for compute in cases for channel in channels]
    started = time.monotonic()

    scores, asked_again = returned_scores(live, probe_eval, rows)

    returned = {(row["compute"], row["channel"]): score for row, score in zip(rows, scores)}
    # The encoded channel: two whole numbers that are the bytes of the double the evaluator computed.
    encoded_parts_altered = [
        compute
        for compute in cases
        if (
            type(returned[compute, "encoded_1"]) is not int
            or type(returned[compute, "encoded_2"]) is not int
            or returned[compute, "encoded_1"].to_bytes(6, "big") + returned[compute, "encoded_2"].to_bytes(2, "big")
            != as_bytes(computed_here(compute))
        )
    ]
    # The numeric channel: the score, read as a double, against the double the encoded channel carried.
    numeric_differs = {
        compute: (repr(computed_here(compute)), repr(returned[compute, "numeric"]))
        for compute in cases
        if as_bytes(float(returned[compute, "numeric"])) != as_bytes(computed_here(compute))
    }
    numeric_unequal = [compute for compute in cases if returned[compute, "numeric"] != computed_here(compute)]
    print(json.dumps({"seconds": round(time.monotonic() - started), "fidelity_probe": {
        "doubles": len(cases),
        "rows": len(rows),
        "rows_asked_for_again": asked_again,
        "encoded_channel_altered": encoded_parts_altered,
        "numeric_channel_other_bits": numeric_differs,
        "numeric_channel_other_number": numeric_unequal,
        "numeric_channel_as_received": {
            compute: json.dumps(returned[compute, "numeric"])
            for compute in cases
            if compute.startswith(("decimal:", "mean:"))
        },
    }}))
    assert len(cases) == 281
    # Every double came back on the encoded channel with the bytes this machine computes for it.
    assert encoded_parts_altered == []
    # On the numeric channel every double came back as the same number.
    assert numeric_unequal == []
    # And as the same double, but for negative zero, which came back without its sign.
    assert numeric_differs == {"decimal:-0.0": ("-0.0", "0")}


def test_whole_numbers_of_up_to_six_bytes_come_back_unchanged(live, probe_eval):
    rows = [{"compute": f"whole:{number}", "channel": "numeric"} for number in WHOLE_NUMBERS]

    scores, _ = returned_scores(live, probe_eval, rows)

    print(json.dumps({"whole_numbers": {str(number): json.dumps(score) for number, score in zip(WHOLE_NUMBERS, scores)}}))
    assert [(score, float(score).is_integer()) for score in scores] == [(number, True) for number in WHOLE_NUMBERS]


def successes(succeeded, attempts):
    return [True] * succeeded + [False] * (attempts - succeeded)


# Evidence whose outcome consistency is a long decimal, with the value calculated by hand as a fraction.
# A Task with s successes in n Attempts has the value (2s - n)^2 / n^2, and the metric is the mean over the Tasks.
LONG_DECIMALS = {
    "2 of 3": ({"a": successes(2, 3)}, 1 / 9),
    "5 of 7": ({"a": successes(5, 7)}, 9 / 49),
    "7 of 9": ({"a": successes(7, 9)}, 25 / 81),
    "9 of 11": ({"a": successes(9, 11)}, 49 / 121),
    "7 of 13": ({"a": successes(7, 13)}, 1 / 169),
    # (1/9 + 9/49) / 2 = 130/882 = 65/441.
    "2 of 3 and 5 of 7": ({"a": successes(2, 3), "b": successes(5, 7)}, 65 / 441),
    # (1/9 + 9/49 + 9/25) / 3 = (1225 + 2025 + 3969) / 11025 / 3 = 7219/33075.
    "2 of 3, 5 of 7 and 4 of 5": ({"a": successes(2, 3), "b": successes(5, 7), "c": successes(4, 5)}, 7219 / 33075),
    # (9/25 + 1) / 2 = 0.68, which came back as 0.6799999999999999 in the earlier checks.
    "4 of 5 and 0 of 3": ({"a": successes(4, 5), "b": successes(0, 3)}, 0.68),
}


def test_the_plain_value_of_the_derived_evaluator_is_the_value_in_its_encoded_result(live, scoring):
    artifact = derive_artifact("outcome_consistency")
    version = scoring.register(["outcome_consistency"])["outcome_consistency"].version
    eval_id = evaluation(
        live, "plain and encoded value", artifact.evaluator_name, version, (artifact.evidence_field, "result_part")
    )
    evidence = {name: artifact.item(synthetic_evidence(outcomes)) for name, (outcomes, _) in LONG_DECIMALS.items()}
    started = time.monotonic()

    def asked(rows):
        scores, _ = returned_scores(live, eval_id, [{**evidence[name], "result_part": part} for name, part in rows])
        return dict(zip(rows, scores))

    plain = asked([(name, part) for name in evidence for part in ("value", "size")])
    part_rows = [(name, str(number)) for name in evidence for number in range(1, result_parts(plain[name, "size"]) + 1)]
    parts = asked(part_rows)

    encoded = {
        name: decode_result(
            outcome_consistency.DEFINITION,
            evidence[name][artifact.evidence_field],
            plain[name, "size"],
            [parts[row] for row in part_rows if row[0] == name],
        )
        for name in evidence
    }
    print(json.dumps({"seconds": round(time.monotonic() - started), "plain_and_encoded": {
        name: {"plain": repr(plain[name, "value"]), "encoded": repr(encoded[name].value), "parts": result_parts(plain[name, "size"])}
        for name in evidence
    }}))
    for name, (_, by_hand) in LONG_DECIMALS.items():
        assert encoded[name].value == pytest.approx(by_hand, abs=1e-12), name
        # Exactly the same number on both channels: nothing is compared to a tolerance.
        assert plain[name, "value"] == encoded[name].value, name


def test_an_authoritative_value_from_the_live_project_supports_an_acceptance_verdict(live, scoring, monkeypatch):
    # close: 2 of 3, 1/9. archive: 5 of 7, 9/49. reopen: 4 of 5, 9/25. Outcome consistency: 7219/33075, about 0.2183.
    script = {
        "close": ["success"] * 2 + ["failure"],
        "archive": ["success"] * 5 + ["failure"] * 2,
        "reopen": ["success"] * 4 + ["failure"],
    }
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_most": 0.25}})

    scorecard = sweep_on_the_fixture(script, scoring, monkeypatch, acceptance_criteria=[criterion])

    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    print(json.dumps({"authoritative_value": {
        key: entry[key] for key in ("state", "value", "scoring_path", "fidelity", "acceptance")
    }, "fidelity_check": {key: entry["managed_scoring"]["fidelity_check"][key] for key in ("outcome", "plain_value", "encoded_value")}}))
    assert entry["value"] == pytest.approx(7219 / 33075, abs=1e-12)
    assert (entry["scoring_path"], entry["fidelity"]) == ("foundry_managed", "authoritative")
    check = entry["managed_scoring"]["fidelity_check"]
    assert (check["outcome"], check["plain_value"], check["encoded_value"]) == ("passed", entry["value"], entry["value"])
    # The value is at most 0.25, and it is the offering that says so: the criterion is assessed on a managed value.
    assert (entry["acceptance"]["status"], entry["acceptance"]["assessed_by"]) == ("met", "offering")
    assert entry["acceptance"]["checks"] == [
        {"subject": "value", "comparison": "at_most", "threshold": 0.25, "observed": entry["value"], "met": True}
    ]
