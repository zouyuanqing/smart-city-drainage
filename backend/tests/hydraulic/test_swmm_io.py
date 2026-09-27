"""Behavior tests and bounded malicious fixtures for SWMM INP I/O."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.hydraulic.exporters.swmm import (
    SwmmExportError,
    export_inp,
    export_swmm,
)
from app.services.hydraulic.importers.swmm import (
    SwmmNetwork,
    SwmmParseError,
    SwmmParser,
    parse_inp,
    parse_swmm,
    parse_swmm_inp,
)

# A compact but complete fixture.  Lower/mixed-case section names and comments
# are intentional: section names are case-insensitive, while line comments are
# ignored and must not change the semantic model.
VALID_INP = """\
[TITLE]
; comment before a section
Minimal safe network
[OPTIONS]
FLOW_UNITS CFS
INFILTRATION ON
[jUnCtIoNs]
; name elev max-depth init-depth surcharge-depth ponded-area
J-1 10.0 4.0 0.0 0.0 0.0
[OUTFALLS]
; name elev type stage gated
OUT 5.0 FREE NO
[CONDUITS]
; name from to length roughness in-offset out-offset init-flow max-flow
C-1 J-1 OUT 100.0 0.013 0.0 0.0 0.0 0.0
[SUBCATCHMENTS]
; name outlet area width slope imperv n-perv s-perv dstore-i dstore-p
SUB-1 J-1 1.25 100.0 1.5 45.0 0.1 0.05 1.0 2.0
[COORDINATES]
J-1 121.3000 31.2000
OUT 121.3100 31.1900
[SLOPES]
C-1 J-1 0.0 OUT 0.0
[XSECTIONS]
; raw supported section retained for a runnable round-trip
C-1 CIRCULAR 0.8 0.8 0 0 1
[REPORT]
INPUT YES
CONTROLS NO
"""

MALICIOUS_PATH_FIXTURES = {
    "timeseries-file": """[TIMESERIES]\nTS1 FILE \"C:\\\\Users\\\\outside.dat\"\n""",
    "url": """[INFLOWS]\nI1 FLOW 1.0 FILE https://example.invalid/rain.dat\n""",
    "traversal": """[TIMESERIES]\nTS1 FILE ../secrets.txt\n""",
    "unc": """[TIMESERIES]\nTS1 FILE \\\\server\\share\\rain.dat\n""",
    "forbidden-section": """[FILES]\nrain.dat\n""",
    "import-section": """[IMPORT]\nnetwork.inp\n""",
}

MALFORMED_FIXTURES = {
    "duplicate-section": VALID_INP + "\n[COORDINATES]\nJ-1 121.3 31.2\n",
    "duplicate-junction-id": VALID_INP.replace(
        "J-1 10.0 4.0 0.0 0.0 0.0", "J-1 10.0 4.0 0.0 0.0 0.0\nJ-1 11.0 4.0 0.0 0.0 0.0"
    ),
    "missing-coordinate": VALID_INP.replace("OUT 121.3100 31.1900\n", ""),
    "nan-coordinate": VALID_INP.replace("121.3000 31.2000", "NaN 31.2000"),
    "bad-row": VALID_INP.replace("C-1 J-1 OUT 100.0 0.013 0.0 0.0 0.0 0.0", "C-1 J-1"),
    "duplicate-slope": VALID_INP + "\n[SLOPES]\nC-1 J-1 0.0 OUT 0.0\n",
    "dangling-conduit": VALID_INP.replace("C-1 J-1 OUT", "C-1 J-1 MISSING"),
    "cross-section-duplicate-id": VALID_INP.replace("C-1 J-1 OUT", "J-1 J-1 OUT"),
}


def test_parser_reads_all_prd_sections_case_insensitively_with_comments() -> None:
    model = parse_swmm(VALID_INP)

    assert [(row.name, row.elevation) for row in model.junctions] == [("J-1", 10.0)]
    assert [(row.name, row.boundary_type, row.gated) for row in model.outfalls] == [
        ("OUT", "FREE", False)
    ]
    assert model.conduits[0].from_node == "J-1"
    assert model.conduits[0].to_node == "OUT"
    assert model.conduits[0].length == 100.0
    assert model.subcatchments[0].area == 1.25
    assert [(row.name, row.x, row.y) for row in model.coordinates] == [
        ("J-1", 121.3, 31.2),
        ("OUT", 121.31, 31.19),
    ]
    assert model.slopes[0].conduit == "C-1"
    assert model.options_map["FLOW_UNITS"] == "CFS"
    assert any(row.section == "XSECTIONS" for row in model.raw_sections)
    assert model.valid
    assert model.issues == ()
    assert parse_inp(VALID_INP).content_sha256 == model.content_sha256
    assert parse_swmm_inp(VALID_INP).content_sha256 == model.content_sha256
    assert SwmmParser().parse(VALID_INP).content_sha256 == model.content_sha256


def test_explicit_facade_aliases_keep_the_parser_api_compatible() -> None:
    model = parse_swmm_inp(VALID_INP)
    output = export_inp(model)
    assert output.startswith("[TITLE]\n")
    assert parse_inp(output).content_sha256 == model.content_sha256


@pytest.mark.parametrize("fixture_name", sorted(MALICIOUS_PATH_FIXTURES))
def test_path_and_executable_directives_are_rejected(fixture_name: str) -> None:
    with pytest.raises(SwmmParseError) as raised:
        parse_swmm(MALICIOUS_PATH_FIXTURES[fixture_name])

    assert raised.value.code == "UNSAFE_PATH"
    assert raised.value.line is not None


@pytest.mark.parametrize("fixture_name", sorted(MALFORMED_FIXTURES))
def test_malformed_and_semantically_invalid_fixtures_are_rejected(
    fixture_name: str,
) -> None:
    with pytest.raises(SwmmParseError) as raised:
        parse_swmm(MALFORMED_FIXTURES[fixture_name])

    assert raised.value.code in {
        "INVALID_INP",
        "DUPLICATE_ID",
        "MISSING_COORDINATES",
        "DANGLING_NODE",
        "SELF_LOOP",
        "INVALID_SLOPE",
        "DUPLICATE_OPTION",
    }


def test_non_strict_parse_collects_missing_coordinate_and_duplicate_issues() -> None:
    fixture = MALFORMED_FIXTURES["missing-coordinate"]
    model = parse_swmm(fixture, strict=False)

    assert not model.valid
    assert "MISSING_COORDINATES" in {issue.code for issue in model.issues}
    assert all(issue.severity == "error" for issue in model.issues)

    duplicate = parse_swmm(
        "[JUNCTIONS]\nA 1 2 3 4 5\nA 2 3 4 5 6\n[COORDINATES]\nA 1 1\nA 1 1\n",
        strict=False,
    )
    assert "DUPLICATE_ID" in {issue.code for issue in duplicate.issues}


def test_parser_enforces_byte_line_and_control_boundaries() -> None:
    with pytest.raises(SwmmParseError) as too_large:
        parse_swmm("[TITLE]\n" + ("x" * 20), max_bytes=8)
    assert too_large.value.code == "INPUT_TOO_LARGE"

    with pytest.raises(SwmmParseError) as too_many_lines:
        parse_swmm("[TITLE]\na\nb\n", max_lines=2)
    assert too_many_lines.value.code == "INPUT_TOO_LARGE"

    with pytest.raises(SwmmParseError) as invalid_encoding:
        parse_swmm(b"[TITLE]\n\xff")
    assert invalid_encoding.value.code == "INVALID_ENCODING"

    with pytest.raises(SwmmParseError) as control_character:
        parse_swmm("[TITLE]\n\x00")
    assert control_character.value.code == "INVALID_INP"


def test_export_round_trip_is_reparseable_and_semantically_identical() -> None:
    model = parse_swmm(VALID_INP)

    exported = export_swmm(model)
    reparsed = parse_swmm(exported)

    assert exported.endswith("\n")
    assert reparsed.content_sha256 == model.content_sha256
    assert reparsed.source_sha256 != model.source_sha256
    assert [row.name for row in reparsed.nodes] == ["J-1", "OUT"]
    assert [row.name for row in reparsed.conduits] == ["C-1"]
    assert [row.name for row in reparsed.subcatchments] == ["SUB-1"]
    assert [row.name for row in reparsed.coordinates] == ["J-1", "OUT"]
    assert [row.conduit for row in reparsed.slopes] == ["C-1"]
    assert "FILE " not in exported.upper()


def test_export_rejects_unrepresentable_models_instead_of_writing_partial_inp() -> None:
    model = parse_swmm(MALFORMED_FIXTURES["missing-coordinate"], strict=False)
    # This fixture is valid syntax but has no OUT coordinate.
    assert not model.valid
    with pytest.raises(SwmmExportError) as raised:
        export_swmm(model)
    assert raised.value.code == "MISSING_COORDINATES"

    with pytest.raises(SwmmExportError) as duplicate:
        export_swmm(
            {
                "junctions": [
                    {"name": "A", "elevation": 1.0},
                    {"name": "a", "elevation": 2.0},
                ],
                "coordinates": [{"name": "A", "x": 0.0, "y": 0.0}],
            }
        )
    assert duplicate.value.code == "DUPLICATE_ID"


def test_mapping_exporter_accepts_canonical_field_aliases() -> None:
    output = export_swmm(
        {
            "title": ["mapping fixture"],
            "junctions": [{"id": "J1", "elevation_m": 3.0}],
            "outfalls": [{"id": "O1", "elevation_m": 1.0, "type": "FREE"}],
            "conduits": [
                {
                    "id": "C1",
                    "upstream_node_id": "J1",
                    "downstream_node_id": "O1",
                    "length_m": 12.0,
                    "roughness_manning_n": 0.01,
                }
            ],
            "coordinates": [
                {"node_id": "J1", "longitude": 10.0, "latitude": 20.0},
                {"node_id": "O1", "longitude": 11.0, "latitude": 19.0},
            ],
            "raw_sections": [
                {
                    "section": "XSECTIONS",
                    "tokens": ["C1", "CIRCULAR", "0.5"],
                }
            ],
        }
    )
    model = parse_swmm(output)
    assert model.options_map["FLOW_UNITS"] == "CFS"
    assert model.conduits[0].from_node == "J1"
    assert model.conduits[0].to_node == "O1"
    assert model.content_sha256 == parse_swmm(output).content_sha256


def test_direct_model_options_are_normalized_and_flow_units_exportable() -> None:
    model = SwmmNetwork(options={"flow_units": "CFS"})  # type: ignore[arg-type]
    assert model.options == (("FLOW_UNITS", "CFS"),)
    assert model.counts["nodes"] == 0


@pytest.mark.parametrize(
    "row",
    [
        "TS1 FILE C:secrets.txt",
        "TS1 FILE relative.txt:stream",
    ],
)
def test_drive_relative_and_ads_paths_use_the_frozen_unsafe_path_code(row: str) -> None:
    with pytest.raises(SwmmParseError) as raised:
        parse_swmm(f"[TIMESERIES]\n{row}\n")

    assert raised.value.code == "UNSAFE_PATH"
    assert raised.value.line == 2


def test_parser_rejects_duplicate_physical_segments_and_reverse_slopes() -> None:
    duplicate = VALID_INP.replace(
        "C-1 J-1 OUT 100.0 0.013 0.0 0.0 0.0 0.0",
        "C-1 J-1 OUT 100.0 0.013 0.0 0.0 0.0 0.0\n"
        "C-2 OUT J-1 100.0 0.013 0.0 0.0 0.0 0.0",
    )
    with pytest.raises(SwmmParseError) as duplicate_error:
        parse_swmm(duplicate)
    assert duplicate_error.value.code == "DUPLICATE_SEGMENT"

    reverse = VALID_INP.replace("OUT 5.0 FREE NO", "OUT 12.0 FREE NO")
    with pytest.raises(SwmmParseError) as reverse_error:
        parse_swmm(reverse)
    assert reverse_error.value.code == "REVERSE_SLOPE"

    without_slope_rows = VALID_INP.replace("[SLOPES]\nC-1 J-1 0.0 OUT 0.0\n", "")
    offset_valid = without_slope_rows.replace(
        "C-1 J-1 OUT 100.0 0.013 0.0 0.0 0.0 0.0",
        "C-1 J-1 OUT 100.0 0.013 5.0 0.0 0.0 0.0",
    )
    assert parse_swmm(offset_valid).valid
    offset_reverse = offset_valid.replace(
        "C-1 J-1 OUT 100.0 0.013 5.0 0.0 0.0 0.0",
        "C-1 J-1 OUT 100.0 0.013 6.0 0.0 0.0 0.0",
    )
    with pytest.raises(SwmmParseError) as offset_error:
        parse_swmm(offset_reverse)
    assert offset_error.value.code == "REVERSE_SLOPE"


@pytest.mark.parametrize(
    ("fixture", "expected_code"),
    [
        ("[XSECTIONS]\nC-1\n", "INVALID_ROW"),
        (
            VALID_INP.replace(
                "C-1 CIRCULAR 0.8 0.8 0 0 1",
                "C-1 CIRCULAR 0.8 0.8 0 0 1\nC-1 CIRCULAR 0.9",
            ),
            "DUPLICATE_ID",
        ),
        ("[OPTIONS]\nFLOW_UNITS PARSECS\n", "INVALID_OPTION"),
        ("[OPTIONS]\nINFILTRATION MAYBE\n", "INVALID_OPTION"),
        (
            VALID_INP.replace(
                "C-1 CIRCULAR 0.8 0.8 0 0 1",
                "MISSING CIRCULAR 0.8",
            ),
            "DANGLING_NODE",
        ),
    ],
)
def test_raw_rows_and_options_fail_closed_before_round_trip(
    fixture: str, expected_code: str
) -> None:
    with pytest.raises(SwmmParseError) as raised:
        parse_swmm(fixture)
    assert raised.value.code == expected_code


def test_timeseries_outfall_uses_a_text_stage_identifier() -> None:
    model = parse_swmm(
        VALID_INP.replace("OUT 5.0 FREE NO", "OUT 5.0 TIMESERIES STAGE-1 NO")
    )
    assert model.outfalls[0].stage == "STAGE-1"
    assert "STAGE-1" in export_swmm(model)


def test_local_coordinate_mode_requires_and_applies_an_explicit_affine_transform() -> (
    None
):
    local = VALID_INP.replace("121.3000 31.2000", "1000.0 2000.0").replace(
        "121.3100 31.1900", "1010.0 1990.0"
    )
    with pytest.raises(SwmmParseError) as missing_transform:
        parse_swmm(local, coordinate_mode="local")
    assert missing_transform.value.code == "CRS_UNKNOWN"

    model = parse_swmm(
        local,
        coordinate_mode="local",
        local_to_wgs84=(0.001, 0.0, 120.0, 0.0, 0.001, 29.0),
    )
    assert [(row.x, row.y) for row in model.coordinates] == [
        (121.0, 31.0),
        (121.01, 30.99),
    ]


def test_direct_option_sequences_normalize_keys_before_defaulting() -> None:
    model = SwmmNetwork(options=[("flow_units", "CFS")])  # type: ignore[arg-type]
    assert model.options == (("FLOW_UNITS", "CFS"),)
    assert model._effective_options_map()["FLOW_UNITS"] == "CFS"


def test_mapping_export_requires_cross_sections_and_maps_malformed_rows_stably() -> (
    None
):
    mapping = {
        "junctions": [{"id": "J1", "elevation_m": 3.0}],
        "outfalls": [{"id": "O1", "elevation_m": 1.0, "type": "FREE"}],
        "conduits": [
            {
                "id": "C1",
                "upstream_node_id": "J1",
                "downstream_node_id": "O1",
                "length_m": 12.0,
                "roughness_manning_n": 0.01,
            }
        ],
        "coordinates": [
            {"node_id": "J1", "longitude": 10.0, "latitude": 20.0},
            {"node_id": "O1", "longitude": 11.0, "latitude": 19.0},
        ],
    }
    with pytest.raises(SwmmExportError) as missing_profile:
        export_swmm(mapping)
    assert missing_profile.value.code == "MISSING_XSECTIONS"

    malformed_values = [
        {"junctions": [{"id": "J1", "elevation_m": float("nan")}]},
        {"junctions": [{"id": "J1", "elevation_m": 1.0}], "options": [None]},
        {"raw_sections": [{"section": "XSECTIONS", "tokens": ["C1"]}]},
    ]
    for malformed in malformed_values:
        with pytest.raises(SwmmExportError) as raised:
            export_swmm(malformed)
        assert raised.value.code in {"INVALID_MAPPING", "MISSING_XSECTIONS"}


def test_frozen_swmm_fixture_registry_matches_manifest() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures" / "hydraulic" / "imports"
    manifest_path = fixture_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "hydraulic-import-fixture-manifest-v1"

    for entry in manifest["fixtures"]:
        payload = (fixture_root / entry["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        assert len(payload) == entry["bytes"]

    valid = (fixture_root / "swmm_minimal_wgs84.inp").read_text(encoding="utf-8")
    model = parse_swmm(valid)
    assert model.counts["conduits"] == 1
    assert model.valid and model.exportable

    local = (fixture_root / "swmm_minimal_local.inp").read_text(encoding="utf-8")
    transformed = parse_swmm(
        local,
        coordinate_mode="local",
        local_to_wgs84=(0.001, 0.0, 121.0, 0.0, 0.001, 31.0),
    )
    assert [(row.x, row.y) for row in transformed.coordinates] == [
        (121.3, 31.2),
        (121.31, 31.19),
    ]

    unsafe_rows = json.loads(
        (fixture_root / "unsafe_paths.json").read_text(encoding="utf-8")
    )
    for row in unsafe_rows:
        with pytest.raises(SwmmParseError) as raised:
            parse_swmm(f"[TIMESERIES]\n{row}\n")
        assert raised.value.code == "UNSAFE_PATH"
