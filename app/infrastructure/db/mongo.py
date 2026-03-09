import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.domain.entities import Macro, SavedServer, ServerGroup, SessionHistoryEntry

logger = logging.getLogger(__name__)


class MongoDatabase:
    def __init__(self, mongo_uri: str, db_name: str) -> None:
        self._mongo_uri = mongo_uri
        self._db_name = db_name
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None

    @property
    def db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            raise RuntimeError("MongoDatabase not connected")
        return self._db

    async def connect(self) -> None:
        self._client = AsyncIOMotorClient(self._mongo_uri)
        self._db = self._client[self._db_name]

        await self.db.ssh_sessions.create_index("user_id")
        await self.db.ssh_sessions.create_index([("user_id", 1), ("is_active", 1)])
        await self.db.ssh_sessions.create_index([("user_id", 1), ("session_name", 1)])

        await self.db.saved_servers.create_index("user_id")
        await self.db.saved_servers.create_index([("user_id", 1), ("name", 1)], unique=True)

        await self.db.macros.create_index("user_id")
        await self.db.macros.create_index([("user_id", 1), ("name", 1)], unique=True)

        await self.db.server_groups.create_index("user_id")
        await self.db.server_groups.create_index([("user_id", 1), ("name", 1)], unique=True)

        logger.info("Connected to MongoDB: %s", self._db_name)

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("MongoDB connection closed")

    # Session history repository
    async def save_session(
        self,
        user_id: int,
        host: str,
        port: int,
        username: str,
        session_name: str,
        auth_type: str,
        encrypted_password: str,
        encrypted_key: str,
    ) -> str:
        doc = {
            "user_id": user_id,
            "host": host,
            "port": port,
            "username": username,
            "session_name": session_name,
            "auth_type": auth_type,
            "encrypted_password": encrypted_password,
            "encrypted_key": encrypted_key,
            "created_at": datetime.now(timezone.utc),
            "is_active": True,
        }
        result = await self.db.ssh_sessions.insert_one(doc)
        return str(result.inserted_id)

    async def deactivate_sessions(self, user_id: int, session_name: str | None = None) -> None:
        query: dict = {"user_id": user_id, "is_active": True}
        if session_name:
            query["session_name"] = session_name
        await self.db.ssh_sessions.update_many(
            query,
            {"$set": {"is_active": False, "disconnected_at": datetime.now(timezone.utc)}},
        )

    async def get_recent_sessions(self, user_id: int, limit: int = 10) -> list[SessionHistoryEntry]:
        cursor = self.db.ssh_sessions.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [
            SessionHistoryEntry(
                user_id=doc["user_id"],
                host=doc["host"],
                port=doc["port"],
                username=doc["username"],
                session_name=doc.get("session_name", "default"),
                auth_type=doc.get("auth_type", "password"),
                is_active=doc.get("is_active", False),
                created_at=doc["created_at"],
                encrypted_password=doc.get("encrypted_password", ""),
                encrypted_key=doc.get("encrypted_key", ""),
            )
            for doc in docs
        ]

    async def get_active_session(self, user_id: int, session_name: str | None = None) -> SessionHistoryEntry | None:
        query: dict = {"user_id": user_id, "is_active": True}
        if session_name:
            query["session_name"] = session_name
        doc = await self.db.ssh_sessions.find_one(query, sort=[("created_at", -1)])
        if not doc:
            return None

        return SessionHistoryEntry(
            user_id=doc["user_id"],
            host=doc["host"],
            port=doc["port"],
            username=doc["username"],
            session_name=doc.get("session_name", "default"),
            auth_type=doc.get("auth_type", "password"),
            is_active=doc.get("is_active", False),
            created_at=doc["created_at"],
            encrypted_password=doc.get("encrypted_password", ""),
            encrypted_key=doc.get("encrypted_key", ""),
        )

    async def get_active_session_doc(self, user_id: int, session_name: str) -> dict | None:
        return await self.db.ssh_sessions.find_one(
            {"user_id": user_id, "is_active": True, "session_name": session_name},
            sort=[("created_at", -1)],
        )

    # Saved server repository
    async def upsert_server(self, server: SavedServer) -> None:
        await self.db.saved_servers.update_one(
            {"user_id": server.user_id, "name": server.name},
            {
                "$set": {
                    "host": server.host,
                    "port": server.port,
                    "username": server.username,
                    "auth_type": server.auth_type,
                    "encrypted_password": server.encrypted_password,
                    "encrypted_key": server.encrypted_key,
                    "group": server.group,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    async def get_server(self, user_id: int, name: str) -> SavedServer | None:
        doc = await self.db.saved_servers.find_one({"user_id": user_id, "name": name})
        if not doc:
            return None
        return SavedServer(
            user_id=doc["user_id"],
            name=doc["name"],
            host=doc["host"],
            port=doc["port"],
            username=doc["username"],
            auth_type=doc.get("auth_type", "password"),
            group=doc.get("group", ""),
            encrypted_password=doc.get("encrypted_password", ""),
            encrypted_key=doc.get("encrypted_key", ""),
        )

    async def list_servers(self, user_id: int) -> list[SavedServer]:
        docs = await self.db.saved_servers.find({"user_id": user_id}).sort("name", 1).to_list(length=200)
        return [
            SavedServer(
                user_id=d["user_id"],
                name=d["name"],
                host=d["host"],
                port=d["port"],
                username=d["username"],
                auth_type=d.get("auth_type", "password"),
                group=d.get("group", ""),
                encrypted_password=d.get("encrypted_password", ""),
                encrypted_key=d.get("encrypted_key", ""),
            )
            for d in docs
        ]

    async def delete_server(self, user_id: int, name: str) -> bool:
        result = await self.db.saved_servers.delete_one({"user_id": user_id, "name": name})
        return result.deleted_count > 0

    async def update_server_group(self, user_id: int, server_name: str, group: str) -> bool:
        result = await self.db.saved_servers.update_one(
            {"user_id": user_id, "name": server_name},
            {"$set": {"group": group}},
        )
        return result.modified_count > 0

    async def list_servers_by_group(self, user_id: int, group: str) -> list[SavedServer]:
        docs = await self.db.saved_servers.find({"user_id": user_id, "group": group}).sort("name", 1).to_list(length=200)
        return [
            SavedServer(
                user_id=d["user_id"],
                name=d["name"],
                host=d["host"],
                port=d["port"],
                username=d["username"],
                auth_type=d.get("auth_type", "password"),
                group=d.get("group", ""),
                encrypted_password=d.get("encrypted_password", ""),
                encrypted_key=d.get("encrypted_key", ""),
            )
            for d in docs
        ]

    # Macro repository
    async def upsert_macro(self, macro: Macro) -> None:
        await self.db.macros.update_one(
            {"user_id": macro.user_id, "name": macro.name},
            {
                "$set": {"command": macro.command, "updated_at": datetime.now(timezone.utc)},
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    async def get_macro(self, user_id: int, name: str) -> Macro | None:
        doc = await self.db.macros.find_one({"user_id": user_id, "name": name})
        if not doc:
            return None
        return Macro(user_id=doc["user_id"], name=doc["name"], command=doc["command"])

    async def list_macros(self, user_id: int) -> list[Macro]:
        docs = await self.db.macros.find({"user_id": user_id}).sort("name", 1).to_list(length=200)
        return [Macro(user_id=d["user_id"], name=d["name"], command=d["command"]) for d in docs]

    async def delete_macro(self, user_id: int, name: str) -> bool:
        result = await self.db.macros.delete_one({"user_id": user_id, "name": name})
        return result.deleted_count > 0

    # Group repository
    async def upsert_group(self, group: ServerGroup) -> None:
        await self.db.server_groups.update_one(
            {"user_id": group.user_id, "name": group.name},
            {"$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def list_groups(self, user_id: int) -> list[ServerGroup]:
        docs = await self.db.server_groups.find({"user_id": user_id}).sort("name", 1).to_list(length=200)
        return [ServerGroup(user_id=d["user_id"], name=d["name"]) for d in docs]

    async def delete_group(self, user_id: int, name: str) -> bool:
        await self.db.saved_servers.update_many(
            {"user_id": user_id, "group": name},
            {"$set": {"group": ""}},
        )
        result = await self.db.server_groups.delete_one({"user_id": user_id, "name": name})
        return result.deleted_count > 0
