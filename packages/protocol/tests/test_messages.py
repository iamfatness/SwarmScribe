import pytest
from pydantic import ValidationError
from swarmscribe_protocol import (
    Capabilities,
    ClaimResponse,
    Correction,
    ErrorBody,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    JobSettings,
    Link,
    OutputChecksums,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
    SubmitResponse,
    UploadUrls,
    Vocabulary,
)


def _link(method="GET"):
    return Link(url="https://storage.example/object?sig=abc", method=method)


def _claim():
    return ClaimResponse(
        job_id="job-1",
        lease_id="lease-1",
        download_url=_link(),
        upload_urls=UploadUrls(txt=_link("PUT"), srt=_link("PUT"), segments_json=_link("PUT")),
        settings=JobSettings(model="large-v3", compute_type="float16"),
        vocabulary=Vocabulary(
            version=2,
            terms=["Ashford"],
            corrections=[Correction(heard="ash ford", replacement="Ashford")],
        ),
        source_version="etag-1",
    )


@pytest.mark.parametrize(
    "message",
    [
        RegisterRequest(
            join_token="token",
            protocol_version=1,
            capabilities=Capabilities(
                device="cuda",
                gpu_name="RTX 4090",
                gpu_memory_mb=24564,
                models=["large-v3"],
                engine_version="0.1.0",
                pool="gpu",
            ),
        ),
        RegisterResponse(
            follower_id="f-1", credential="secret", heartbeat_interval=30, lease_seconds=120
        ),
        _claim(),
        HeartbeatRequest(lease_id="lease-1", progress=0.5),
        HeartbeatResponse(directive="drain"),
        SubmitRequest(
            lease_id="lease-1",
            checksums=OutputChecksums(
                source="d" * 64, txt="a" * 64, srt="b" * 64, segments_json="c" * 64
            ),
        ),
        SubmitResponse(accepted=True),
        FailRequest(
            lease_id="lease-1", code="undecodable", reason="undecodable audio", retryable=False
        ),
        ReleaseRequest(lease_id="lease-1"),
    ],
    ids=lambda m: type(m).__name__,
)
def test_every_message_round_trips_through_json(message):
    assert type(message).model_validate_json(message.model_dump_json()) == message


def test_cpu_follower_capabilities_need_no_gpu_fields():
    capabilities = Capabilities(
        device="cpu", models=["distil-large-v3"], engine_version="0.1.0", pool="cpu"
    )
    assert capabilities.gpu_name is None
    assert capabilities.gpu_memory_mb is None


def test_link_headers_default_to_empty():
    assert _link().headers == {}


def test_link_rejects_methods_other_than_get_and_put():
    with pytest.raises(ValidationError):
        Link(url="https://storage.example/object", method="DELETE")


def test_heartbeat_directive_must_be_known():
    with pytest.raises(ValidationError):
        HeartbeatResponse(directive="explode")


@pytest.mark.parametrize("progress", [-0.1, 1.1])
def test_heartbeat_progress_must_be_a_fraction(progress):
    with pytest.raises(ValidationError):
        HeartbeatRequest(lease_id="lease-1", progress=progress)


def test_heartbeat_progress_is_optional():
    assert HeartbeatRequest(lease_id="lease-1").progress is None


@pytest.mark.parametrize("field", ["heartbeat_interval", "lease_seconds"])
def test_register_response_timings_must_be_positive(field):
    values = {
        "follower_id": "f-1",
        "credential": "secret",
        "heartbeat_interval": 30,
        "lease_seconds": 120,
        field: 0,
    }
    with pytest.raises(ValidationError):
        RegisterResponse(**values)


def test_claim_carries_the_fixed_settings():
    assert _claim().settings.condition_on_previous_text is False


def test_claim_carries_the_vocabulary_and_no_glossary():
    claim = _claim()
    assert claim.vocabulary.version == 2
    assert claim.vocabulary.corrections[0].replacement == "Ashford"
    assert "glossary" not in ClaimResponse.model_fields


def test_claim_carries_a_source_version_and_no_checksum():
    assert _claim().source_version == "etag-1"
    assert "source_checksum" not in ClaimResponse.model_fields


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, ""])
def test_checksums_must_be_lowercase_hex_sha256(bad):
    with pytest.raises(ValidationError):
        OutputChecksums(source=bad, txt="a" * 64, srt="a" * 64, segments_json="a" * 64)


def test_fail_request_code_must_be_known():
    with pytest.raises(ValidationError):
        FailRequest(lease_id="lease-1", code="exploded", reason="x", retryable=True)


def test_error_body_round_trips():
    body = ErrorBody(code="stale_lease", message="this job is not leased to you")
    assert ErrorBody.model_validate_json(body.model_dump_json()) == body


def test_fail_request_reason_is_bounded():
    FailRequest(lease_id="lease-1", code="other", reason="x" * 2000, retryable=True)
    with pytest.raises(ValidationError):
        FailRequest(lease_id="lease-1", code="other", reason="x" * 2001, retryable=True)


def _capabilities(**overrides):
    values = {
        "device": "cpu",
        "gpu_name": None,
        "models": ["distil-large-v3"],
        "engine_version": "0.1.0",
        "pool": "default",
    }
    values.update(overrides)
    return Capabilities(**values)


@pytest.mark.parametrize("field", ["gpu_name", "engine_version", "pool"])
def test_capability_strings_are_bounded(field):
    assert getattr(_capabilities(**{field: "x" * 200}), field) == "x" * 200
    with pytest.raises(ValidationError):
        _capabilities(**{field: "x" * 201})


def test_capability_models_are_bounded():
    assert len(_capabilities(models=["m"] * 50).models) == 50
    with pytest.raises(ValidationError):
        _capabilities(models=["m"] * 51)
    with pytest.raises(ValidationError):
        _capabilities(models=["m" * 201])


# --- fresh links and the drain header (follower spec, section 12) -----------------------


def test_job_links_carry_one_download_and_three_upload_links():
    from swarmscribe_protocol import JobLinks, LinksRequest

    put = {"url": "https://storage.example.org/out", "method": "PUT"}
    links = JobLinks.model_validate(
        {
            "download_url": {"url": "https://storage.example.org/in", "method": "GET"},
            "upload_urls": {"txt": put, "srt": put, "segments_json": put},
        }
    )
    assert links.download_url.method == "GET"
    assert links.upload_urls.segments_json.method == "PUT"
    assert LinksRequest(lease_id="lease-1").model_dump() == {"lease_id": "lease-1"}


def test_the_drain_header_name_and_value_are_fixed():
    from typing import get_args

    from swarmscribe_protocol import DIRECTIVE_HEADER, Directive

    assert DIRECTIVE_HEADER == "X-SwarmScribe-Directive"
    assert "drain" in get_args(Directive)


def test_a_links_url_never_appears_in_a_repr_or_a_validation_error():
    from swarmscribe_protocol import JobLinks, Link

    secret = "https://storage.example.org/o?sig=SECRETSIG"
    assert "SECRETSIG" not in repr(Link(url=secret, method="GET"))
    put = {"url": secret, "method": "PUT"}
    links = JobLinks.model_validate(
        {
            "download_url": {"url": secret, "method": "GET"},
            "upload_urls": {"txt": put, "srt": put, "segments_json": put},
        }
    )
    assert "SECRETSIG" not in repr(links) and "SECRETSIG" not in str(links)
    with pytest.raises(ValidationError) as bad:
        Link.model_validate({"url": secret, "method": "DELETE"})
    assert "SECRETSIG" not in str(bad.value)
