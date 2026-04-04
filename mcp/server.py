from typing import Any, Dict, List, Optional
import os

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
import httpx
from mcp.server.fastmcp import FastMCP

# Intialize Monogo DB Client
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(
    os.getenv("MONGO_URI"),
    tls=True,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=30000
)

db = client['cooking']
collection = db['recipes']

if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI in environment variables")

ALLOWED_OPERATIONS = {"find", "aggregate", "count"}
BANNED_OPERATORS = {"$where", "$function"}

# Intialize FastMCP server
mcp = FastMCP('cooking-advisor')

# Helpers
async def contains_banned_operator(obj: Any) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in BANNED_OPERATORS:
                return True
            if await contains_banned_operator(v):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if await contains_banned_operator(item):
                return True
    return False

async def validate_query_spec(query: Dict[str, Any]) -> None:
    if "collection" not in query:
        raise ValueError("Missing 'collection'")
    
    if "operation" not in query:
        raise ValueError("Missing 'operation'")
    
    if query['operation'] not in ALLOWED_OPERATIONS:
        raise ValueError(f"Operation not allowed: {query['operation']}")

    if contains_banned_operator(query):
        raise ValueError("Query contains banned operator ($where / $function)")
    
@mcp.tool()
async def mongo_query(
    collection: str,
    operation: str,
    filter: Optional[Dict[str, Any]] = None,
    projection: Optional[Dict[str, Any]] = None,
    sort: Optional[Dict[str, int]] = None,
    limit: int = 20,
    pipeline: Optional[List[Dict[str, Any]]] = None,
    
) -> Any:
    """
    Execute SAFE MonogDB query.
    Allowed operations: find, aggergate, count
    """

    query_spec = {
        "collection": collection,
        "operation": operation,
        "filter": filter,
        "projection": projection,
        "sort": sort,
        "limit": limit,
        "pipeeliine": pipeline,
    }

    await validate_query_spec(query=query_spec)

    col = db[collection]

    if operation == "find":
        cursor = col.find(filter or {}, projection or {})
        if sort:
            cursor = cursor.sort(list(sort.items()))
        cursor = cursor.limit(limit)
        return list(cursor)
    
    if operation == "count":
        return col.count_documents(filter or {})
    
    if operation == "aggregate":
        if not pipeline:
            raise ValueError("Missing pipeline for aggregate")
        return list(col.aggregate(pipeline))
    
    raise ValueError("Unsupported operation")


if __name__ == "__main__":
    mcp.run()