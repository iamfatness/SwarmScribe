"""Settings profiles: the model, compute type and temperature ladder each device class
transcribes with. One per device; a claim carries the caller's device's profile, read at
the claim, so a change applies to every job claimed after it.

A profile is only ever updated in place (never replaced or deleted): jobs name it
(`settings_profile_id`) and a missing one would leave a claim with no settings."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .db.models import SettingsProfile


def profile_view(profile: SettingsProfile) -> dict[str, Any]:
    return {
        "device": profile.device,
        "name": profile.name,
        "model": profile.model,
        "compute_type": profile.compute_type,
        "temperatures": [float(value) for value in profile.temperatures],
    }


async def list_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    profiles = (
        await session.scalars(select(SettingsProfile).order_by(SettingsProfile.device))
    ).all()
    return [profile_view(profile) for profile in profiles]


async def set_profile(
    session: AsyncSession,
    device: str,
    *,
    model: str,
    compute_type: str,
    temperatures: list[float] | None,
    actor: str,
) -> SettingsProfile:
    """Change the device's profile (create it if the row is missing). `temperatures` None
    keeps the ladder it has."""
    profile = await session.scalar(
        select(SettingsProfile)
        .where(SettingsProfile.device == device)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None:
        profile = SettingsProfile(
            id=uuid.uuid4(), name=device, device=device, temperatures=[0.0, 0.2, 0.4]
        )
        session.add(profile)
    before = {"model": profile.model, "compute_type": profile.compute_type}
    profile.model = model
    profile.compute_type = compute_type
    if temperatures is not None:
        profile.temperatures = list(temperatures)
    audit.record(
        session,
        actor=actor,
        action="profile.set",
        subject_type="settings_profile",
        subject_id=profile.id,
        detail={
            "device": device,
            "model": model,
            "compute_type": compute_type,
            "temperatures": [float(value) for value in profile.temperatures],
            "before": before,
        },
    )
    return profile
