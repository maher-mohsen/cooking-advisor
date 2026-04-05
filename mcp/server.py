from typing import Any, Dict, List, Optional
import os
import ollama
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
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

    if await contains_banned_operator(query):
        raise ValueError("Query contains banned operator ($where / $function)")

async def embed(text):
    return ollama.embed(
    model='bge-m3',
    input=text,
    dimensions=1024
).embeddings[0]
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
    Execute a safe MongoDB query on a specified collection.

    This tool allows only predefined safe operations and blocks
    dangerous operators like $where or $function. It supports
    find, count, and aggregate operations with optional filters,
    projections, sorting, limits, and aggregation pipelines.

    Args:
        collection (str): Name of the MongoDB collection to query.
        operation (str): Type of operation: 'find', 'count', or 'aggregate'.
        filter (Optional[Dict[str, Any]]): Query filter for find/count operations.
        projection (Optional[Dict[str, Any]]): Fields to include or exclude in results.
        sort (Optional[Dict[str, int]]): Sort specification, e.g. {"field": 1} for ascending.
        limit (int, optional): Maximum number of documents to return (for find). Defaults to 20.
        pipeline (Optional[List[Dict[str, Any]]]): Aggregation pipeline for 'aggregate' operations.

    Returns:
        Any: 
            - For 'find': a list of documents matching the filter.
            - For 'count': an integer count of matching documents.
            - For 'aggregate': a list of documents resulting from the aggregation pipeline.

    Raises:
        ValueError: If required arguments are missing, operation is not allowed, 
                    banned operators are detected, or the aggregate pipeline is missing.
    
    Notes:
        - Dangerous operators like $where and $function are automatically blocked.
        - Aggregation pipelines are required for 'aggregate' operation.
        - Limit is applied only to 'find' operation.
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

@mcp.tool()
async def mongo_fuzzy_search(query: str, limit: int = 10):
    """
    Perform a typo-tolerant fuzzy search on the recipes collection using MongoDB Atlas Search.

    This function searches the 'title', 'ingredients', and 'NER' fields of recipes
    using a fuzzy text search. It can handle minor typos and misspellings in the query.

    Args:
        query (str): The search string entered by the user. Can contain typos.
        limit (int, optional): Maximum number of results to return. Defaults to 10.

    Returns:
        List[Dict[str, Any]]: A list of recipe documents matching the query.
        Each document contains:
            - _id (str): The MongoDB ObjectId as a string.
            - title (str): Recipe title.
            - ingredients (List[str]): List of ingredients.
            - directions (List[str]): List of directions/steps.
            - NER (List[str]): Named entities or key terms extracted from ingredients.
            - link (str): Source link for the recipe.
            - source (str): Source name.
            - score (float): MongoDB Atlas Search relevance score.

    Notes:
        - Requires an Atlas Search index named 'recipe_text_index' covering
          'title', 'ingredients', and 'NER'.
        - The fuzzy search uses maxEdits=2 and prefixLength=1 for typo tolerance.
        - Embedding vectors are excluded from the returned documents for efficiency.
    """
    pipeline = [
        {
            "$search": {
                "index": "recipe_text_index",
                "compound": {
                    "should": [
                        {
                            "text": {
                                "query": query,
                                "path": "title",
                                "fuzzy": {"maxEdits": 2, "prefixLength": 1}
                            }
                        },
                        {
                            "text": {
                                "query": query,
                                "path": "NER",
                                "fuzzy": {"maxEdits": 2, "prefixLength": 1}
                            }
                        },
                        {
                            "text": {
                                "query": query,
                                "path": "ingredients",
                                "fuzzy": {"maxEdits": 2, "prefixLength": 1}
                            }
                        }
                    ]
                }
            }
        },
        {"$limit": limit},
        {
            "$project": {
    "title": 1,
    "ingredients": 1,
    "directions": 1,
    "NER": 1,
    "link": 1,
    "source": 1,
    "score": {"$meta": "searchScore"}
  }
        }
    ]

    results = list(collection.aggregate(pipeline))

    for doc in results:
        doc["_id"] = str(doc["_id"])

    return results

@mcp.tool()
async def mongo_vector_search(
    query: str,
    limit: int = 10,
    num_candidates: int = 3000
) -> List[Dict[str, Any]]:
    """
    Perform a vector similarity search on the recipes collection using MongoDB Atlas Vector Search.

    This tool finds the most similar recipes based on their embedding vectors.
    It uses a vector index (e.g., 'recipe_embedding_index') and cosine similarity by default.

    Args:
        query_vector (List[float]): The embedding vector of the query. Must match the dimension of stored embeddings.
        limit (int, optional): Maximum number of results to return. Defaults to 10.
        num_candidates (int, optional): Number of candidates to consider in the search for performance tuning. Defaults to 200.

    Returns:
        List[Dict[str, Any]]: A list of recipe documents sorted by similarity.
        Each document contains:
            - _id (str): MongoDB ObjectId as a string.
            - title (str): Recipe title.
            - ingredients (List[str]): List of ingredients.
            - directions (List[str]): Recipe steps.
            - NER (List[str]): Key terms from ingredients.
            - link (str): Recipe source link.
            - source (str): Source name.
            - score (float): Similarity score (higher = more similar).

    Notes:
        - Requires a vector index named 'recipe_embedding_index' on the 'embedding' field.
        - The query vector dimension must exactly match the stored embedding dimension.
        - Embedding vectors are excluded from the returned documents for efficiency.
    """
    query_vector = await embed(query)
    query_vector = [float(x) for x in query_vector]  # Ensure it's a list of floats
   
    if len(query_vector) != 1024:
        raise ValueError("Query vector dimension mismatch!")
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "queryVector": query_vector,
                "path": "embedding",
                "numCandidates": num_candidates,
                "limit": limit
            }
        },
        {
            "$project": {
                "title": 1,
                "ingredients": 1,
                "directions": 1,
                "NER": 1,
                "link": 1,
                "source": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]

    results = list(collection.aggregate(pipeline))

    # Convert ObjectId to string
    for doc in results:
        doc["_id"] = str(doc["_id"])
    
    return results
if __name__ == "__main__":
    mcp.run()