from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)


def canonical_date(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("date must be canonical YYYY-MM-DD")
    return value


def canonical_timestamp(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("timestamp must be an RFC3339 string")
    return value


Day = Annotated[date, BeforeValidator(canonical_date)]
Timestamp = Annotated[datetime, BeforeValidator(canonical_timestamp)]

Money = Annotated[StrictInt, Field(ge=1, le=999_999_999)]
Bank = Annotated[StrictInt, Field(ge=-9_000_000_000_000, le=9_000_000_000_000)]
Revision = Annotated[StrictInt, Field(ge=0)]
Name = Annotated[str, Field(min_length=1, max_length=40)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppleLogin(StrictModel):
    challenge_id: UUID
    id_token: str
    authorization_code: str
    display_name: Name | None = None


class RefreshRequest(StrictModel):
    refresh_token: str
    refresh_request_id: UUID


class ProfilePatch(StrictModel):
    display_name: Name


class DeleteAccount(StrictModel):
    command_id: UUID
    confirm_shared_history_changes: Annotated[StrictBool, Field(json_schema_extra={"const": True})]

    @model_validator(mode="after")
    def explicit_confirmation(self) -> "DeleteAccount":
        if not self.confirm_shared_history_changes:
            raise ValueError("account deletion requires explicit confirmation")
        return self


class CreateCabinet(StrictModel):
    command_id: UUID
    name: Name
    timezone: Literal["Asia/Shanghai"]


class InvitationCode(StrictModel):
    code: Annotated[str, Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{10}$")]


class AcceptInvitation(InvitationCode):
    command_id: UUID


class CreateSprint(StrictModel):
    sprint_id: UUID
    initial_minor: Money
    end_date: Day
    expected_cabinet_revision: Revision
    replaces_sprint_id: UUID | None = None
    expected_business_date: Day | None = None

    @model_validator(mode="after")
    def replacement_has_date(self) -> "CreateSprint":
        if self.replaces_sprint_id is not None and self.expected_business_date is None:
            raise ValueError("replacing a sprint requires the previewed business date")
        return self


class AddEntry(StrictModel):
    entry_id: UUID
    sprint_id: UUID
    kind: Literal["expense", "topup"]
    amount_minor: Money
    occurred_at: Timestamp
    allow_completed: StrictBool
    membership_id: UUID
    recovery_of_command_id: UUID | None = None
    original_occurred_at: Timestamp | None = None

    @model_validator(mode="after")
    def aware_time(self) -> "AddEntry":
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include timezone")
        if self.original_occurred_at and (
            self.original_occurred_at.tzinfo is None or not self.recovery_of_command_id
        ):
            raise ValueError("date recovery requires timezone and rejected command reference")
        return self


class CorrectEntry(StrictModel):
    entry_id: UUID
    sprint_id: UUID
    kind: Literal["expense", "topup"]
    amount_minor: Money
    expected_entry_revision: Annotated[StrictInt, Field(ge=1)]
    allow_completed: StrictBool


class VoidEntry(StrictModel):
    entry_id: UUID
    sprint_id: UUID
    expected_entry_revision: Annotated[StrictInt, Field(ge=1)]
    allow_completed: StrictBool


class Carry(StrictModel):
    sprint_id: UUID
    day: Day
    mode: Literal["today", "distribute"]
    expected_cabinet_revision: Revision


class UpdateSprint(StrictModel):
    sprint_id: UUID
    expected_cabinet_revision: Revision
    expected_business_date: Day
    bank_minor: Bank | None = None
    end_date: Day | None = None

    @model_validator(mode="before")
    @classmethod
    def no_explicit_nulls(cls, value):
        if isinstance(value, dict) and any(
            key in value and value[key] is None for key in ("bank_minor", "end_date")
        ):
            raise ValueError("dirty fields cannot be null")
        return value

    @model_validator(mode="after")
    def dirty_fields(self) -> "UpdateSprint":
        if self.bank_minor is None and self.end_date is None:
            raise ValueError("at least one dirty field is required")
        return self


class Empty(StrictModel):
    pass


class RevokeInvitation(StrictModel):
    invitation_id: UUID


class Rename(StrictModel):
    name: Name
    expected_cabinet_revision: Revision


class Leave(StrictModel):
    expected_cabinet_revision: Revision


PAYLOADS: dict[str, type[StrictModel]] = {
    "create_sprint": CreateSprint,
    "add_entry": AddEntry,
    "correct_entry": CorrectEntry,
    "void_entry": VoidEntry,
    "choose_carry": Carry,
    "update_sprint": UpdateSprint,
    "create_invitation": Empty,
    "revoke_invitation": RevokeInvitation,
    "rename_cabinet": Rename,
    "leave_cabinet": Leave,
}


class Command(StrictModel):
    command_id: UUID
    type: str
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> "Command":
        if self.type not in PAYLOADS:
            raise ValueError("unknown command type")
        self.payload = (
            PAYLOADS[self.type]
            .model_validate(self.payload)
            .model_dump(mode="json", exclude_none=True)
        )
        return self


class SyncRequest(StrictModel):
    unresolved_command_ids: Annotated[list[UUID], Field(max_length=1000)]

    @model_validator(mode="after")
    def unique_ids(self) -> "SyncRequest":
        if len(set(self.unresolved_command_ids)) != len(self.unresolved_command_ids):
            raise ValueError("command ids must be unique")
        return self


class DeviceRegistration(StrictModel):
    device_id: UUID
    app_apns_token: str | None
    widget_push_token: str | None
    apns_environment: Literal["sandbox", "production"]
    app_version: str
    os_version: str


class WidgetTokenRequest(StrictModel):
    cabinet_id: UUID
