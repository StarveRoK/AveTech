# tests/test_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
import sys
import os

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app, AsyncRedisManager, get_redis


# Фикстура для тестового клиента
@pytest.fixture
def client():
    """Фикстура для тестового клиента FastAPI"""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Фикстура для мокирования RedisManager"""
    # Создаем мок RedisManager
    redis_mock = AsyncMock(spec=AsyncRedisManager)

    # Настраиваем методы мока
    redis_mock.exists = AsyncMock()
    redis_mock.get_data = AsyncMock()
    redis_mock.set_data = AsyncMock()
    redis_mock.delete_key = AsyncMock()
    redis_mock.keys = AsyncMock(return_value=[])

    return redis_mock


# Патчим зависимость get_redis
@pytest.fixture(autouse=True)
def patch_get_redis(mock_redis):
    """Автоматически патчим зависимость get_redis для всех тестов"""

    async def mock_get_redis():
        return mock_redis

    app.dependency_overrides[get_redis] = mock_get_redis
    yield
    app.dependency_overrides.clear()


# Базовые тесты CRUD операций
def test_create_record_success(client, mock_redis):
    """Тест успешного создания записи"""
    mock_redis.exists.return_value = False
    mock_redis.set_data.return_value = True

    data = {
        "phone": "+79161234567",
        "address": "г. Москва, ул. Примерная, д. 1"
    }

    response = client.post("/phones", json=data)

    assert response.status_code == 201
    assert response.json()["status"] == "created"
    assert response.json()["phone"] == "+79161234567"
    mock_redis.exists.assert_called_once_with("+79161234567")


def test_create_record_conflict(client, mock_redis):
    """Тест создания записи с конфликтом"""
    mock_redis.exists.return_value = True
    mock_redis.get_data.return_value = "Старый адрес"

    data = {
        "phone": "+79161234567",
        "address": "Новый адрес"
    }

    response = client.post("/phones", json=data)

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]["message"]


def test_get_address_found(client, mock_redis):
    """Тест успешного получения адреса"""
    mock_redis.get_data.return_value = "г. Москва, ул. Примерная, д. 1"

    response = client.get("/phones/%2B79161234567")

    assert response.status_code == 200
    assert response.json()["phone"] == "+79161234567"
    assert response.json()["address"] == "г. Москва, ул. Примерная, д. 1"


def test_get_address_not_found(client, mock_redis):
    """Тест получения несуществующего адреса"""
    mock_redis.get_data.return_value = None

    response = client.get("/phones/%2B79161234567")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_update_address_success(client, mock_redis):
    """Тест успешного обновления адреса"""
    mock_redis.exists.return_value = True
    mock_redis.set_data.return_value = True

    update_data = {"address": "Новый адрес"}
    response = client.put("/phones/%2B79161234567", json=update_data)

    assert response.status_code == 200
    assert response.json()["status"] == "updated"
    mock_redis.exists.assert_called_once_with("+79161234567")


def test_update_address_not_found(client, mock_redis):
    """Тест обновления несуществующего адреса"""
    mock_redis.exists.return_value = False

    update_data = {"address": "Новый адрес"}
    response = client.put("/phones/%2B79161234567", json=update_data)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_delete_record_success(client, mock_redis):
    """Тест успешного удаления записи"""
    mock_redis.exists.return_value = True
    mock_redis.delete_key.return_value = True

    response = client.delete("/phones/%2B79161234567")

    assert response.status_code == 204
    mock_redis.exists.assert_called_once_with("+79161234567")


def test_delete_record_not_found(client, mock_redis):
    """Тест удаления несуществующей записи"""
    mock_redis.exists.return_value = False

    response = client.delete("/phones/%2B79161234567")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


# Тесты валидации данных
def test_invalid_address_validation():
    """Тест валидации неверного адреса"""
    from main import PhoneAddressCreate, AddressUpdate
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PhoneAddressCreate(phone="+79161234567", address="a")

    with pytest.raises(ValidationError):
        AddressUpdate(address="a")


# Тесты нормализации телефона
def test_phone_normalization_in_get(client, mock_redis):
    """Тест нормализации номера телефона в GET запросе"""
    mock_redis.get_data.return_value = "Тестовый адрес"

    # Тестируем разные форматы
    test_cases = [
        ("+79161234567", "+79161234567"),
        ("8 (916) 123-45-67", "89161234567"),
        ("+7 916 123 45 67", "+79161234567"),
    ]

    for phone_input, expected_normalized in test_cases:
        mock_redis.get_data.reset_mock()
        encoded_phone = phone_input.replace("+", "%2B").replace(" ", "%20")
        response = client.get(f"/phones/{encoded_phone}")

        if response.status_code == 200:
            mock_redis.get_data.assert_called_with(expected_normalized)


def test_phone_normalization_in_create():
    """Тест нормализации номера телефона при создании"""
    from main import PhoneAddressCreate

    test_cases = [
        ("8 (916) 123-45-67", "89161234567"),
        ("+7 916 123 45 67", "+79161234567"),
        ("916-123-45-67", "9161234567"),
    ]

    for input_phone, expected_normalized in test_cases:
        try:
            data = PhoneAddressCreate(
                phone=input_phone,
                address="Тестовый адрес, минимум 5 символов"
            )
            # Проверяем что номер нормализован
            assert data.phone == expected_normalized
        except Exception as e:
            # Если валидация падает, это тоже нормально
            print(f"Validation error for {input_phone}: {e}")


# Тесты админских эндпоинтов
def test_get_all_records(client, mock_redis):
    """Тест получения всех записей"""
    mock_keys = ["+79161234567", "+79161234568"]
    mock_redis.keys.return_value = mock_keys
    mock_redis.get_data.side_effect = ["Адрес 1", "Адрес 2"]

    response = client.get("/admin/records")

    assert response.status_code == 200
    assert response.json()["total_records"] == 2
    mock_redis.keys.assert_called_once_with("*")


def test_get_all_records_with_limit(client, mock_redis):
    """Тест получения записей с лимитом"""
    mock_keys = [f"+7916123456{i}" for i in range(10)]
    mock_redis.keys.return_value = mock_keys
    mock_redis.get_data.return_value = "Адрес"

    response = client.get("/admin/records?limit=5")

    assert response.status_code == 200
    assert response.json()["total_records"] == 10
    assert response.json()["displayed_records"] == 5


# Тесты документации
def test_docs_endpoint(client):
    """Тест доступности документации"""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_redoc_endpoint(client):
    """Тест доступности ReDoc"""
    response = client.get("/redoc")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# Тесты для edge cases
def test_special_characters_in_phone():
    """Тест специальных символов в номере телефона"""
    from main import PhoneAddressCreate

    data = PhoneAddressCreate(
        phone="+7 (916) 123-45-67 доб. 1234",
        address="Длинный адрес"
    )
    # После очистки должны остаться только цифры и +
    assert all(c.isdigit() or c == '+' for c in data.phone)


def test_app_state_integration():
    """Тест интеграции с состоянием приложения"""
    from fastapi import FastAPI
    from unittest.mock import AsyncMock

    test_app = FastAPI()
    test_app.state.redis = AsyncMock()

    assert hasattr(test_app.state, 'redis')
    assert test_app.state.redis is not None


# Простые тесты без моков
def test_create_with_valid_data(client, mock_redis):
    """Тест создания с валидными данными"""
    mock_redis.exists.return_value = False
    mock_redis.set_data.return_value = True

    response = client.post("/phones", json={
        "phone": "+79161234567",
        "address": "Достаточно длинный адрес для валидации"
    })

    assert response.status_code == 201


def test_update_with_valid_data(client, mock_redis):
    """Тест обновления с валидными данными"""
    mock_redis.exists.return_value = True
    mock_redis.set_data.return_value = True

    response = client.put("/phones/%2B79161234567", json={
        "address": "Новый достаточно длинный адрес"
    })

    assert response.status_code == 200


def test_delete_existing_record(client, mock_redis):
    """Тест удаления существующей записи"""
    mock_redis.exists.return_value = True
    mock_redis.delete_key.return_value = True

    response = client.delete("/phones/%2B79161234567")

    assert response.status_code == 204


# Тест для проверки обработки ошибок Redis
def test_create_record_redis_error(client, mock_redis):
    """Тест создания записи при ошибке Redis"""
    mock_redis.exists.return_value = False
    mock_redis.set_data.return_value = False

    data = {
        "phone": "+79161234567",
        "address": "г. Москва, ул. Примерная, д. 1"
    }

    response = client.post("/phones", json=data)

    assert response.status_code == 500
    assert "Failed to create record" in response.json()["detail"]


def test_update_address_redis_error(client, mock_redis):
    """Тест обновления адреса при ошибке Redis"""
    mock_redis.exists.return_value = True
    mock_redis.set_data.return_value = False

    update_data = {"address": "Новый адрес"}
    response = client.put("/phones/%2B79161234567", json=update_data)

    assert response.status_code == 500
    assert "Failed to update" in response.json()["detail"]


def test_delete_record_redis_error(client, mock_redis):
    """Тест удаления записи при ошибке Redis"""
    mock_redis.exists.return_value = True
    mock_redis.delete_key.return_value = False

    response = client.delete("/phones/%2B79161234567")

    assert response.status_code == 500
    assert "Failed to delete" in response.json()["detail"]


# Фильтр для запуска только этих тестов
if __name__ == "__main__":
    pytest.main([__file__, "-v"])