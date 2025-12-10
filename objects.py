from pydantic import BaseModel, Field, validator, field_validator


class PhoneAddressCreate(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20, description="Номер телефона")
    address: str = Field(..., min_length=5, max_length=500, description="Адрес")

    @field_validator('phone', mode='before')
    def validate_phone(cls, v):
        """Валидация номера телефона - удаляем все нецифровые символы"""
        # Удаляем все, кроме цифр и +
        cleaned = ''.join(c for c in v if c.isdigit() or c == '+')

        if len(cleaned) < 10:  # ИЛИ 11? Проверьте какая у вас логика
            raise ValueError('Номер телефона должен содержать минимум 10 цифр')
            # ИЛИ: 'минимум 11 цифр с кодом страны'

        return cleaned


class AddressUpdate(BaseModel):
    address: str = Field(..., min_length=5, max_length=500, description="Новый адрес")
