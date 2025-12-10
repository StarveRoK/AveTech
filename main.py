import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status, Depends

from cst_logging import logger
from objects import PhoneAddressCreate, AddressUpdate
from redis_client import AsyncRedisManager

load_dotenv()



# События жизненного цикла приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    # Запуск - инициализация Redis
    redis_manager = AsyncRedisManager(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', 6379)),
        db=int(os.getenv('REDIS_DB', 0)),
        max_connections=50,
        min_connections=2
    )

    try:
        await redis_manager.initialize()
        app.state.redis = redis_manager
        logger.info("✅ Application started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start application: {e}")
        raise

    yield

    # Остановка - закрытие соединений
    await redis_manager.close()
    logger.info("✅ Application stopped")
# Создание FastAPI приложения
app = FastAPI(
    title="Phone-Address Microservice",
    description="Микросервис для управления связками 'телефон-адрес' с использованием Redis",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)


# Зависимость для получения Redis менеджера
async def get_redis() -> AsyncRedisManager:
    return app.state.redis


@app.get("/health")
async def health_check(redis: AsyncRedisManager = Depends(get_redis)):
    """
    Проверка здоровья сервиса и соединения с Redis
    """
    try:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection failed"
        )

        return {
            "status": "healthy",
            "service": "phone-address-microservice",
            "redis": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service unhealthy: {str(e)}"
        )


@app.get("/phones/{phone}")
async def get_address(
        phone: str,
        redis: AsyncRedisManager = Depends(get_redis)
):
    """
    ## 1. Просмотр данных - Получить адрес по номеру телефона

    **Цель**: Быстрая проверка наличия данных и извлечение адреса клиента.
    **Используется для**: кеширования часто запрашиваемой информации.

    **Бизнес-логика**:
    - Номер телефона нормализуется (удаляются все нецифровые символы кроме +)
    - Поиск в Redis по ключу = номер телефона
    - Возврат адреса в формате JSON

    **Ожидаемый ответ**:
    - Если телефон найден → 200 OK с адресом
    - Если не найден → 404 Not Found
    """
    # Нормализация номера телефона
    cleaned_phone = ''.join(c for c in phone if c.isdigit() or c == '+')

    # Получаем адрес из Redis
    address = await redis.get_data(cleaned_phone)

    if address is None:
        logger.warning(f"Phone not found: {cleaned_phone}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Address not found for phone: {cleaned_phone}"
        )

    logger.info(f"Retrieved address for phone: {cleaned_phone}")
    return {
        "phone": cleaned_phone,
        "address": address,
        "status": "success"
    }


@app.post("/phones", status_code=status.HTTP_201_CREATED)
async def create_record(
        data: PhoneAddressCreate,
        redis: AsyncRedisManager = Depends(get_redis)
):
    """
    ## 2. Создание новой записи

    **Цель**: Зарегистрировать новую связку телефон-адрес в системе.
    **Используется при**: первом обращении клиента или регистрации нового пользователя.

    **Тело запроса (JSON)**:
    ```json
    {
        "phone": "+79161234567",
        "address": "г. Москва, ул. Примерная, д. 1"
    }
    ```

    **Бизнес-логика**:
    - Проверка уникальности номера телефона
    - Сохранение в Redis с TTL (30 дней по умолчанию)
    - Номер телефона используется как ключ

    **Ожидаемое поведение**:
    - Если телефон уже существует → 409 Conflict
    - Если создано успешно → 201 Created
    """
    # Проверяем, существует ли уже запись
    exists = await redis.exists(data.phone)
    if exists:
        existing_address = await redis.get_data(data.phone)
        logger.warning(f"Phone already exists: {data.phone}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Phone {data.phone} already exists",
                "existing_address": existing_address
            }
        )

    # Создаем новую запись с TTL 30 дней
    success = await redis.set_data(
        key=data.phone,
        value=data.address,
        expire=30 * 24 * 60 * 60  # 30 дней в секундах
    )

    if not success:
        logger.error(f"Failed to create record for phone: {data.phone}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create record in Redis"
        )

    logger.info(f"Created record for phone: {data.phone}")
    return {
        "message": "Record created successfully",
        "phone": data.phone,
        "address": data.address,
        "status": "created",
        "ttl_days": 30
    }


@app.put("/phones/{phone}")
async def update_address(
        phone: str,
        data: AddressUpdate,
        redis: AsyncRedisManager = Depends(get_redis)
):
    """
    ## 3. Обновление существующей записи

    **Цель**: Актуализировать адрес клиента.
    **Используется когда**: клиент переехал или изменил адрес доставки.

    **Тело запроса (JSON)**:
    ```json
    {
        "address": "г. Санкт-Петербург, ул. Новая, д. 5"
    }
    ```

    **Бизнес-логика**:
    - Поиск существующей записи
    - Обновление значения адреса
    - Сброс TTL (продление срока жизни)

    **Ожидаемое поведение**:
    - Если телефон существует → 200 OK
    - Если телефон не найден → 404 Not Found
    """
    # Нормализация номера телефона
    cleaned_phone = ''.join(c for c in phone if c.isdigit() or c == '+')

    # Проверяем, существует ли запись
    exists = await redis.exists(cleaned_phone)
    if not exists:
        logger.warning(f"Cannot update - phone not found: {cleaned_phone}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Phone {cleaned_phone} not found"
        )

    # Обновляем запись с новым TTL
    success = await redis.set_data(
        key=cleaned_phone,
        value=data.address,
        expire=30 * 24 * 60 * 60  # Обновляем TTL
    )

    if not success:
        logger.error(f"Failed to update record for phone: {cleaned_phone}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update record in Redis"
        )

    logger.info(f"Updated address for phone: {cleaned_phone}")
    return {
        "message": "Address updated successfully",
        "phone": cleaned_phone,
        "address": data.address,
        "status": "updated"
    }


@app.delete("/phones/{phone}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
        phone: str,
        redis: AsyncRedisManager = Depends(get_redis)
):
    """
    ## 4. Удаление записи (опционально)

    **Цель**: Удалить устаревшие или ошибочные данные из системы.

    **Бизнес-логика**:
    - Проверка существования записи
    - Удаление из Redis по ключу

    **Ожидаемое поведение**:
    - Если запись существовала и удалена → 204 No Content
    - Если запись не найдена → 404 Not Found
    """
    # Нормализация номера телефона
    cleaned_phone = ''.join(c for c in phone if c.isdigit() or c == '+')

    # Проверяем существование перед удалением
    exists = await redis.exists(cleaned_phone)
    if not exists:
        logger.warning(f"Cannot delete - phone not found: {cleaned_phone}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Phone {cleaned_phone} not found"
        )

    # Удаляем запись
    success = await redis.delete_key(cleaned_phone)
    if not success:
        logger.error(f"Failed to delete record for phone: {cleaned_phone}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete record from Redis"
        )

    logger.info(f"Deleted record for phone: {cleaned_phone}")
    # Возвращаем 204 No Content без тела ответа


# Дополнительные эндпоинты для мониторинга и отладки

@app.get("/admin/records")
async def get_all_records(
        redis: AsyncRedisManager = Depends(get_redis),
        limit: int = 100
):
    """
    Получить все записи (для отладки и администрирования)
    """
    try:
        # Получаем все ключи
        all_keys = await redis.keys("*")

        # Ограничиваем количество
        keys_to_fetch = all_keys[:limit] if limit > 0 else all_keys

        # Получаем значения для каждого ключа
        records = {}
        for key in keys_to_fetch:
            value = await redis.get_data(key)
            if value:
                records[key] = value

        return {
            "total_records": len(all_keys),
            "displayed_records": len(records),
            "records": records
        }
    except Exception as e:
        logger.error(f"Error getting all records: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve records: {str(e)}"
        )


@app.get("/admin/stats")
async def get_stats(redis: AsyncRedisManager = Depends(get_redis)):
    """
    Получить статистику сервиса
    """
    try:
        all_keys = await redis.keys("*")

        return {
            "total_records": len(all_keys),
            "service": "phone-address-microservice",
            "redis_status": "connected",
            "sample_size": min(5, len(all_keys))
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve stats: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )