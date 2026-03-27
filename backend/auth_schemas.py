from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileResponse(BaseModel):
    id: int
    username: str
    location_consent: bool
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    target_role: str | None = None
    profile_summary: str | None = None


class UserPreferenceUpdate(BaseModel):
    location_consent: bool


class UserProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=80)
    target_role: str | None = Field(default=None, max_length=120)
    profile_summary: str | None = Field(default=None, max_length=2000)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class PasswordChangeResponse(BaseModel):
    success: bool = True
    message: str = "密码修改成功"
