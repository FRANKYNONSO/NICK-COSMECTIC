from pydantic import BaseModel, EmailStr, Field


class CustomerRegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    phone: str | None = None


class CustomerLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
