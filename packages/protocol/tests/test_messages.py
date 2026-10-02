import pytest
from pydantic import ValidationError
from swarmscribe_protocol import (
    Capabilities,
    ClaimResponse,
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
        glossary=["Shiloh"],
        source_checksum="a" * 64,
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
            checksums=OutputChecksums(txt="a" * 64, srt="b" * 64, segments_json="c" * 64),
        ),
        SubmitResponse(accepted=True),
        FailRequest(lease_id="lease-1", reason="undecodable audio", retryable=False),
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
