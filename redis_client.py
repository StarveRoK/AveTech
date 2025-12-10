import json
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from cst_logging import logger


class AsyncRedisManager:
    def __init__(
            self,
            host: str = 'localhost',
            port: int = 6379,
            db: int = 0,
            max_connections: int = 50,
            min_connections: int = 2,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.max_connections = max_connections
        self.min_connections = min_connections
        self.pool: Optional[ConnectionPool] = None
        self.client: Optional[aioredis.Redis] = None

    async def initialize(self):
        """Инициализация пула соединений и клиента"""
        try:
            # Создаем пул соединений
            self.pool = ConnectionPool(
                host=self.host,
                port=self.port,
                db=self.db,
                max_connections=self.max_connections,
                decode_responses=True
            )

            # Создаем клиент с пулом
            self.client = aioredis.Redis(connection_pool=self.pool)

            # Проверяем подключение
            logger.info("✅ Async Redis manager initialized successfully")

        except Exception as e:
            logger.error(f"❌ Failed to initialize Redis: {e}")
            raise


    async def close(self):
        """Закрытие соединений"""
        try:
            if self.client:
                await self.client.close()
            if self.pool:
                await self.pool.disconnect()
            logger.info("✅ Redis connections closed")
        except Exception as e:
            logger.error(f"❌ Error closing connections: {e}")

    async def set_data(self, key: str, value: str, expire: Optional[int] = 300) -> bool:
        """Асинхронная установка значения"""
        try:
            if not self.client:
                raise RuntimeError("Redis client not initialized")

            if expire:
                await self.client.setex(key, expire, value)
            else:
                await self.client.set(key, value)
            return True

        except Exception as e:
            logger.error(f"❌ Error setting data: {e}")
            return False

    async def get_data(self, key: str) -> Optional[str]:
        """Асинхронное получение значения"""
        try:
            if not self.client:
                raise RuntimeError("Redis client not initialized")

            return await self.client.get(key)

        except Exception as e:
            logger.error(f"❌ Error getting data: {e}")
            return None

    async def set_json(self, key: str, data: Any, expire: Optional[int] = 300) -> bool:
        """Сохранение JSON данных"""
        try:
            json_data = json.dumps(data, ensure_ascii=False)
            return await self.set_data(key, json_data, expire)
        except Exception as e:
            logger.error(f"❌ Error setting JSON: {e}")
            return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Получение JSON данных"""
        try:
            data = await self.get_data(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"❌ Error getting JSON: {e}")
            return None

    async def delete_key(self, key: str) -> bool:
        """Удаление ключа"""
        try:
            if not self.client:
                raise RuntimeError("Redis client not initialized")

            result = await self.client.delete(key)
            return result > 0

        except Exception as e:
            logger.error(f"❌ Error deleting key: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Проверка существования ключа"""
        try:
            if not self.client:
                raise RuntimeError("Redis client not initialized")

            result = await self.client.exists(key)
            return result > 0
        except Exception as e:
            logger.error(f"❌ Error checking if key exists: {e}")
            return False

    async def keys(self, pattern: str = "*") -> list:
        """Получение всех ключей по шаблону"""
        try:
            if not self.client:
                raise RuntimeError("Redis client not initialized")

            return await self.client.keys(pattern)
        except Exception as e:
            logger.error(f"❌ Error getting keys: {e}")
            return []
