"""Acceptance must fail closed when evidence is missing or incomplete."""
import importlib.util
from pathlib import Path
import pytest


def reporter():
    path = Path(__file__).resolve().parents[1] / 'tools/acceptance_report.py'
    spec = importlib.util.spec_from_file_location('acceptance_report', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence(tmp_path, body):
    path = tmp_path / 'results.xml'
    path.write_text('<testsuites><testsuite>' + body + '</testsuite></testsuites>')
    return path


def mapping(coverage='full', tests=None):
    return {'id': 'GOLD01', 'tests': tests if tests is not None else ['tests/test_sample.py::test_transfer'],
            'coverage': coverage, 'rationale': 'Exact synthetic input and output assertions.', 'remaining': []}


def test_only_complete_executed_mapping_passes(tmp_path):
    report = reporter().build_report([{'id': 'GOLD01'}], [mapping()], [evidence(tmp_path,
        '<testcase classname="tests.test_sample" name="test_transfer"/>')])
    assert report['cases'][0]['status'] == 'PASSED'
    assert report['release_ready'] is True


@pytest.mark.parametrize('body,status', [
    ('', 'NOT_RUN'),
    ('<testcase classname="tests.test_other" name="test_transfer"/>', 'NOT_RUN'),
    ('<testcase classname="tests.test_sample" name="test_transfer"><skipped/></testcase>', 'SKIPPED'),
    ('<testcase classname="tests.test_sample" name="test_transfer"><failure/></testcase>', 'FAILED'),
    ('<testcase classname="tests.test_sample" name="test_transfer"><error/></testcase>', 'FAILED'),
])
def test_missing_skipped_failed_evidence_never_passes(tmp_path, body, status):
    report = reporter().build_report([{'id': 'GOLD01'}], [mapping()], [evidence(tmp_path, body)])
    assert report['cases'][0]['status'] == status
    assert not report['release_ready']


@pytest.mark.parametrize('coverage,status', [('partial', 'PARTIAL'), ('manual', 'BLOCKED')])
def test_successful_unit_evidence_does_not_complete_partial_or_hardware_case(tmp_path, coverage, status):
    report = reporter().build_report([{'id': 'GOLD01'}], [mapping(coverage)], [evidence(tmp_path,
        '<testcase classname="tests.test_sample" name="test_transfer"/>')])
    assert report['cases'][0]['status'] == status
    assert not report['release_ready']


def test_all_parameterized_instances_are_required(tmp_path):
    xml = evidence(tmp_path, '<testcase classname="tests.test_sample" name="test_transfer[python]"/>'
                   '<testcase classname="tests.test_sample" name="test_transfer[sql]"><skipped/></testcase>')
    assert reporter().build_report([{'id': 'GOLD01'}], [mapping()], [xml])['cases'][0]['status'] == 'SKIPPED'


def test_two_reports_cannot_hide_a_failure_with_a_pass(tmp_path):
    bad = evidence(tmp_path, '<testcase classname="tests.test_sample" name="test_transfer"><failure/></testcase>')
    good = tmp_path / 'other.xml'
    good.write_text('<testsuite><testcase classname="tests.test_sample" name="test_transfer"/></testsuite>')
    assert reporter().build_report([{'id': 'GOLD01'}], [mapping()], [bad, good])['cases'][0]['status'] == 'FAILED'


def test_exact_parameter_selection_does_not_match_another_parameter(tmp_path):
    xml = evidence(tmp_path, '<testcase classname="tests.test_sample" name="test_transfer[sql]"/>')
    row = mapping(tests=['tests/test_sample.py::test_transfer[python]'])
    assert reporter().build_report([{'id': 'GOLD01'}], [row], [xml])['cases'][0]['status'] == 'NOT_RUN'


def test_independent_fixture_run_can_resolve_the_same_skipped_instance(tmp_path):
    default = evidence(tmp_path, '<testcase classname="tests.test_sample" name="test_transfer[sql]"><skipped/></testcase>')
    actual = tmp_path / 'actual.xml'
    actual.write_text('<testsuite><testcase classname="tests.test_sample" name="test_transfer[sql]"/></testsuite>')
    assert reporter().build_report([{'id': 'GOLD01'}], [mapping()], [default, actual])['cases'][0]['status'] == 'PASSED'


def test_unittest_class_selector_matches_junit_classname(tmp_path):
    xml = evidence(tmp_path, '<testcase classname="tests.test_sample.WorkflowTests" name="test_transfer"/>')
    row = mapping(tests=['tests/test_sample.py::WorkflowTests::test_transfer'])
    assert reporter().build_report([{'id': 'GOLD01'}], [row], [xml])['cases'][0]['status'] == 'PASSED'


def test_unmapped_regression_failure_still_blocks_release(tmp_path):
    xml = evidence(tmp_path, '<testcase classname="tests.test_sample" name="test_transfer"/>'
                   '<testcase classname="tests.test_other" name="test_regression"><failure/></testcase>')
    report = reporter().build_report([{'id': 'GOLD01'}], [mapping()], [xml])
    assert report['cases'][0]['status'] == 'PASSED'
    assert not report['release_ready']


@pytest.mark.parametrize('rows', [[], [mapping(), mapping()], [{**mapping(), 'id': 'UNKNOWN'}]])
def test_missing_duplicate_unknown_mapping_is_rejected(rows):
    with pytest.raises(ValueError):
        reporter().build_report([{'id': 'GOLD01'}], rows, [])


def test_full_mapping_with_missing_oracles_is_rejected():
    row = {**mapping(), 'remaining': ['Must still prove actual n8n execution']}
    with pytest.raises(ValueError):
        reporter().build_report([{'id': 'GOLD01'}], [row], [])


def test_empty_full_mapping_is_not_a_pass():
    report = reporter().build_report([{'id': 'GOLD01'}], [mapping(tests=[])], [])
    assert report['cases'][0]['status'] == 'NOT_RUN'
    assert not report['release_ready']


def test_superseded_requires_reason_and_does_not_count_as_a_pass():
    report = reporter().build_report([{'id': 'GOLD01'}], [mapping('superseded', [])], [])
    assert report['cases'][0]['status'] == 'SUPERSEDED'
    assert report['summary'].get('PASSED', 0) == 0
    assert not report['release_ready']  # no executed cases at all
